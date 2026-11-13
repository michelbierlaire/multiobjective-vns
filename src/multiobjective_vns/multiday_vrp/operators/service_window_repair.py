"""Destroy and rebuild service activities with high time-window penalties.

Created: 2026-11-04T09:43:07+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cmp_to_key

from multiobjective_vns.identifiers import ObjectiveId, identifier_text
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
    randomized_order,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import objective_position
from multiobjective_vns.multiday_vrp.operators.path_search import (
    PathSearchIndex,
    build_path_search_index,
    rebuild_changed_routes,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = ['TimeWindowFocusedDestroyRepairOperator']

type _Position = tuple[int, int]


@dataclass(frozen=True, slots=True)
class _Target:
    """One service occurrence selected for removal and reinsertion."""

    reference: ActivityRef
    stop: ActivityStop
    original_position: _Position
    guidance: float | None


class TimeWindowFocusedDestroyRepairOperator:
    """Reinsert the ``k`` most window-penalized served activity occurrences.

    Each trial removes the selected occurrences together, reinserts them in
    descending guidance order at uniformly selected eligible route positions,
    rebuilds affected route paths, and assesses the complete plan.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_id',
        '_objective_position',
        '_maximum_size',
        '_candidate_limit',
        '_path_limit',
        '_expansion_limit',
        '_windowed_activity_keys',
        '_package_compatibility',
        '_intervention_window_days',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        objective_id: ObjectiveId,
        max_neighborhood_size: int,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._objective_id = objective_id
        self._objective_position = objective_position(
            objective_declaration, objective_id
        )
        self._maximum_size = require_integer(
            max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )
        self._candidate_limit = _bounded_limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _bounded_limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _bounded_limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )
        self._windowed_activity_keys = _windowed_activity_keys(instance)
        self._package_compatibility = {
            (item.vehicle_id, item.package_id): item.compatible
            for item in instance.package_data.vehicle_package_compatibility
        }
        intervention_window_days: dict[str, set[int]] = {}
        for item in instance.intervention_data.intervention_time_windows:
            intervention_window_days.setdefault(item.intervention_id, set()).add(
                item.day_index
            )
        self._intervention_window_days = {
            intervention_id: frozenset(days)
            for intervention_id, days in intervention_window_days.items()
        }

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply one bounded removal-and-reinsertion move of up to ``k`` stops."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        analysis = self._base_analysis(context)
        guidance_by_position = _maximum_guidance_by_position(
            base, analysis, identifier_text(self._objective_id)
        )
        targets = _select_targets(
            base,
            self._windowed_activity_keys,
            guidance_by_position,
            size,
            context,
        )
        if not targets:
            return operator_result(None, modifications=0, requested_size=size)

        target_positions = {target.original_position for target in targets}
        base_stops = [
            [
                stop
                for stop_index, stop in enumerate(route.stops)
                if (route_index, stop_index) not in target_positions
            ]
            for route_index, route in enumerate(base.routes)
        ]

        candidates: list[tuple[VrpSolution, tuple[object, ...], int]] = []
        seen: set[VrpSolution] = set()
        path_search_index: PathSearchIndex | None = None
        for _ in range(self._candidate_limit):
            trial_stops = [list(stops) for stops in base_stops]
            complete = True
            for target in targets:
                positions = _insertion_positions(
                    base,
                    trial_stops,
                    target.stop,
                    self._package_compatibility,
                    self._intervention_window_days.get(
                        target.reference.activity_id, frozenset()
                    ),
                )
                if not positions:
                    complete = False
                    break
                route_index, stop_index = choose_uniformly(
                    positions, context.random_source
                )
                trial_stops[route_index].insert(stop_index, target.stop)
            if not complete:
                continue

            changed_stops = {
                route_index: tuple(stops)
                for route_index, stops in enumerate(trial_stops)
                if tuple(stops) != base.routes[route_index].stops
            }
            if not changed_stops:
                continue
            if path_search_index is None:
                path_search_index = build_path_search_index(self._instance)
            rebuilt = rebuild_changed_routes(
                self._instance,
                base,
                changed_stops,
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=1,
                path_search_index=path_search_index,
            )
            for candidate in rebuilt:
                if candidate in seen:
                    continue
                seen.add(candidate)
                modifications = _moved_target_count(base, candidate, targets)
                if modifications == 0:
                    continue
                evaluation = _assessment(
                    self._assess_plan, self._instance, self._objectives, candidate
                )
                if not evaluation.representation_valid:
                    continue
                objective_values = _objective_values(
                    evaluation, len(self._objectives.objectives)
                )
                if context.ensure_feasible and evaluation.feasible is not True:
                    continue
                candidates.append((candidate, objective_values, modifications))

        if not candidates:
            return operator_result(None, modifications=0, requested_size=size)
        ranked = self._rank_candidates(candidates, context)
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), modifications)
                for solution, _values, modifications in ranked[: context.max_candidates]
            )
        )

    def _base_analysis(self, context: CandidateContext) -> VrpOperatorAnalysis | None:
        record = context.base_evaluation
        evaluation = None if record is None else record.application_evaluation
        if (
            not isinstance(evaluation, ApplicationEvaluation)
            or not evaluation.representation_valid
            or not isinstance(evaluation.analysis, VrpOperatorAnalysis)
            or not evaluation.analysis.is_complete
        ):
            return None
        return evaluation.analysis

    def _rank_candidates(
        self,
        candidates: list[tuple[VrpSolution, tuple[object, ...], int]],
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...], int], ...]:
        objective = self._objectives.objectives[self._objective_position]

        def compare(
            left: tuple[VrpSolution, tuple[object, ...], int],
            right: tuple[VrpSolution, tuple[object, ...], int],
        ) -> int:
            comparison = objective.compare(
                left[1][self._objective_position],
                right[1][self._objective_position],
            )
            if comparison is ObjectiveComparison.LESS:
                return -1
            if comparison is ObjectiveComparison.GREATER:
                return 1
            return 0

        ordered = sorted(candidates, key=cmp_to_key(compare))
        ranked: list[tuple[VrpSolution, tuple[object, ...], int]] = []
        group_start = 0
        while group_start < len(ordered):
            group_end = group_start + 1
            while (
                group_end < len(ordered)
                and compare(ordered[group_start], ordered[group_end]) == 0
            ):
                group_end += 1
            ranked.extend(
                randomized_order(ordered[group_start:group_end], context.random_source)
            )
            group_start = group_end
        return tuple(ranked)


def _select_targets(
    solution: VrpSolution,
    windowed_activity_keys: frozenset[tuple[str, str, int | None]],
    guidance_by_position: Mapping[_Position, float],
    maximum_count: int,
    context: CandidateContext,
) -> tuple[_Target, ...]:
    targets: list[_Target] = []
    for route_index, route in enumerate(solution.routes):
        for stop_index, stop in enumerate(route.stops):
            if type(stop) is not ActivityStop:
                continue
            reference = stop.activity_ref
            window_key = (
                (reference.kind, reference.activity_id, None)
                if reference.kind in {'pickup', 'delivery'}
                else (reference.kind, reference.activity_id, route.day_index)
            )
            if window_key not in windowed_activity_keys:
                continue
            guidance = guidance_by_position.get((route_index, stop_index))
            targets.append(
                _Target(reference, stop, (route_index, stop_index), guidance)
            )
    if not targets:
        return ()

    positive = tuple(
        item for item in targets if item.guidance is not None and item.guidance > 0.0
    )
    if not positive:
        return randomized_order(targets, context.random_source)[:maximum_count]

    selected: list[_Target] = []
    remaining_positive = list(positive)
    while remaining_positive and len(selected) < maximum_count:
        best_score = max(
            item.guidance for item in remaining_positive if item.guidance is not None
        )
        tied = tuple(item for item in remaining_positive if item.guidance == best_score)
        ordered_ties = randomized_order(tied, context.random_source)
        for item in ordered_ties:
            selected.append(item)
            remaining_positive.remove(item)
            if len(selected) == maximum_count:
                break

    if len(selected) < maximum_count:
        remaining = tuple(item for item in targets if item not in selected)
        selected.extend(
            randomized_order(remaining, context.random_source)[
                : maximum_count - len(selected)
            ]
        )
    return tuple(selected)


def _windowed_activity_keys(
    instance: LoadedVrpInstance,
) -> frozenset[tuple[str, str, int | None]]:
    return frozenset(
        (item.activity, item.package_id, None)
        for item in instance.package_data.package_time_windows
    ) | frozenset(
        ('intervention', item.intervention_id, item.day_index)
        for item in instance.intervention_data.intervention_time_windows
    )


def _maximum_guidance_by_position(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    objective_id: str,
) -> dict[_Position, float]:
    if analysis is None or not analysis.scenario_evaluations:
        return {}
    guidance_by_position: dict[_Position, float] = {}
    for scenario in analysis.scenario_evaluations:
        traces_by_route = {
            (item.vehicle_id, item.day_index): item for item in scenario.vehicle_days
        }
        for route_index, route in enumerate(solution.routes):
            vehicle_day = traces_by_route.get((route.vehicle_id, route.day_index))
            if vehicle_day is None:
                continue
            for stop_trace in vehicle_day.stops:
                stop_index = stop_trace.stop_index
                if stop_index >= len(route.stops):
                    continue
                route_stop = route.stops[stop_index]
                if (
                    type(route_stop) is not ActivityStop
                    or stop_trace.activity_ref != route_stop.activity_ref
                ):
                    continue
                assessment = next(
                    (
                        item
                        for item in stop_trace.window_assessments_by_objective
                        if item.objective_id == objective_id
                    ),
                    None,
                )
                if assessment is None:
                    continue
                key = (route_index, stop_index)
                guidance_by_position[key] = max(
                    guidance_by_position.get(key, 0.0), assessment.guidance_value
                )
    return guidance_by_position


def _insertion_positions(
    solution: VrpSolution,
    stops_by_route: list[list[RouteStop]],
    stop: ActivityStop,
    package_compatibility: Mapping[tuple[str, str], bool],
    intervention_days: frozenset[int],
) -> tuple[_Position, ...]:
    reference = stop.activity_ref
    return tuple(
        (route_index, stop_index)
        for route_index, route in enumerate(solution.routes)
        if (
            reference.kind not in {'pickup', 'delivery'}
            or package_compatibility.get(
                (route.vehicle_id, reference.activity_id), True
            )
        )
        and (
            reference.kind != 'intervention'
            or not intervention_days
            or route.day_index in intervention_days
        )
        for stop_index in range(len(stops_by_route[route_index]) + 1)
    )


def _moved_target_count(
    base: VrpSolution, candidate: VrpSolution, targets: tuple[_Target, ...]
) -> int:
    positions = {
        stop.activity_ref: (route_index, stop_index)
        for route_index, route in enumerate(candidate.routes)
        for stop_index, stop in enumerate(route.stops)
        if type(stop) is ActivityStop
    }
    return sum(
        positions.get(target.reference) != target.original_position
        for target in targets
    )


def _assessment(
    assess_plan: VrpPlanAssessor,
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    solution: VrpSolution,
) -> ApplicationEvaluation:
    evaluation = assess_plan(instance, objectives, solution)
    if not isinstance(evaluation, ApplicationEvaluation):
        raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
    return evaluation


def _objective_values(
    evaluation: ApplicationEvaluation, objective_count: int
) -> tuple[object, ...]:
    values = evaluation.objective_values
    if type(values) is not tuple:
        raise TypeError(
            'representation-valid VRP evaluations require an objective tuple'
        )
    if len(values) != objective_count:
        raise ValueError('objective vector length must match the declaration')
    return values


def _bounded_limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
