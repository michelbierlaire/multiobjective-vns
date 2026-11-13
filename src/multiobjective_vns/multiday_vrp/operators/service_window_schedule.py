"""Search route timing assignments for lower scenario-wide window violations.

Created: 2026-11-05T11:17:46+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import fsum, isfinite

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.helpers import (
    _lower_violation,
    _upper_violation,
)
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    VehicleDayScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    randomized_order,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import objective_position
from multiobjective_vns.multiday_vrp.operators.service_window_common import (
    select_window_first_candidates,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_insertion_search import (
    target_first_nondominated_scored_plans,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteStop,
    TransferOperationStop,
    VehicleDayRoute,
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
from multiobjective_vns.values import finite_real, require_integer

__all__ = ['TimeWindowScheduleComplianceOperator']

_TimingField = tuple[int, int | None, str]
_WindowScore = tuple[int, float]
_WindowKey = tuple[str, str, int | None]
_WindowIndex = Mapping[_WindowKey, tuple[tuple[float, float], ...]]
_RouteTraceIndex = Mapping[tuple[str, int], VehicleDayScenarioEvaluation]


@dataclass(frozen=True, slots=True)
class _ScoredPlan:
    solution: VrpSolution
    evaluation: ApplicationEvaluation


class TimeWindowScheduleComplianceOperator:
    """Jointly adjust one route's start time and planned pre-service waits.

    :param instance: Validated VRP instance containing service windows.
    :param objective_declaration: Ordered objectives used to rank alternatives.
    :param assess_plan: Shared pure plan-assessment callback.
    :param objective_id: Objective used after window-first candidate selection.
    :param time_step: Positive time adjustment in the instance time unit.
    :param max_neighborhood_size: Maximum number of timing fields changed.
    :param max_candidate_alternatives: Maximum sampled timing assignments.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_position',
        '_window_position',
        '_window_index',
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
        objective_id: ObjectiveId,
        time_step: float,
        max_neighborhood_size: int,
        max_candidate_alternatives: int = 32,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._objective_position = objective_position(
            objective_declaration, objective_id
        )
        window_id = instance.time_window_violation_objective_id
        if window_id is None:
            raise ValueError(
                'schedule compliance repair requires a dedicated window objective'
            )
        self._window_position = objective_position(objective_declaration, window_id)
        self._window_index = _build_window_index(instance)
        self._time_step = finite_real(time_step, name='time_step')
        if self._time_step <= 0.0:
            raise ValueError('time_step must be positive')
        self._maximum_size = require_integer(
            max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )
        self._candidate_limit = require_integer(
            max_candidate_alternatives,
            name='max_candidate_alternatives',
            minimum=1,
        )
        if self._candidate_limit > 32:
            raise ValueError('max_candidate_alternatives must be at most 32')

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return ranked schedule alternatives without changing route structure."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        base_evaluation = None
        if context.base_evaluation is not None:
            # Feasibility constrains generated alternatives, not the evidence
            # used to identify the route that needs repair.
            base_evaluation = self._validate_evaluation(
                context.base_evaluation.application_evaluation,
                ensure_feasible=False,
            )
        base_analysis = (
            None if base_evaluation is None else _complete_analysis(base_evaluation)
        )

        trace_indexes: tuple[_RouteTraceIndex, ...] | None = None
        if base_analysis is None:
            values_by_route = {
                route_index: _candidate_values_without_analysis(
                    self._instance,
                    base,
                    route_index,
                    time_step=self._time_step,
                )
                for route_index in range(len(base.routes))
            }
            eligible_routes = tuple(
                route_index
                for route_index, values in values_by_route.items()
                if any(values.values())
            )
            if not eligible_routes:
                return OperatorResult(candidates=())
            selected_route_index = choose_uniformly(
                eligible_routes, context.random_source
            )
            candidate_values = values_by_route[selected_route_index]
        else:
            trace_indexes = _index_route_traces(base_analysis)
            route_scores: list[tuple[int, _WindowScore]] = []
            for route_index in range(len(base.routes)):
                score = _window_score(
                    self._instance,
                    base,
                    base_analysis,
                    route_indices=(route_index,),
                    window_index=self._window_index,
                    trace_indexes=trace_indexes,
                )
                if score is None:
                    return OperatorResult(candidates=())
                route_scores.append((route_index, score))

            if not route_scores:
                return OperatorResult(candidates=())
            worst_score = max(score for _route_index, score in route_scores)
            if worst_score[0] > 0:
                target_routes = tuple(
                    route_index
                    for route_index, score in route_scores
                    if score == worst_score
                )
                route_values: dict[int, dict[_TimingField, tuple[float, ...]]] = {}
            else:
                # A compliant plan still explores eligible timing fields, as
                # required by the documented zero-score fallback.
                route_values = {
                    route_index: _candidate_values_by_field(
                        self._instance,
                        base,
                        base_analysis,
                        route_index,
                        time_step=self._time_step,
                        window_index=self._window_index,
                        trace_indexes=trace_indexes,
                    )
                    for route_index in range(len(base.routes))
                }
                target_routes = tuple(
                    route_index
                    for route_index, values in route_values.items()
                    if any(values.values())
                )
                if not target_routes:
                    return OperatorResult(candidates=())

            selected_route_index = choose_uniformly(
                target_routes, context.random_source
            )
            if route_values:
                candidate_values = route_values[selected_route_index]
            else:
                candidate_values = _candidate_values_by_field(
                    self._instance,
                    base,
                    base_analysis,
                    selected_route_index,
                    time_step=self._time_step,
                    window_index=self._window_index,
                    trace_indexes=trace_indexes,
                )

        eligible_fields = tuple(
            field for field, values in candidate_values.items() if values
        )
        if not eligible_fields:
            return OperatorResult(candidates=())

        candidates: list[_ScoredPlan] = []
        seen_assignments: set[frozenset[tuple[_TimingField, float]]] = set()
        maximum_fields = min(size, len(eligible_fields))
        field_counts = tuple(range(1, maximum_fields + 1))
        for _ in range(self._candidate_limit):
            number_fields = choose_uniformly(field_counts, context.random_source)
            selected_fields = randomized_sample(
                eligible_fields, number_fields, context.random_source
            )
            changes = {
                field: choose_uniformly(candidate_values[field], context.random_source)
                for field in selected_fields
            }
            assignment = frozenset(changes.items())
            if not assignment or assignment in seen_assignments:
                continue
            seen_assignments.add(assignment)
            candidate = _apply_timing_changes(base, selected_route_index, changes)
            evaluation = self._evaluate(candidate, context.ensure_feasible)
            if evaluation is not None:
                candidates.append(_ScoredPlan(candidate, evaluation))

        if not candidates:
            return OperatorResult(candidates=())
        ranked = self._rank_candidates(candidates, context)
        proposals = tuple(
            CandidateProposal(
                Candidate(candidate.solution),
                _modification_count(base, candidate.solution, selected_route_index),
            )
            for candidate in ranked[: context.max_candidates]
        )
        return OperatorResult(candidates=proposals)

    def _evaluate(
        self, solution: VrpSolution, ensure_feasible: bool
    ) -> ApplicationEvaluation | None:
        evaluation = self._assess_plan(self._instance, self._objectives, solution)
        if not isinstance(evaluation, ApplicationEvaluation):
            raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
        return self._validate_evaluation(evaluation, ensure_feasible)

    def _validate_evaluation(
        self, evaluation: ApplicationEvaluation | None, ensure_feasible: bool
    ) -> ApplicationEvaluation | None:
        if evaluation is None:
            return None
        if not evaluation.representation_valid or evaluation.objective_values is None:
            return None
        values = evaluation.objective_values
        if type(values) is not tuple:
            raise TypeError('objective_values must be a tuple')
        if len(values) != len(self._objectives.objectives):
            raise ValueError('objective vector length must match the declaration')
        if ensure_feasible and evaluation.feasible is not True:
            return None
        return evaluation

    def _rank_candidates(
        self, candidates: list[_ScoredPlan], context: CandidateContext
    ) -> tuple[_ScoredPlan, ...]:
        window_objective = self._objectives.objectives[self._window_position]
        scored = tuple(
            (candidate, candidate.evaluation.objective_values)
            for candidate in candidates
            if candidate.evaluation.objective_values is not None
        )
        if not scored:
            return ()
        window_best = select_window_first_candidates(
            scored,
            window_objective,
            window_objective_position=self._window_position,
        )
        frontier = target_first_nondominated_scored_plans(
            window_best,
            self._objectives,
            objective_position=self._objective_position,
        )
        return randomized_order(
            tuple(candidate for candidate, _values in frontier),
            context.random_source,
        )


def _complete_analysis(
    evaluation: ApplicationEvaluation,
) -> VrpOperatorAnalysis | None:
    analysis = evaluation.analysis
    if isinstance(analysis, VrpOperatorAnalysis) and analysis.is_complete:
        return analysis
    return None


def _build_window_index(
    instance: LoadedVrpInstance,
) -> dict[_WindowKey, tuple[tuple[float, float], ...]]:
    windows: dict[_WindowKey, list[tuple[float, float]]] = {}
    for package_window in instance.package_data.package_time_windows:
        windows.setdefault(
            (package_window.activity, package_window.package_id, None), []
        ).append((package_window.start_time, package_window.end_time))
    for intervention_window in instance.intervention_data.intervention_time_windows:
        windows.setdefault(
            (
                'intervention',
                intervention_window.intervention_id,
                intervention_window.day_index,
            ),
            [],
        ).append((intervention_window.start_time, intervention_window.end_time))
    return {key: tuple(sorted(values)) for key, values in windows.items()}


def _index_route_traces(
    analysis: VrpOperatorAnalysis,
) -> tuple[dict[tuple[str, int], VehicleDayScenarioEvaluation], ...]:
    return tuple(
        {(item.vehicle_id, item.day_index): item for item in scenario.vehicle_days}
        for scenario in analysis.scenario_evaluations
    )


def _window_score(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    *,
    route_indices: tuple[int, ...] | None = None,
    window_index: _WindowIndex | None = None,
    trace_indexes: tuple[_RouteTraceIndex, ...] | None = None,
) -> _WindowScore | None:
    selected_routes = (
        tuple(range(len(solution.routes))) if route_indices is None else route_indices
    )
    cached_windows = (
        _build_window_index(instance) if window_index is None else window_index
    )
    cached_traces = (
        _index_route_traces(analysis) if trace_indexes is None else trace_indexes
    )
    violation_count = 0
    weighted_amounts: list[float] = []
    for scenario_index, scenario in enumerate(analysis.scenario_evaluations):
        traces = cached_traces[scenario_index]
        for route_index in selected_routes:
            route = solution.routes[route_index]
            route_trace = traces.get((route.vehicle_id, route.day_index))
            if route_trace is None or len(route_trace.stops) != len(route.stops):
                return None
            for stop_index, stop in enumerate(route.stops):
                if not isinstance(stop, ActivityStop):
                    continue
                windows = _activity_windows(cached_windows, stop, route.day_index)
                if not windows:
                    continue
                trace = route_trace.stops[stop_index]
                if trace.service_start is None or trace.service_finish is None:
                    return None
                amount = min(
                    _lower_violation(trace.service_start, start)
                    + _upper_violation(trace.service_finish, end)
                    for start, end in windows
                )
                if amount > 0.0:
                    violation_count += 1
                    weighted_amounts.append(scenario.probability * amount)
    return violation_count, fsum(weighted_amounts)


def _activity_windows(
    window_index: _WindowIndex,
    stop: ActivityStop,
    day_index: int,
) -> tuple[tuple[float, float], ...]:
    reference = stop.activity_ref
    if reference.kind == 'intervention':
        return window_index.get(('intervention', reference.activity_id, day_index), ())
    if reference.kind in {'pickup', 'delivery'}:
        return window_index.get((reference.kind, reference.activity_id, None), ())
    return ()


def _candidate_values_by_field(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    route_index: int,
    *,
    time_step: float,
    window_index: _WindowIndex | None = None,
    trace_indexes: tuple[_RouteTraceIndex, ...] | None = None,
) -> dict[_TimingField, tuple[float, ...]]:
    route = solution.routes[route_index]
    cached_windows = (
        _build_window_index(instance) if window_index is None else window_index
    )
    cached_traces = (
        _index_route_traces(analysis) if trace_indexes is None else trace_indexes
    )
    activities: list[tuple[int, tuple[tuple[float, float], ...]]] = []
    for stop_index, stop in enumerate(route.stops):
        if isinstance(stop, ActivityStop):
            windows = _activity_windows(cached_windows, stop, route.day_index)
            if windows:
                activities.append((stop_index, windows))
    route_traces = tuple(
        (scenario, cached_traces[index].get((route.vehicle_id, route.day_index)))
        for index, scenario in enumerate(analysis.scenario_evaluations)
    )
    if any(trace is None for _scenario, trace in route_traces):
        return {}

    deltas: list[float] = []
    for scenario, route_trace in route_traces:
        assert route_trace is not None
        for stop_index, windows in activities:
            if stop_index >= len(route_trace.stops):
                return {}
            trace = route_trace.stops[stop_index]
            if trace.service_start is None or trace.service_finish is None:
                return {}
            for start, end in windows:
                deltas.extend(
                    (
                        start - trace.service_start,
                        end - trace.service_finish,
                    )
                )

    fields: list[_TimingField] = [(route_index, None, 'start_time')]
    fields.extend(
        (route_index, stop_index, 'pre_service_wait')
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, (ActivityStop, TransferOperationStop))
    )
    values_by_field: dict[_TimingField, tuple[float, ...]] = {}
    for field in fields:
        _route_index, target_stop_index, field_name = field
        current = (
            route.start_time
            if target_stop_index is None
            else route.stops[target_stop_index].pre_service_wait
        )
        proposals = (current - time_step, current + time_step)
        values_by_field[field] = tuple(
            dict.fromkeys(
                value
                for value in (*proposals, *(current + delta for delta in deltas))
                if value != current
                and _field_value_valid(instance, route, field_name, value)
            )
        )
    return values_by_field


def _candidate_values_without_analysis(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route_index: int,
    *,
    time_step: float,
) -> dict[_TimingField, tuple[float, ...]]:
    """Return bounded time-step changes without inspecting the input plan."""
    route = solution.routes[route_index]
    fields: list[_TimingField] = [(route_index, None, 'start_time')]
    fields.extend(
        (route_index, stop_index, 'pre_service_wait')
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, (ActivityStop, TransferOperationStop))
    )
    values_by_field: dict[_TimingField, tuple[float, ...]] = {}
    for field in fields:
        _field_route_index, stop_index, field_name = field
        current = (
            route.start_time
            if stop_index is None
            else route.stops[stop_index].pre_service_wait
        )
        values_by_field[field] = tuple(
            value
            for value in (current - time_step, current + time_step)
            if value != current
            and _field_value_valid(instance, route, field_name, value)
        )
    return values_by_field


def _field_value_valid(
    instance: LoadedVrpInstance,
    route: VehicleDayRoute,
    field_name: str,
    value: float,
) -> bool:
    if not isfinite(value):
        return False
    if field_name == 'pre_service_wait':
        return value >= 0.0
    if field_name != 'start_time':
        raise ValueError(f'unsupported timing field {field_name!r}')
    day_index = route.day_index
    if not 1 <= day_index <= len(instance.scenario_bank.day_boundaries):
        return False
    boundaries = instance.scenario_bank.day_boundaries[day_index - 1]
    return boundaries[0] <= value < boundaries[-1]


def _apply_timing_changes(
    solution: VrpSolution,
    route_index: int,
    changes: dict[_TimingField, float],
) -> VrpSolution:
    route = solution.routes[route_index]
    start_time = changes.get((route_index, None, 'start_time'), route.start_time)
    stop_waits: dict[int, float] = {}
    for (target_route_index, stop_index, field_name), value in changes.items():
        if target_route_index != route_index:
            raise ValueError('all timing changes must target the selected route')
        if field_name == 'start_time' and stop_index is None:
            continue
        if field_name != 'pre_service_wait' or stop_index is None:
            raise ValueError(f'invalid timing field {field_name!r}')
        stop_waits[stop_index] = value

    if stop_waits:
        stops = tuple(
            _with_pre_service_wait(stop, stop_waits[stop_index])
            if stop_index in stop_waits
            else stop
            for stop_index, stop in enumerate(route.stops)
        )
    else:
        stops = route.stops
    changed_route = VehicleDayRoute(
        route.vehicle_id,
        route.day_index,
        route.overnight_depot_id,
        start_time,
        route.driver_id,
        stops,
        route.legs,
    )
    routes = (
        *solution.routes[:route_index],
        changed_route,
        *solution.routes[route_index + 1 :],
    )
    return VrpSolution(routes, solution.transfer_steps)


def _with_pre_service_wait(
    stop: RouteStop, value: float
) -> ActivityStop | TransferOperationStop:
    if isinstance(stop, ActivityStop):
        return ActivityStop(
            stop.activity_ref,
            stop.team_member_ids,
            value,
            stop.wait,
            stop.break_duration,
            stop.tour_end,
        )
    if isinstance(stop, TransferOperationStop):
        return TransferOperationStop(
            stop.transfer_step_ref,
            stop.role,
            value,
            stop.wait,
            stop.break_duration,
            stop.tour_end,
        )
    raise TypeError('pre_service_wait target must be a non-depot stop')


def _modification_count(
    base: VrpSolution, candidate: VrpSolution, route_index: int
) -> int:
    base_route = base.routes[route_index]
    candidate_route = candidate.routes[route_index]
    return int(base_route.start_time != candidate_route.start_time) + sum(
        left.pre_service_wait != right.pre_service_wait
        for left, right in zip(base_route.stops, candidate_route.stops, strict=True)
    )
