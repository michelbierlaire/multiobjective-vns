"""Feature-guided timing adjustment Operators for the multi-day VRP.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    StopScenarioEvaluation,
    VrpScenarioEvaluation,
)
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
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    DepotOnlyStop,
    RouteStop,
    TransferOperationStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import finite_real, require_integer

type FeatureTimingMode = Literal[
    'departure_profile',
    'scenario_differential',
    'vehicle_availability',
    'personnel_availability',
    'service_window_wait',
    'route_start_window',
    'positive_service_duration',
    'personnel_calendar',
    'work_rule',
]
type _TimingTarget = tuple[int, int | None, str]

__all__ = ['FeatureTimingMode', 'FeatureTimingOperator']

_VALID_MODES = {
    'departure_profile',
    'scenario_differential',
    'vehicle_availability',
    'personnel_availability',
    'service_window_wait',
    'route_start_window',
    'positive_service_duration',
    'personnel_calendar',
    'work_rule',
}
_STOP_TYPES = (ActivityStop, TransferOperationStop, DepotOnlyStop)
_WAIT_FIELDS = ('pre_service_wait', 'wait')


class FeatureTimingOperator:
    """Adjust timing decisions selected by one active instance feature.

    The same implementation serves the compatible Operator modes named in
    ``FeatureTimingMode``. A catalog registration fixes one mode and its
    objective, time step, and maximum size.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_mode',
        '_objective_position',
        '_time_step',
        '_maximum_size',
        '_candidate_limit',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        mode: FeatureTimingMode,
        objective_id: ObjectiveId,
        time_step: float,
        max_neighborhood_size: int,
        max_candidate_alternatives: int = 32,
    ) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f'unsupported feature timing mode: {mode!r}')
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._mode = mode
        self._objective_position = objective_position(
            objective_declaration, objective_id
        )
        self._time_step = finite_real(time_step, name='time_step')
        if self._time_step <= 0.0:
            raise ValueError('time_step must be positive')
        self._maximum_size = require_integer(
            max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )
        self._candidate_limit = _limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Change up to ``k`` distinct timing fields and rank full plans."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        modified_targets: set[_TimingTarget] = set()
        modifications = 0
        for _ in range(size):
            evaluation = self._assess_plan(self._instance, self._objectives, working)
            if not isinstance(evaluation, ApplicationEvaluation):
                raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
            analysis = _analysis(evaluation)
            targets = tuple(
                target
                for target in _targets(
                    self._instance,
                    working,
                    analysis,
                    self._mode,
                    str(
                        self._objectives.objectives[
                            self._objective_position
                        ].objective_id
                    ),
                )
                if target not in modified_targets
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            alternatives = _candidate_values(
                self._instance,
                working,
                analysis,
                target,
                self._mode,
                self._time_step,
                context.random_source,
            )
            candidates: list[tuple[VrpSolution, object]] = []
            for value in randomized_sample(
                alternatives, self._candidate_limit, context.random_source
            ):
                candidate = _set_timing_value(working, target, value)
                if candidate is None or candidate == working:
                    continue
                result = self._assess_plan(self._instance, self._objectives, candidate)
                if not isinstance(result, ApplicationEvaluation):
                    raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
                if not result.representation_valid or result.objective_values is None:
                    continue
                if type(result.objective_values) is not tuple:
                    raise TypeError('objective_values must be a tuple')
                if len(result.objective_values) != len(self._objectives.objectives):
                    raise ValueError(
                        'objective vector length must match the declaration'
                    )
                if context.ensure_feasible and result.feasible is not True:
                    continue
                candidates.append(
                    (candidate, result.objective_values[self._objective_position])
                )
            if not candidates:
                modified_targets.add(target)
                continue
            working = _choose_best(
                candidates,
                self._objectives,
                self._objective_position,
                context.random_source,
            )
            modified_targets.add(target)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )


def _analysis(evaluation: ApplicationEvaluation) -> VrpOperatorAnalysis | None:
    if isinstance(evaluation.analysis, VrpOperatorAnalysis):
        return evaluation.analysis
    return None


def _targets(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    mode: FeatureTimingMode,
    objective_id: str,
) -> tuple[_TimingTarget, ...]:
    all_targets = tuple(
        target
        for route_index, route in enumerate(solution.routes)
        for target in (
            (route_index, None, 'start_time'),
            *(
                (route_index, stop_index, field)
                for stop_index, stop in enumerate(route.stops)
                if type(stop) in _STOP_TYPES
                for field in _fields_for_stop(stop)
            ),
        )
    )
    if mode == 'departure_profile':
        return all_targets
    if mode == 'scenario_differential':
        if analysis is None or len(analysis.scenario_evaluations) < 2:
            return ()
        variable_routes = _scenario_variable_routes(
            solution, analysis.scenario_evaluations
        )
        return tuple(target for target in all_targets if target[0] in variable_routes)
    if mode in {'vehicle_availability', 'personnel_availability', 'personnel_calendar'}:
        kind = (
            'vehicle_availability'
            if mode == 'vehicle_availability'
            else 'personnel_availability'
        )
        route_indices = _finding_routes(analysis, kind, objective_id, solution)
        if not route_indices:
            return all_targets
        return tuple(target for target in all_targets if target[0] in route_indices)
    if mode == 'work_rule':
        route_indices = _work_rule_routes(analysis, objective_id, solution)
        return (
            tuple(target for target in all_targets if target[0] in route_indices)
            or all_targets
        )
    if mode in {'service_window_wait', 'route_start_window'}:
        window_targets = _window_targets(
            solution, analysis, objective_id, mode == 'service_window_wait'
        )
        if window_targets:
            return window_targets
        if mode == 'service_window_wait':
            return tuple(
                (route_index, stop_index, 'pre_service_wait')
                for route_index, route in enumerate(solution.routes)
                for stop_index, stop in enumerate(route.stops)
                if type(stop) is ActivityStop
                and _activity_has_window(instance, stop, route.day_index)
            )
        return tuple(
            (route_index, None, 'start_time')
            for route_index, route in enumerate(solution.routes)
            if any(
                type(stop) is ActivityStop
                and _activity_has_window(instance, stop, route.day_index)
                for stop in route.stops
            )
        )
    if mode == 'positive_service_duration':
        activity_by_id = {
            item.package_id: item for item in instance.package_data.packages
        }
        intervention_by_id = {
            item.intervention_id: item
            for item in instance.intervention_data.interventions
        }
        eligible: set[_TimingTarget] = set()
        for route_index, route in enumerate(solution.routes):
            for stop_index, stop in enumerate(route.stops):
                if type(stop) is not ActivityStop:
                    continue
                ref = stop.activity_ref
                duration = 0.0
                if ref.kind in {'pickup', 'delivery'}:
                    package = activity_by_id.get(ref.activity_id)
                    if package is not None:
                        duration = (
                            package.pickup_service_duration
                            if ref.kind == 'pickup'
                            else package.delivery_service_duration
                        )
                elif ref.kind == 'intervention':
                    intervention = intervention_by_id.get(ref.activity_id)
                    if intervention is not None:
                        duration = intervention.service_duration
                if duration > 0.0:
                    eligible.add((route_index, None, 'start_time'))
                    eligible.update(
                        target
                        for target in all_targets
                        if target[0] == route_index
                        and target[1] is not None
                        and target[1] < stop_index
                    )
        return tuple(target for target in all_targets if target in eligible)
    return all_targets


def _fields_for_stop(stop: RouteStop) -> tuple[str, ...]:
    if type(stop) is DepotOnlyStop:
        return ('wait', 'break_duration')
    return _WAIT_FIELDS


def _scenario_variable_routes(
    solution: VrpSolution,
    scenarios: tuple[VrpScenarioEvaluation, ...],
) -> set[int]:
    variable: set[int] = set()
    for route_index, route in enumerate(solution.routes):
        scenario_values: list[tuple[float, ...]] = []
        for scenario in scenarios:
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
                scenario_values = []
                break
            scenario_values.append(
                tuple(
                    sum(arc.travel_time for arc in leg.arc_traversals)
                    for leg in trace.legs
                )
            )
        if scenario_values and any(
            max(values) > min(values) for values in zip(*scenario_values, strict=True)
        ):
            variable.add(route_index)
    return variable


def _finding_routes(
    analysis: VrpOperatorAnalysis | None,
    kind: str,
    objective_id: str,
    solution: VrpSolution,
) -> set[int]:
    if analysis is None:
        return set()
    findings = [
        finding
        for finding in analysis.resource_findings
        if finding.kind == kind
        and any(
            key == objective_id and value > 0.0
            for key, value in finding.penalty_by_objective
        )
    ]
    keys = {
        (finding.vehicle_id, finding.day_index)
        for finding in findings
        if finding.vehicle_id is not None
    }
    return {
        index
        for index, route in enumerate(solution.routes)
        if (route.vehicle_id, route.day_index) in keys
    }


def _work_rule_routes(
    analysis: VrpOperatorAnalysis | None,
    objective_id: str,
    solution: VrpSolution,
) -> set[int]:
    if analysis is None:
        return set()
    relevant = {'driver_work', 'driving_time', 'break', 'rest', 'hos'}
    findings = tuple(
        finding
        for finding in analysis.resource_findings
        if finding.kind in relevant
        and any(
            key == objective_id and value > 0.0
            for key, value in finding.penalty_by_objective
        )
        and finding.vehicle_id is not None
    )
    keys = {
        (finding.vehicle_id, finding.day_index)
        for finding in findings
        if finding.vehicle_id is not None
    }
    return {
        index
        for index, route in enumerate(solution.routes)
        if (route.vehicle_id, route.day_index) in keys
    }


def _window_targets(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    objective_id: str,
    wait_only: bool,
) -> tuple[_TimingTarget, ...]:
    if analysis is None:
        return ()
    scored: list[tuple[float, _TimingTarget]] = []
    for scenario in analysis.scenario_evaluations:
        for route_index, route in enumerate(solution.routes):
            vehicle_day = next(
                (
                    item
                    for item in scenario.vehicle_days
                    if item.vehicle_id == route.vehicle_id
                    and item.day_index == route.day_index
                ),
                None,
            )
            if vehicle_day is None:
                continue
            for stop_index, stop_trace in enumerate(vehicle_day.stops):
                guidance = next(
                    (
                        item.guidance_value
                        for item in stop_trace.window_assessments_by_objective
                        if item.objective_id == objective_id
                    ),
                    0.0,
                )
                if guidance <= 0.0 or stop_index >= len(route.stops):
                    continue
                if wait_only:
                    if type(route.stops[stop_index]) is ActivityStop:
                        scored.append(
                            (guidance, (route_index, stop_index, 'pre_service_wait'))
                        )
                else:
                    scored.append((guidance, (route_index, None, 'start_time')))
    if not scored:
        return ()
    maximum = max(score for score, _target in scored)
    return tuple(target for score, target in scored if score == maximum)


def _candidate_values(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    target: _TimingTarget,
    mode: FeatureTimingMode,
    time_step: float,
    random_source: RandomSource,
) -> tuple[float, ...]:
    route_index, stop_index, field = target
    route = solution.routes[route_index]
    current = (
        route.start_time
        if stop_index is None
        else getattr(route.stops[stop_index], field)
    )
    values = [current - time_step, current + time_step]
    if mode == 'service_window_wait' and stop_index is not None:
        stop = route.stops[stop_index]
        if type(stop) is ActivityStop:
            for trace in _stop_traces(solution, analysis, route_index, stop_index):
                arrival = trace.arrival_time
                for lower, upper, duration in _activity_windows(
                    instance, stop, route.day_index
                ):
                    values.extend(
                        (
                            max(0.0, lower - arrival),
                            max(0.0, upper - duration - arrival),
                        )
                    )
    if mode == 'route_start_window' and stop_index is None and analysis is not None:
        for trace in _target_stop_traces(solution, analysis, route_index):
            if trace.service_start is None or trace.service_finish is None:
                continue
            stop = route.stops[trace.stop_index]
            if type(stop) is not ActivityStop:
                continue
            for lower, upper, _duration in _activity_windows(
                instance, stop, route.day_index
            ):
                values.extend(
                    (
                        current + lower - trace.service_start,
                        current + upper - trace.service_finish,
                    )
                )
    return tuple(
        dict.fromkeys(value for value in values if value >= 0.0 and value != current)
    )


def _stop_traces(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    route_index: int,
    stop_index: int,
) -> tuple[StopScenarioEvaluation, ...]:
    if analysis is None:
        return ()
    route = solution.routes[route_index]
    found: list[StopScenarioEvaluation] = []
    for scenario in analysis.scenario_evaluations:
        vehicle_day = next(
            (
                item
                for item in scenario.vehicle_days
                if item.vehicle_id == route.vehicle_id
                and item.day_index == route.day_index
            ),
            None,
        )
        if vehicle_day is not None and stop_index < len(vehicle_day.stops):
            found.append(vehicle_day.stops[stop_index])
    return tuple(found)


def _target_stop_traces(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    route_index: int,
) -> tuple[StopScenarioEvaluation, ...]:
    route = solution.routes[route_index]
    found: list[StopScenarioEvaluation] = []
    for scenario in analysis.scenario_evaluations:
        vehicle_day = next(
            (
                item
                for item in scenario.vehicle_days
                if item.vehicle_id == route.vehicle_id
                and item.day_index == route.day_index
            ),
            None,
        )
        if vehicle_day is not None:
            found.extend(vehicle_day.stops)
    return tuple(found)


def _activity_windows(
    instance: LoadedVrpInstance, stop: ActivityStop, day_index: int
) -> tuple[tuple[float, float, float], ...]:
    ref = stop.activity_ref
    if ref.kind in {'pickup', 'delivery'}:
        package = next(
            (
                item
                for item in instance.package_data.packages
                if item.package_id == ref.activity_id
            ),
            None,
        )
        if package is None:
            return ()
        duration = (
            package.pickup_service_duration
            if ref.kind == 'pickup'
            else package.delivery_service_duration
        )
        windows = tuple(
            (item.start_time, item.end_time, duration)
            for item in instance.package_data.package_time_windows
            if item.package_id == ref.activity_id and item.activity == ref.kind
        )
        return windows
    intervention = next(
        (
            item
            for item in instance.intervention_data.interventions
            if item.intervention_id == ref.activity_id
        ),
        None,
    )
    if intervention is None:
        return ()
    return tuple(
        (item.start_time, item.end_time, intervention.service_duration)
        for item in instance.intervention_data.intervention_time_windows
        if item.intervention_id == ref.activity_id and item.day_index == day_index
    )


def _activity_has_window(
    instance: LoadedVrpInstance, stop: ActivityStop, day_index: int
) -> bool:
    return bool(_activity_windows(instance, stop, day_index))


def _set_timing_value(
    solution: VrpSolution, target: _TimingTarget, value: float
) -> VrpSolution | None:
    route_index, stop_index, field = target
    routes = list(solution.routes)
    route = routes[route_index]
    if stop_index is None:
        routes[route_index] = replace(route, start_time=value)
    else:
        stops = list(route.stops)
        stop = stops[stop_index]
        updated = _replace_stop_timing(stop, field, value)
        if updated is None:
            return None
        if updated.break_duration > updated.wait:
            return None
        stops[stop_index] = updated
        routes[route_index] = replace(route, stops=tuple(stops))
    return replace(solution, routes=tuple(routes))


def _replace_stop_timing(stop: RouteStop, field: str, value: float) -> RouteStop | None:
    if field == 'pre_service_wait':
        if type(stop) is ActivityStop:
            return replace(stop, pre_service_wait=value)
        if type(stop) is TransferOperationStop:
            return replace(stop, pre_service_wait=value)
        return None
    if field == 'wait':
        return replace(stop, wait=value)
    if field == 'break_duration':
        return replace(stop, break_duration=value)
    raise ValueError(f'unsupported timing field {field!r}')


def _choose_best(
    candidates: list[tuple[VrpSolution, object]],
    objectives: ObjectiveDeclaration,
    position: int,
    random_source: RandomSource,
) -> VrpSolution:
    objective = objectives.objectives[position]
    best_value = candidates[0][1]
    tied = [candidates[0]]
    for candidate in candidates[1:]:
        comparison = objective.compare(candidate[1], best_value)
        if comparison is ObjectiveComparison.LESS:
            best_value = candidate[1]
            tied = [candidate]
        elif comparison is ObjectiveComparison.EQUAL:
            tied.append(candidate)
    return choose_uniformly(tied, random_source)[0]


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
