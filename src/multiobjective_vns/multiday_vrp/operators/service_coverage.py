"""Optional package service-coverage change Operator.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
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
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
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
        """Toggle up to ``k`` package activity coverage decisions."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        if self._instance.settings.service_coverage_mode != 'penalized_omission':
            return operator_result(None, modifications=0, requested_size=size)
        working = base
        considered: set[tuple[str, str]] = set()
        modifications = 0
        for _ in range(size):
            choices = tuple(
                choice
                for choice in _coverage_choices(self._instance, working)
                if (choice[0].kind, choice[0].activity_id) not in considered
            )
            if not choices:
                break
            choice = choose_uniformly(choices, context.random_source)
            ref, present = choice
            considered.add((ref.kind, ref.activity_id))
            candidates = self._change_candidates(working, choice, context)
            if not candidates:
                continue
            working = choose_uniformly(candidates, context.random_source)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _change_candidates(
        self,
        solution: VrpSolution,
        choice: _CoverageChoice,
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
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
                transfer_steps=transfer_steps,
            )

        if ref.kind not in {'pickup', 'delivery'}:
            return ()
        package = next(
            (
                item
                for item in self._instance.package_data.packages
                if item.package_id == ref.activity_id
            ),
            None,
        )
        if package is None:
            return ()
        compatibility = {
            (item.vehicle_id, item.package_id): item.compatible
            for item in self._instance.package_data.vehicle_package_compatibility
        }
        routes = tuple(
            index
            for index, route in enumerate(solution.routes)
            if compatibility.get((route.vehicle_id, ref.activity_id), True)
        )
        if not routes:
            return ()
        targets = tuple(
            (route_index, insertion_index)
            for route_index in routes
            for insertion_index in range(len(solution.routes[route_index].stops) + 1)
        )
        sampled = randomized_sample(
            targets, self._candidate_limit, context.random_source
        )
        candidates: list[VrpSolution] = []
        for route_index, insertion_index in sampled:
            mutable_stops = list(solution.routes[route_index].stops)
            mutable_stops.insert(
                insertion_index,
                ActivityStop(ref, frozenset(), 0.0, 0.0, 0.0, False),
            )
            candidates.extend(
                self._rebuild(solution, {route_index: tuple(mutable_stops)}, context)
            )
        return tuple(candidates)

    def _rebuild(
        self,
        solution: VrpSolution,
        stops_by_route: dict[int, tuple[RouteStop, ...]],
        context: CandidateContext,
        *,
        transfer_steps: tuple[TransferStep, ...] | None = None,
    ) -> tuple[VrpSolution, ...]:
        candidates = rebuild_changed_routes(
            self._instance,
            solution,
            stops_by_route,
            random_source=context.random_source,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_candidate_alternatives=self._candidate_limit,
        )
        eligible = []
        for candidate in candidates:
            if transfer_steps is not None:
                candidate = replace(candidate, transfer_steps=transfer_steps)
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
        return tuple(eligible)


def _coverage_choices(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> tuple[_CoverageChoice, ...]:
    result: list[_CoverageChoice] = []
    for package in instance.package_data.packages:
        for kind in ('pickup', 'delivery'):
            ref = ActivityRef(kind, package.package_id)
            count = sum(
                type(stop) is ActivityStop and stop.activity_ref == ref
                for route in solution.routes
                for stop in route.stops
            )
            if count <= 1:
                result.append((ref, count == 1))
    return tuple(result)


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
