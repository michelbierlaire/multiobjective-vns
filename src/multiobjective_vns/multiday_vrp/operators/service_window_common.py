"""Shared scoring, window-slack, and repair-unit helpers for VRP Operators.

Created: 2026-11-06T13:24:40+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TypeVar

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.helpers import _tolerance
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    assess_complete_candidate,
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    PathSearchIndex,
    build_path_search_index,
    rebuild_changed_routes,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_elimination import (
    _relationship_groups,
    _service_units,
    _stop_unit_key,
    _UnitKey,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_insertion_search import (
    nondominated_scored_plans,
    target_first_nondominated_scored_plans,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_assignment_helpers import (
    _choose_scored,
    _ScoredPlan,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteStop,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
    ObjectiveFunction,
)
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.values import require_integer

__all__ = [
    '_Bundle',
    '_ScoredPlan',
    '_UnitKey',
    '_WindowStructuralOperator',
    '_append_scored',
    '_block_slack',
    '_bundle_from_route',
    '_bundle_slack',
    '_bundle_windowed',
    '_complete_base_analysis',
    '_first_key_position',
    '_insert_bundle',
    '_is_windowed_activity',
    '_remove_bundle',
    '_route_bundles',
    '_select_critical_pair',
    '_select_critical_target',
    '_unique_solutions',
    '_without_keys',
    'select_window_first_candidates',
]

_ScoredCandidate = TypeVar('_ScoredCandidate')
_Unit = tuple[_UnitKey, ...]
_RoutePair = tuple[int, int]


def select_window_first_candidates(
    candidates: Sequence[tuple[_ScoredCandidate, tuple[object, ...]]],
    window_objective: ObjectiveFunction,
    *,
    window_objective_position: int,
) -> tuple[tuple[_ScoredCandidate, tuple[object, ...]], ...]:
    """Keep candidates with the least assessed time-window penalty.

    :param candidates: Complete candidate and objective-vector pairs.
    :param window_objective: Comparator for the dedicated window-penalty objective.
    :param window_objective_position: Its position in each objective vector.
    :returns: All candidates tied at the least window-penalty value.
    :raises ValueError: If there are no candidates or a vector is malformed.
    """
    if not candidates:
        raise ValueError('at least one scored candidate is required')
    if type(window_objective_position) is not int or window_objective_position < 0:
        raise ValueError('window_objective_position must be nonnegative')
    best = candidates[0]
    if window_objective_position >= len(best[1]):
        raise ValueError('window objective position exceeds the objective vector')
    tied = [best]
    for candidate in candidates[1:]:
        if window_objective_position >= len(candidate[1]):
            raise ValueError('candidate objective vector is too short')
        comparison = window_objective.compare(
            candidate[1][window_objective_position],
            best[1][window_objective_position],
        )
        if comparison is ObjectiveComparison.LESS:
            best = candidate
            tied = [candidate]
        elif comparison is ObjectiveComparison.EQUAL:
            tied.append(candidate)
    return tuple(tied)


class _WindowStructuralOperator:
    """Common assessment, ranking, and proposal limits for focused Operators."""

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_window_position',
        '_target_position',
        '_maximum_size',
        '_candidate_limit',
        '_path_limit',
        '_expansion_limit',
        '_path_search_index',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        window_objective_id: ObjectiveId,
        objective_id: ObjectiveId | None,
        max_neighborhood_size: int,
        max_candidate_alternatives: int,
        max_paths_per_leg: int,
        max_arc_expansion_steps: int,
    ) -> None:
        if instance.time_window_violation_objective_id != window_objective_id:
            raise ValueError(
                'window_objective_id must match the instance window objective'
            )
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._window_position = objective_position(
            objective_declaration, window_objective_id
        )
        self._target_position = (
            None
            if objective_id is None
            else objective_position(objective_declaration, objective_id)
        )
        self._maximum_size = require_integer(
            max_neighborhood_size,
            name='max_neighborhood_size',
            minimum=1,
        )
        self._candidate_limit = _bounded_limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _bounded_limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _bounded_limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )
        self._path_search_index: PathSearchIndex = build_path_search_index(instance)

    def _assess(
        self, candidate: VrpSolution, *, ensure_feasible: bool
    ) -> tuple[object, ...] | None:
        evaluation = assess_complete_candidate(
            self._instance,
            self._objectives,
            candidate,
            self._assess_plan,
            ensure_feasible=ensure_feasible,
        )
        if evaluation is None or evaluation.objective_values is None:
            return None
        return evaluation.objective_values

    def _choose(
        self,
        candidates: Sequence[_ScoredPlan],
        context: CandidateContext,
    ) -> _ScoredPlan:
        window_objective = self._objectives.objectives[self._window_position]
        retained = select_window_first_candidates(
            candidates,
            window_objective,
            window_objective_position=self._window_position,
        )
        if self._target_position is None:
            retained = nondominated_scored_plans(retained, self._objectives)
        else:
            retained = target_first_nondominated_scored_plans(
                retained,
                self._objectives,
                objective_position=self._target_position,
            )
        return _choose_scored(
            retained,
            self._objectives,
            self._target_position,
            context,
        )

    def _rebuild(
        self,
        base: VrpSolution,
        proposal: VrpSolution,
        changed_route_indices: Sequence[int],
        context: CandidateContext,
        *,
        max_candidate_alternatives: int | None = None,
    ) -> tuple[VrpSolution, ...]:
        replacements = {
            index: proposal.routes[index].stops
            for index in changed_route_indices
            if proposal.routes[index].stops != base.routes[index].stops
        }
        if not replacements:
            return ()
        candidate_limit = self._candidate_limit
        if max_candidate_alternatives is not None:
            candidate_limit = require_integer(
                max_candidate_alternatives,
                name='max_candidate_alternatives',
                minimum=1,
            )
            if candidate_limit > self._candidate_limit:
                raise ValueError(
                    'max_candidate_alternatives cannot exceed the Operator limit'
                )
        return rebuild_changed_routes(
            self._instance,
            base,
            replacements,
            random_source=context.random_source,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_candidate_alternatives=candidate_limit,
            path_search_index=self._path_search_index,
        )


@dataclass(frozen=True, slots=True)
class _Bundle:
    keys: frozenset[_UnitKey]
    stops: tuple[RouteStop, ...]


def _bundle_from_route(route: VehicleDayRoute, keys: _Unit) -> _Bundle:
    key_set = frozenset(keys)
    stops = tuple(
        replace(stop, tour_end=False)
        for stop in route.stops
        if _stop_unit_key(stop) in key_set
    )
    return _Bundle(key_set, stops)


def _route_bundles(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route_index: int,
) -> tuple[_Bundle, ...]:
    """Return complete repair units wholly contained in one route.

    A cross-route relationship makes that relationship group ineligible for
    ejection. It must not make unrelated, complete units on the same route
    ineligible too.
    """
    bundles: list[_Bundle] = []
    for group in _relationship_groups(instance, _service_units(solution)):
        if not any(unit.occurrences for unit in group) or any(
            occurrence_route != route_index
            for unit in group
            for occurrence_route, _stop_index, _stop in unit.occurrences
        ):
            continue
        keys = tuple(unit.key for unit in group)
        bundle = _bundle_from_route(solution.routes[route_index], keys)
        if bundle.stops:
            bundles.append(bundle)
    return tuple(bundles)


def _without_keys(
    stops: tuple[RouteStop, ...], keys: frozenset[_UnitKey]
) -> tuple[RouteStop, ...]:
    return tuple(stop for stop in stops if _stop_unit_key(stop) not in keys)


def _remove_bundle(
    solution: VrpSolution,
    route_index: int,
    keys: frozenset[_UnitKey],
) -> VrpSolution | None:
    if not 0 <= route_index < len(solution.routes):
        return None
    route = solution.routes[route_index]
    remaining = _without_keys(route.stops, keys)
    if len(remaining) == len(route.stops):
        return None
    routes = list(solution.routes)
    routes[route_index] = replace(route, stops=remaining, legs=())
    return replace(solution, routes=tuple(routes))


def _insert_bundle(
    stops: tuple[RouteStop, ...],
    bundle: tuple[RouteStop, ...],
    index: int,
) -> tuple[RouteStop, ...]:
    return (*stops[:index], *bundle, *stops[index:])


def _first_key_position(stops: tuple[RouteStop, ...], keys: frozenset[_UnitKey]) -> int:
    return next(
        index for index, stop in enumerate(stops) if _stop_unit_key(stop) in keys
    )


def _is_windowed_activity(
    instance: LoadedVrpInstance,
    day_index: int,
    stop: RouteStop,
) -> bool:
    if type(stop) is not ActivityStop:
        return False
    reference = stop.activity_ref
    if reference.kind in {'pickup', 'delivery'}:
        return any(
            window.package_id == reference.activity_id
            and window.activity == reference.kind
            for window in instance.package_data.package_time_windows
        )
    return reference.kind == 'intervention' and any(
        window.intervention_id == reference.activity_id
        and window.day_index == day_index
        for window in instance.intervention_data.intervention_time_windows
    )


def _bundle_windowed(
    instance: LoadedVrpInstance,
    day_index: int,
    bundle: _Bundle,
) -> bool:
    return any(
        _is_windowed_activity(instance, day_index, stop) for stop in bundle.stops
    )


def _select_critical_target(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    targets: tuple[tuple[int, int], ...],
    context: CandidateContext,
) -> tuple[int, int]:
    analysis = _complete_base_analysis(context)
    if analysis is None:
        return choose_uniformly(targets, context.random_source)
    scored = tuple(
        (
            target,
            _block_slack(instance, solution, target[0], target[1], context.k, analysis),
        )
        for target in targets
    )
    known = tuple((target, slack) for target, slack in scored if slack is not None)
    if not known:
        return choose_uniformly(targets, context.random_source)
    best = min(slack for _target, slack in known)
    tied = tuple(
        target
        for target, slack in known
        if abs(slack - best) <= _tolerance(slack, best)
    )
    return choose_uniformly(tied, context.random_source)


def _select_critical_pair(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    pairs: list[tuple[int, _Bundle, int, _Bundle]],
    context: CandidateContext,
) -> tuple[int, _Bundle, int, _Bundle]:
    analysis = _complete_base_analysis(context)
    if analysis is None:
        return choose_uniformly(pairs, context.random_source)
    slack_by_bundle: dict[tuple[int, frozenset[_UnitKey]], float | None] = {}

    def bundle_slack(route_index: int, bundle: _Bundle) -> float | None:
        key = (route_index, bundle.keys)
        if key not in slack_by_bundle:
            slack_by_bundle[key] = _bundle_slack(
                instance,
                solution,
                route_index,
                bundle,
                context,
                analysis,
                unknown_if_incomplete=True,
            )
        return slack_by_bundle[key]

    scored: list[tuple[tuple[int, _Bundle, int, _Bundle], float | None]] = []
    for pair in pairs:
        left_index, left, right_index, right = pair
        slacks = tuple(
            slack
            for route_index, bundle in ((left_index, left), (right_index, right))
            if (slack := bundle_slack(route_index, bundle)) is not None
        )
        scored.append((pair, min(slacks) if slacks else None))
    known = tuple((pair, slack) for pair, slack in scored if slack is not None)
    if not known:
        return choose_uniformly(pairs, context.random_source)
    best = min(slack for _pair, slack in known)
    tied = tuple(
        pair for pair, slack in known if abs(slack - best) <= _tolerance(slack, best)
    )
    return choose_uniformly(tied, context.random_source)


def _bundle_slack(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route_index: int,
    bundle: _Bundle,
    context: CandidateContext,
    analysis: VrpOperatorAnalysis | None = None,
    *,
    unknown_if_incomplete: bool = True,
) -> float | None:
    selected_analysis = analysis or _complete_base_analysis(context)
    if selected_analysis is None:
        return None
    route = solution.routes[route_index]
    values: list[float] = []
    for stop_index, stop in enumerate(route.stops):
        if _stop_unit_key(stop) not in bundle.keys or not _is_windowed_activity(
            instance, route.day_index, stop
        ):
            continue
        slack = _activity_slack(
            instance, solution, route_index, stop_index, stop, selected_analysis
        )
        if slack is None:
            if unknown_if_incomplete:
                return None
            continue
        values.append(slack)
    return min(values) if values else None


def _block_slack(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route_index: int,
    start: int,
    size: int,
    analysis: VrpOperatorAnalysis,
) -> float | None:
    route = solution.routes[route_index]
    values: list[float] = []
    for stop_index in range(start, start + size):
        stop = route.stops[stop_index]
        if not _is_windowed_activity(instance, route.day_index, stop):
            continue
        slack = _activity_slack(
            instance, solution, route_index, stop_index, stop, analysis
        )
        if slack is None:
            return None
        values.append(slack)
    return min(values) if values else None


def _activity_slack(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route_index: int,
    stop_index: int,
    stop: RouteStop,
    analysis: VrpOperatorAnalysis,
) -> float | None:
    if type(stop) is not ActivityStop or not analysis.is_complete:
        return None
    route = solution.routes[route_index]
    windows = _activity_windows(instance, route.day_index, stop.activity_ref)
    if not windows:
        return None
    scenario_by_id = {
        scenario.scenario_id: scenario for scenario in analysis.scenario_evaluations
    }
    expected_scenario_ids = tuple(
        scenario.scenario_id for scenario in instance.scenario_bank.scenarios
    )
    if not expected_scenario_ids or any(
        scenario_id not in scenario_by_id for scenario_id in expected_scenario_ids
    ):
        return None
    scenario_slacks: list[float] = []
    for scenario_id in expected_scenario_ids:
        scenario = scenario_by_id[scenario_id]
        trace = next(
            (
                item
                for item in scenario.vehicle_days
                if item.vehicle_id == route.vehicle_id
                and item.day_index == route.day_index
            ),
            None,
        )
        if trace is None or not 0 <= stop_index < len(trace.stops):
            return None
        stop_trace = trace.stops[stop_index]
        if (
            stop_trace.activity_ref != stop.activity_ref
            or stop_trace.service_start is None
            or stop_trace.service_finish is None
        ):
            return None
        margins = tuple(
            min(
                stop_trace.service_start - lower,
                upper - stop_trace.service_finish,
            )
            for lower, upper in windows
        )
        scenario_slacks.append(max(margins))
    if not scenario_slacks:
        return None
    slack = min(scenario_slacks)
    return 0.0 if abs(slack) <= _tolerance(slack, 0.0) else slack


def _activity_windows(
    instance: LoadedVrpInstance,
    day_index: int,
    reference: ActivityRef,
) -> tuple[tuple[float, float], ...]:
    if reference.kind in {'pickup', 'delivery'}:
        return tuple(
            (window.start_time, window.end_time)
            for window in instance.package_data.package_time_windows
            if window.package_id == reference.activity_id
            and window.activity == reference.kind
        )
    if reference.kind == 'intervention':
        return tuple(
            (window.start_time, window.end_time)
            for window in instance.intervention_data.intervention_time_windows
            if window.intervention_id == reference.activity_id
            and window.day_index == day_index
        )
    return ()


def _complete_base_analysis(
    context: CandidateContext,
) -> VrpOperatorAnalysis | None:
    record = context.base_evaluation
    evaluation = None if record is None else record.application_evaluation
    analysis = None if evaluation is None else evaluation.analysis
    if isinstance(analysis, VrpOperatorAnalysis) and analysis.is_complete:
        return analysis
    return None


def _append_scored(
    candidates: list[_ScoredPlan],
    solution: VrpSolution,
    values: tuple[object, ...],
) -> None:
    if not any(previous[0] == solution for previous in candidates):
        candidates.append((solution, values))


def _unique_solutions(solutions: Sequence[VrpSolution]) -> list[VrpSolution]:
    result: list[VrpSolution] = []
    for solution in solutions:
        if solution not in result:
            result.append(solution)
    return result


def _bounded_limit(value: int, name: str, maximum: int) -> int:
    normalized = require_integer(value, name=name, minimum=1)
    if normalized > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return normalized
