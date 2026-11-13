"""Optional package service-coverage change Operator.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    operator_result,
    randomized_order,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.path_search import rebuild_changed_routes
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteStop,
    TransferOperationStop,
    TransferStep,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = ['ServiceCoverageChangeOperator']

type _CoverageChoice = tuple[ActivityRef, bool]


class ServiceCoverageChangeOperator:
    """Remove or insert a package pickup or delivery activity occurrence."""

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_maximum_size',
        '_candidate_limit',
        '_path_limit',
        '_expansion_limit',
        '_packages_by_id',
        '_compatibility',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        max_neighborhood_size: int,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._packages_by_id = {
            package.package_id: package for package in instance.package_data.packages
        }
        self._compatibility = {
            (item.vehicle_id, item.package_id): item.compatible
            for item in instance.package_data.vehicle_package_compatibility
        }
        self._maximum_size = require_integer(
            max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )
        self._candidate_limit = _limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return independent single-decision service-coverage alternatives."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        if self._instance.settings.service_coverage_mode != 'penalized_omission':
            return operator_result(None, modifications=0, requested_size=size)
        # Each proposal is one independent coverage toggle from the same base;
        # therefore its modification count is one and never exceeds k.
        proposals: list[CandidateProposal] = []
        seen: set[VrpSolution] = {base}
        for choice in randomized_order(
            _coverage_choices(self._instance, base), context.random_source
        ):
            if len(proposals) >= context.max_candidates:
                break
            candidates, _assessed_count = self._change_candidates(
                base,
                choice,
                context,
                max_total_candidates=self._candidate_limit,
                seen=seen,
            )
            ref, was_present = choice
            for candidate in candidates:
                if _activity_occurrence_count(candidate, ref) != (0 if was_present else 1):
                    continue
                proposals.append(CandidateProposal(Candidate(candidate), 1))
                if len(proposals) >= context.max_candidates:
                    break
        return OperatorResult(candidates=tuple(proposals))

    def _change_candidates(
        self,
        solution: VrpSolution,
        choice: _CoverageChoice,
        context: CandidateContext,
        *,
        max_total_candidates: int,
        seen: set[VrpSolution],
    ) -> tuple[tuple[VrpSolution, ...], int]:
        ref, present = choice
        if present:
            stops_by_route: dict[int, tuple[RouteStop, ...]] = {}
            for route_index, route in enumerate(solution.routes):
                stops = tuple(
                    stop
                    for stop in route.stops
                    if not (type(stop) is ActivityStop and stop.activity_ref == ref)
                    and not (
                        ref.kind == 'pickup'
                        and type(stop) is ActivityStop
                        and stop.activity_ref
                        == ActivityRef('delivery', ref.activity_id)
                    )
                    and not (
                        ref.kind == 'pickup'
                        and type(stop) is TransferOperationStop
                        and stop.transfer_step_ref.package_id == ref.activity_id
                    )
                )
                if stops != route.stops:
                    stops_by_route[route_index] = stops
            transfer_steps = (
                tuple(
                    item
                    for item in solution.transfer_steps
                    if item.package_id != ref.activity_id
                )
                if ref.kind == 'pickup'
                else solution.transfer_steps
            )
            return self._rebuild(
                solution,
                stops_by_route,
                context,
                max_candidate_alternatives=max_total_candidates,
                seen=seen,
                transfer_steps=transfer_steps,
            )

        if ref.kind not in {'pickup', 'delivery'}:
            return (), 0
        package = self._packages_by_id.get(ref.activity_id)
        if package is None:
            return (), 0
        routes = tuple(
            index
            for index, route in enumerate(solution.routes)
            if self._compatibility.get((route.vehicle_id, ref.activity_id), True)
        )
        if not routes:
            return (), 0
        targets = tuple(
            (route_index, insertion_index)
            for route_index in routes
            for insertion_index in range(len(solution.routes[route_index].stops) + 1)
        )
        sampled = randomized_sample(
            targets,
            min(self._candidate_limit, max_total_candidates),
            context.random_source,
        )
        candidates: list[VrpSolution] = []
        assessed_count = 0
        for route_index, insertion_index in sampled:
            remaining = max_total_candidates - assessed_count
            if remaining <= 0:
                break
            mutable_stops = list(solution.routes[route_index].stops)
            mutable_stops.insert(
                insertion_index,
                ActivityStop(ref, frozenset(), 0.0, 0.0, 0.0, False),
            )
            rebuilt, newly_assessed = self._rebuild(
                solution,
                {route_index: tuple(mutable_stops)},
                context,
                max_candidate_alternatives=remaining,
                seen=seen,
            )
            candidates.extend(rebuilt)
            assessed_count += newly_assessed
        return tuple(candidates), assessed_count

    def _rebuild(
        self,
        solution: VrpSolution,
        stops_by_route: dict[int, tuple[RouteStop, ...]],
        context: CandidateContext,
        *,
        max_candidate_alternatives: int,
        seen: set[VrpSolution],
        transfer_steps: tuple[TransferStep, ...] | None = None,
    ) -> tuple[tuple[VrpSolution, ...], int]:
        rebuilt = rebuild_changed_routes(
            self._instance,
            solution,
            stops_by_route,
            random_source=context.random_source,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_candidate_alternatives=max_candidate_alternatives,
        )
        eligible = []
        assessed_count = 0
        for candidate in rebuilt:
            if transfer_steps is not None:
                candidate = replace(candidate, transfer_steps=transfer_steps)
            if candidate in seen:
                continue
            seen.add(candidate)
            assessed_count += 1
            evaluation = self._assess_plan(self._instance, self._objectives, candidate)
            if not isinstance(evaluation, ApplicationEvaluation):
                raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
            if (
                not evaluation.representation_valid
                or evaluation.objective_values is None
            ):
                continue
            if type(evaluation.objective_values) is not tuple:
                raise TypeError('objective_values must be a tuple')
            if len(evaluation.objective_values) != len(self._objectives.objectives):
                raise ValueError('objective vector length must match the declaration')
            if context.ensure_feasible and evaluation.feasible is not True:
                continue
            eligible.append(candidate)
        return tuple(eligible), assessed_count


def _coverage_choices(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> tuple[_CoverageChoice, ...]:
    occurrence_counts: dict[ActivityRef, int] = {}
    for route in solution.routes:
        for stop in route.stops:
            if type(stop) is ActivityStop and stop.activity_ref.kind in {
                'pickup',
                'delivery',
            }:
                ref = stop.activity_ref
                occurrence_counts[ref] = occurrence_counts.get(ref, 0) + 1
    result: list[_CoverageChoice] = []
    for package in instance.package_data.packages:
        for kind in ('pickup', 'delivery'):
            ref = ActivityRef(kind, package.package_id)
            count = occurrence_counts.get(ref, 0)
            if count <= 1:
                result.append((ref, count == 1))
    return tuple(result)


def _activity_occurrence_count(solution: VrpSolution, ref: ActivityRef) -> int:
    return sum(
        type(stop) is ActivityStop and stop.activity_ref == ref
        for route in solution.routes
        for stop in route.stops
    )


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
