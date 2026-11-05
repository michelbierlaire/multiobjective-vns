"""Feature-guided activity relocation for multi-day VRP plans.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from typing import Literal

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import objective_position
from multiobjective_vns.multiday_vrp.operators.path_search import rebuild_changed_routes
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    DepotOnlyStop,
    RouteStop,
    TransferOperationStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

type ActivityRelocationMode = Literal[
    'intervention',
    'vehicle_availability',
    'window_guided',
    'service_order_impact',
    'objective_targeted',
]
type _ActivityPosition = tuple[int, int]

__all__ = ['ActivityRelocationMode', 'ActivityRelocationOperator']


class ActivityRelocationOperator:
    """Move one activity stop while keeping its application fields intact.

    A registration fixes one ``mode`` and, optionally, an objective. Package
    precedence, route capacities, relationships, qualifications, path
    connectivity, and the requested feasibility guarantee are screened by
    complete plan assessment.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_mode',
        '_objective_position',
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
        mode: ActivityRelocationMode,
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        if mode not in {
            'intervention',
            'vehicle_availability',
            'window_guided',
            'service_order_impact',
            'objective_targeted',
        }:
            raise ValueError(f'unsupported activity relocation mode: {mode!r}')
        if (
            mode in {'window_guided', 'service_order_impact', 'objective_targeted'}
            and objective_id is None
        ):
            raise ValueError(f'{mode} mode requires objective_id')
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._mode = mode
        self._objective_position = (
            None
            if objective_id is None
            else objective_position(objective_declaration, objective_id)
        )
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
        """Apply up to ``k`` distinct activity relocations sequentially."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        moved_occurrences: set[str] = set()
        modifications = 0
        for _ in range(size):
            targets = tuple(
                target
                for target in _eligible_occurrences(
                    self._instance,
                    working,
                    _analysis(
                        self._assess_plan(self._instance, self._objectives, working)
                    ),
                    self._mode,
                    self._objective_position,
                )
                if _occurrence_key(working, target) not in moved_occurrences
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            occurrence_key = _occurrence_key(working, target)
            candidate_plans = self._relocation_candidates(working, target, context)
            if not candidate_plans:
                moved_occurrences.add(occurrence_key)
                continue
            working = self._choose(candidate_plans, context)
            moved_occurrences.add(occurrence_key)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _relocation_candidates(
        self,
        working: VrpSolution,
        target: _ActivityPosition,
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
        source_route_index, source_stop_index = target
        selected_stop = working.routes[source_route_index].stops[source_stop_index]
        positions = _insertion_positions(working, target)
        sampled = randomized_sample(
            positions, self._candidate_limit, context.random_source
        )
        candidates: list[tuple[VrpSolution, tuple[object, ...]]] = []
        for destination_route_index, insertion_index in sampled:
            stops_by_route: dict[int, tuple[RouteStop, ...]] = {}
            source_stops = list(working.routes[source_route_index].stops)
            del source_stops[source_stop_index]
            if destination_route_index == source_route_index:
                if insertion_index > source_stop_index:
                    insertion_index -= 1
                source_stops.insert(insertion_index, selected_stop)
                stops_by_route[source_route_index] = tuple(source_stops)
            else:
                destination_stops = list(working.routes[destination_route_index].stops)
                destination_stops.insert(insertion_index, selected_stop)
                stops_by_route[source_route_index] = tuple(source_stops)
                stops_by_route[destination_route_index] = tuple(destination_stops)
            rebuilt = rebuild_changed_routes(
                self._instance,
                working,
                stops_by_route,
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=self._candidate_limit,
            )
            for candidate in rebuilt:
                if candidate == working:
                    continue
                evaluation = self._assess_plan(
                    self._instance, self._objectives, candidate
                )
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
                    raise ValueError(
                        'objective vector length must match the declaration'
                    )
                if context.ensure_feasible and evaluation.feasible is not True:
                    continue
                candidates.append((candidate, evaluation.objective_values))
        return tuple(candidates)

    def _choose(
        self,
        candidates: tuple[tuple[VrpSolution, tuple[object, ...]], ...],
        context: CandidateContext,
    ) -> VrpSolution:
        if self._objective_position is None:
            return choose_uniformly(candidates, context.random_source)[0]
        objective = self._objectives.objectives[self._objective_position]
        best = candidates[0]
        tied = [best]
        for candidate in candidates[1:]:
            comparison = objective.compare(
                candidate[1][self._objective_position],
                best[1][self._objective_position],
            )
            if comparison is ObjectiveComparison.LESS:
                best = candidate
                tied = [candidate]
            elif comparison is ObjectiveComparison.EQUAL:
                tied.append(candidate)
        return choose_uniformly(tied, context.random_source)[0]


def _eligible_occurrences(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    mode: ActivityRelocationMode,
    objective_index: int | None,
) -> tuple[_ActivityPosition, ...]:
    occurrences = tuple(
        (route_index, stop_index)
        for route_index, route in enumerate(solution.routes)
        for stop_index, stop in enumerate(route.stops)
        if (
            mode == 'vehicle_availability'
            or (
                type(stop) is ActivityStop
                and (mode != 'intervention' or stop.activity_ref.kind == 'intervention')
            )
        )
    )
    if mode == 'service_order_impact':
        durations = _positive_duration_positions(instance, solution, occurrences)
        if durations and analysis is not None and objective_index is not None:
            objective_id = str(
                instance.objective_data.objectives[objective_index].objective_id
            )
            scores = _downstream_window_scores(
                solution, analysis, objective_id, durations
            )
            if scores and max(score for score, _position in scores) > 0.0:
                best = max(score for score, _position in scores)
                return tuple(position for score, position in scores if score == best)
        return durations or occurrences
    if mode == 'vehicle_availability' and analysis is not None:
        availability_objective_id = (
            None
            if objective_index is None
            else str(instance.objective_data.objectives[objective_index].objective_id)
        )
        route_scores: dict[tuple[str, int], float] = {}
        for finding in analysis.resource_findings:
            if finding.kind != 'vehicle_availability' or finding.vehicle_id is None:
                continue
            if finding.day_index is None:
                continue
            score = finding.measure
            if availability_objective_id is not None:
                score = next(
                    (
                        value
                        for candidate_objective, value in finding.penalty_by_objective
                        if candidate_objective == availability_objective_id
                    ),
                    0.0,
                )
            key = (finding.vehicle_id, finding.day_index)
            route_scores[key] = route_scores.get(key, 0.0) + score
        if route_scores and max(route_scores.values()) > 0.0:
            selected_routes = {
                key
                for key, score in route_scores.items()
                if score == max(route_scores.values())
            }
            targeted = tuple(
                position
                for position in occurrences
                if (
                    solution.routes[position[0]].vehicle_id,
                    solution.routes[position[0]].day_index,
                )
                in selected_routes
            )
            if targeted:
                return targeted
    if mode == 'window_guided' and analysis is not None and objective_index is not None:
        objective_id = str(
            instance.objective_data.objectives[objective_index].objective_id
        )
        scored = _window_guidance(solution, analysis, objective_id, occurrences)
        if scored:
            maximum = max(value for value, _position in scored)
            return tuple(position for value, position in scored if value == maximum)
    if mode == 'window_guided':
        return tuple(
            position
            for position in occurrences
            if _has_service_window(instance, solution, position)
        )
    return occurrences


def _window_guidance(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    objective_id: str,
    occurrences: tuple[_ActivityPosition, ...],
) -> tuple[tuple[float, _ActivityPosition], ...]:
    occurrence_set = set(occurrences)
    values: dict[_ActivityPosition, float] = {}
    for scenario in analysis.scenario_evaluations:
        for route_index, route in enumerate(solution.routes):
            trace = next(
                (
                    item
                    for item in scenario.vehicle_days
                    if item.vehicle_id == route.vehicle_id
                    and item.day_index == route.day_index
                ),
                None,
            )
            if trace is None:
                continue
            for stop in trace.stops:
                key = (route_index, stop.stop_index)
                if key not in occurrence_set:
                    continue
                guidance = next(
                    (
                        item.guidance_value
                        for item in stop.window_assessments_by_objective
                        if item.objective_id == objective_id
                    ),
                    0.0,
                )
                values[key] = max(values.get(key, 0.0), guidance)
    return tuple((value, key) for key, value in values.items() if value > 0.0)


def _positive_duration_positions(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    occurrences: tuple[_ActivityPosition, ...],
) -> tuple[_ActivityPosition, ...]:
    package_map = {item.package_id: item for item in instance.package_data.packages}
    intervention_map = {
        item.intervention_id: item for item in instance.intervention_data.interventions
    }
    result: list[_ActivityPosition] = []
    for route_index, stop_index in occurrences:
        stop = solution.routes[route_index].stops[stop_index]
        if type(stop) is not ActivityStop:
            continue
        ref = stop.activity_ref
        if ref.kind in {'pickup', 'delivery'}:
            package = package_map.get(ref.activity_id)
            if (
                package is not None
                and (
                    package.pickup_service_duration
                    if ref.kind == 'pickup'
                    else package.delivery_service_duration
                )
                > 0.0
            ):
                result.append((route_index, stop_index))
        elif ref.kind == 'intervention':
            intervention = intervention_map.get(ref.activity_id)
            if intervention is not None and intervention.service_duration > 0.0:
                result.append((route_index, stop_index))
    return tuple(result)


def _has_service_window(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    position: _ActivityPosition,
) -> bool:
    route_index, stop_index = position
    stop = solution.routes[route_index].stops[stop_index]
    if type(stop) is not ActivityStop:
        return False
    ref = stop.activity_ref
    if ref.kind in {'pickup', 'delivery'}:
        return any(
            item.package_id == ref.activity_id and item.activity == ref.kind
            for item in instance.package_data.package_time_windows
        )
    route_day = solution.routes[route_index].day_index
    return any(
        item.intervention_id == ref.activity_id and item.day_index == route_day
        for item in instance.intervention_data.intervention_time_windows
    )


def _insertion_positions(
    solution: VrpSolution, target: _ActivityPosition
) -> tuple[_ActivityPosition, ...]:
    source_route_index, source_stop_index = target
    return tuple(
        (route_index, insertion_index)
        for route_index, route in enumerate(solution.routes)
        for insertion_index in range(len(route.stops) + 1)
        if not (
            route_index == source_route_index
            and insertion_index in (source_stop_index, source_stop_index + 1)
        )
    )


def _occurrence_key(solution: VrpSolution, target: _ActivityPosition) -> str:
    route_index, stop_index = target
    stop = solution.routes[route_index].stops[stop_index]
    if type(stop) is ActivityStop:
        return f'activity:{stop.activity_ref.kind}:{stop.activity_ref.activity_id}'
    if type(stop) is TransferOperationStop:
        ref = stop.transfer_step_ref
        return f'transfer:{ref.package_id}:{ref.step_number}:{stop.role}'
    if type(stop) is DepotOnlyStop:
        return (
            f'depot:{stop.node_id}:{stop.wait}:{stop.break_duration}:'
            f'{stop.tour_end}:{route_index}:{stop_index}'
        )
    raise TypeError('route stop has an unsupported type')


def _downstream_window_scores(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    objective_id: str,
    positions: tuple[_ActivityPosition, ...],
) -> tuple[tuple[float, _ActivityPosition], ...]:
    scores = {position: 0.0 for position in positions}
    for scenario in analysis.scenario_evaluations:
        for route_index, route in enumerate(solution.routes):
            trace = next(
                (
                    item
                    for item in scenario.vehicle_days
                    if item.vehicle_id == route.vehicle_id
                    and item.day_index == route.day_index
                ),
                None,
            )
            if trace is None:
                continue
            for target_route, target_stop in positions:
                if target_route != route_index:
                    continue
                downstream = sum(
                    next(
                        (
                            window.guidance_value
                            for window in stop.window_assessments_by_objective
                            if window.objective_id == objective_id
                        ),
                        0.0,
                    )
                    for stop in trace.stops
                    if stop.stop_index > target_stop
                )
                scores[(target_route, target_stop)] += scenario.probability * downstream
    return tuple((score, position) for position, score in scores.items())


def _analysis(evaluation: ApplicationEvaluation) -> VrpOperatorAnalysis | None:
    if isinstance(evaluation.analysis, VrpOperatorAnalysis):
        return evaluation.analysis
    return None


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
