"""Scenario- and event-aware route path Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from functools import cmp_to_key

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    randomized_order,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    PathSearchIndex,
    build_path_search_index,
    route_node_sequence,
    sample_loopless_paths,
)
from multiobjective_vns.multiday_vrp.solution import ActivityStop, RouteLeg, VrpSolution
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
)
from multiobjective_vns.values import require_integer

__all__ = [
    'EventObjectiveRouteRerouteOperator',
    'ScenarioDifferentialRerouteOperator',
    'TimeWindowGuidedRouteRerouteOperator',
]


class ScenarioDifferentialRerouteOperator:
    """Reroute legs whose travel duration differs across scenarios."""

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
        self._candidate_limit = _limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return objective-ranked trials that reroute scenario-variable legs."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        return _scenario_differential_trials(
            self._instance,
            self._objectives,
            self._assess_plan,
            base,
            size,
            self._objective_position,
            self._candidate_limit,
            self._path_limit,
            self._expansion_limit,
            context,
        )

    def _path_candidates(
        self,
        working: VrpSolution,
        key: tuple[int, int],
        context: CandidateContext,
        path_search_index: PathSearchIndex | None = None,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
        route_index, leg_index = key
        route = working.routes[route_index]
        nodes = route_node_sequence(self._instance, working, route)
        paths = sample_loopless_paths(
            self._instance,
            nodes[leg_index],
            nodes[leg_index + 1],
            random_source=context.random_source,
            max_paths=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            path_search_index=path_search_index,
        )
        paths = tuple(path for path in paths if path != route.legs[leg_index].arc_ids)
        paths = randomized_sample(paths, self._candidate_limit, context.random_source)
        return _assessed_path_candidates(
            self._instance,
            self._objectives,
            self._assess_plan,
            working,
            route_index,
            leg_index,
            paths,
            ensure_feasible=context.ensure_feasible,
        )


class EventObjectiveRouteRerouteOperator(ScenarioDifferentialRerouteOperator):
    """Reroute legs that traverse events counted by one objective."""

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return ranked trials that reroute up to ``k`` event-exposed legs."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        return _event_objective_trials(
            self._instance,
            self._objectives,
            self._assess_plan,
            base,
            size,
            self._objective_id,
            self._objective_position,
            self._candidate_limit,
            self._path_limit,
            self._expansion_limit,
            context,
        )


class TimeWindowGuidedRouteRerouteOperator(ScenarioDifferentialRerouteOperator):
    """Reroute legs leading to an activity with the largest window guidance."""

    __slots__ = ('_windowed_activity_keys',)

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
        super().__init__(
            instance,
            objective_declaration,
            assess_plan,
            objective_id=objective_id,
            max_neighborhood_size=max_neighborhood_size,
            max_candidate_alternatives=max_candidate_alternatives,
            max_paths_per_leg=max_paths_per_leg,
            max_arc_expansion_steps=max_arc_expansion_steps,
        )
        self._windowed_activity_keys = _windowed_activity_keys(instance)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Reroute up to ``k`` legs leading to a targeted windowed activity."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        eligible_activities = _windowed_activity_occurrences(
            base, self._windowed_activity_keys
        )
        if not eligible_activities:
            return OperatorResult(candidates=())

        base_evaluation = (
            None
            if context.base_evaluation is None
            else context.base_evaluation.application_evaluation
        )
        analysis = (
            _analysis(base_evaluation)
            if isinstance(base_evaluation, ApplicationEvaluation)
            and base_evaluation.representation_valid
            else None
        )
        if analysis is not None and not analysis.is_complete:
            analysis = None
        targets = (
            _window_guided_activities(
                base, analysis, str(self._objective_id), eligible_activities
            )
            or eligible_activities
        )
        route_index, stop_index = choose_uniformly(targets, context.random_source)
        eligible_legs = [
            (route_index, leg_index)
            for leg_index in range(
                min(stop_index + 1, len(base.routes[route_index].legs))
            )
        ]
        if not eligible_legs:
            return OperatorResult(candidates=())
        path_search_index = (
            build_path_search_index(self._instance)
            if len(eligible_legs) > 1 and size > 1
            else None
        )

        working = base
        modifications = 0
        latest_candidates: tuple[tuple[VrpSolution, tuple[object, ...]], ...] = ()
        for _ in range(size):
            if not eligible_legs:
                break
            key = choose_uniformly(eligible_legs, context.random_source)
            eligible_legs.remove(key)
            candidates = self._path_candidates(working, key, context, path_search_index)
            if not candidates:
                continue
            latest_candidates = _rank_path_candidates(
                candidates,
                self._objectives,
                self._objective_position,
                context,
            )
            working = latest_candidates[0][0]
            modifications += 1
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), modifications)
                for solution, _values in latest_candidates[: context.max_candidates]
            )
        )


def _assessed_path_candidates(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    working: VrpSolution,
    route_index: int,
    leg_index: int,
    paths: tuple[tuple[str, ...], ...],
    *,
    ensure_feasible: bool,
) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
    candidates: list[tuple[VrpSolution, tuple[object, ...]]] = []
    for path in paths:
        routes = list(working.routes)
        legs = list(routes[route_index].legs)
        legs[leg_index] = RouteLeg(path)
        routes[route_index] = replace(routes[route_index], legs=tuple(legs))
        solution = replace(working, routes=tuple(routes))
        evaluation = assess_plan(instance, objectives, solution)
        if not isinstance(evaluation, ApplicationEvaluation):
            raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
        if not evaluation.representation_valid or evaluation.objective_values is None:
            continue
        if type(evaluation.objective_values) is not tuple:
            raise TypeError('objective_values must be a tuple')
        if len(evaluation.objective_values) != len(objectives.objectives):
            raise ValueError('objective vector length must match the declaration')
        if ensure_feasible and evaluation.feasible is not True:
            continue
        candidates.append((solution, evaluation.objective_values))
    return tuple(candidates)


def _rank_path_candidates(
    candidates: tuple[tuple[VrpSolution, tuple[object, ...]], ...],
    objectives: ObjectiveDeclaration,
    position: int,
    context: CandidateContext,
) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
    objective = objectives.objectives[position]

    def compare(
        left: tuple[VrpSolution, tuple[object, ...]],
        right: tuple[VrpSolution, tuple[object, ...]],
    ) -> int:
        comparison = objective.compare(left[1][position], right[1][position])
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    ordered = sorted(candidates, key=cmp_to_key(compare))
    ranked: list[tuple[VrpSolution, tuple[object, ...]]] = []
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


def _scenario_differential_trials(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    base: VrpSolution,
    size: int,
    objective_position: int,
    candidate_limit: int,
    path_limit: int,
    expansion_limit: int,
    context: CandidateContext,
) -> OperatorResult:
    stored_record = context.base_evaluation
    base_evaluation = (
        stored_record.application_evaluation
        if isinstance(stored_record, EvaluationRecord)
        else None
    )
    if not isinstance(base_evaluation, ApplicationEvaluation):
        base_evaluation = assess_plan(instance, objectives, base)
    if not isinstance(base_evaluation, ApplicationEvaluation):
        raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
    if not base_evaluation.representation_valid:
        return OperatorResult(candidates=())

    all_legs = _route_legs(base)
    variable_legs = _scenario_variable_legs(base, _analysis(base_evaluation))
    preferred_legs = variable_legs or all_legs
    if not preferred_legs:
        return OperatorResult(candidates=())
    endpoints_by_route = tuple(
        route_node_sequence(instance, base, route) for route in base.routes
    )

    scored: list[tuple[VrpSolution, int, object]] = []
    seen_plans: set[VrpSolution] = set()
    sampled_replacements: dict[tuple[int, int], tuple[tuple[str, ...], ...]] = {}
    attempt_limit = candidate_limit * 4
    for _ in range(attempt_limit):
        if len(seen_plans) >= candidate_limit:
            break
        working = base
        available_legs = list(preferred_legs)
        modifications = 0
        for _ in range(size):
            while available_legs:
                leg_key = choose_uniformly(available_legs, context.random_source)
                available_legs.remove(leg_key)
                route_index, leg_index = leg_key
                route = working.routes[route_index]
                endpoints = endpoints_by_route[route_index]
                if leg_key not in sampled_replacements:
                    origin = endpoints[leg_index]
                    destination = endpoints[leg_index + 1]
                    paths = (
                        ()
                        if origin == destination
                        else sample_loopless_paths(
                            instance,
                            origin,
                            destination,
                            random_source=context.random_source,
                            max_paths=path_limit,
                            max_arc_expansion_steps=expansion_limit,
                        )
                    )
                    sampled_replacements[leg_key] = tuple(
                        path
                        for path in paths
                        if path != base.routes[route_index].legs[leg_index].arc_ids
                    )
                alternatives = sampled_replacements[leg_key]
                if not alternatives:
                    continue
                path = choose_uniformly(alternatives, context.random_source)
                legs = list(route.legs)
                legs[leg_index] = RouteLeg(path)
                routes = list(working.routes)
                routes[route_index] = replace(route, legs=tuple(legs))
                working = replace(working, routes=tuple(routes))
                modifications += 1
                break
            if not available_legs:
                break

        if not modifications or working in seen_plans:
            continue
        seen_plans.add(working)
        evaluation = assess_plan(instance, objectives, working)
        if not isinstance(evaluation, ApplicationEvaluation):
            raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
        if not evaluation.representation_valid:
            continue
        if evaluation.objective_values is None:
            raise ValueError('valid candidate assessment requires objective values')
        if not isinstance(evaluation.objective_values, tuple):
            raise TypeError('valid VRP assessment objective_values must be a tuple')
        if len(evaluation.objective_values) != len(objectives.objectives):
            raise ValueError('valid VRP assessment objective vector length is invalid')
        if context.ensure_feasible and evaluation.feasible is not True:
            continue
        scored.append(
            (working, modifications, evaluation.objective_values[objective_position])
        )

    objective = objectives.objectives[objective_position]

    def compare(
        left: tuple[VrpSolution, int, object],
        right: tuple[VrpSolution, int, object],
    ) -> int:
        comparison = objective.compare(left[2], right[2])
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    scored.sort(key=cmp_to_key(compare))
    ranked: list[tuple[VrpSolution, int, object]] = []
    group_start = 0
    while group_start < len(scored):
        group_end = group_start + 1
        while (
            group_end < len(scored)
            and compare(scored[group_start], scored[group_end]) == 0
        ):
            group_end += 1
        ranked.extend(
            randomized_order(scored[group_start:group_end], context.random_source)
        )
        group_start = group_end

    return OperatorResult(
        candidates=tuple(
            CandidateProposal(Candidate(solution), modifications)
            for solution, modifications, _value in ranked[: context.max_candidates]
        )
    )


def _event_objective_trials(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    base: VrpSolution,
    size: int,
    objective_id: ObjectiveId,
    objective_position: int,
    candidate_limit: int,
    path_limit: int,
    expansion_limit: int,
    context: CandidateContext,
) -> OperatorResult:
    """Compose distinct event-guided path changes into ranked full-plan trials."""
    all_legs = _route_legs(base)
    if not all_legs:
        return OperatorResult(candidates=())
    arc_event_ids, node_event_ids, arc_heads = _objective_event_locations(
        instance, objective_id
    )
    endpoints_by_route = tuple(
        route_node_sequence(instance, base, route) for route in base.routes
    )
    start_nodes = tuple(endpoints[0] for endpoints in endpoints_by_route)
    sampled_paths: dict[tuple[int, int], tuple[tuple[str, ...], ...]] = {}
    scored: list[tuple[VrpSolution, int, object]] = []
    seen_plans: set[VrpSolution] = set()
    attempt_limit = candidate_limit * 4
    for _ in range(attempt_limit):
        if len(seen_plans) >= candidate_limit:
            break
        working = base
        modified_legs: set[tuple[int, int]] = set()
        unavailable_legs: set[tuple[int, int]] = set()
        modifications = 0
        for _ in range(size):
            while True:
                event_legs = tuple(
                    leg
                    for leg in _event_legs_for_locations(
                        working,
                        arc_event_ids,
                        node_event_ids,
                        arc_heads,
                        start_nodes,
                    )
                    if leg not in modified_legs and leg not in unavailable_legs
                )
                targets = event_legs or tuple(
                    leg
                    for leg in all_legs
                    if leg not in modified_legs and leg not in unavailable_legs
                )
                if not targets:
                    break
                leg_key = choose_uniformly(targets, context.random_source)
                if leg_key not in sampled_paths:
                    route_index, leg_index = leg_key
                    endpoints = endpoints_by_route[route_index]
                    paths = sample_loopless_paths(
                        instance,
                        endpoints[leg_index],
                        endpoints[leg_index + 1],
                        random_source=context.random_source,
                        max_paths=path_limit,
                        max_arc_expansion_steps=expansion_limit,
                    )
                    current_path = base.routes[route_index].legs[leg_index].arc_ids
                    sampled_paths[leg_key] = tuple(
                        path for path in paths if path != current_path
                    )
                alternatives = sampled_paths[leg_key]
                if not alternatives:
                    unavailable_legs.add(leg_key)
                    continue

                route_index, leg_index = leg_key
                route = working.routes[route_index]
                legs = list(route.legs)
                legs[leg_index] = RouteLeg(
                    choose_uniformly(alternatives, context.random_source)
                )
                routes = list(working.routes)
                routes[route_index] = replace(route, legs=tuple(legs))
                working = replace(working, routes=tuple(routes))
                modified_legs.add(leg_key)
                modifications += 1
                break
            if not targets:
                break

        if not modifications or working in seen_plans:
            continue
        seen_plans.add(working)
        evaluation = assess_plan(instance, objectives, working)
        if not isinstance(evaluation, ApplicationEvaluation):
            raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
        if not evaluation.representation_valid or evaluation.objective_values is None:
            continue
        if type(evaluation.objective_values) is not tuple:
            raise TypeError('valid VRP assessment objective_values must be a tuple')
        if len(evaluation.objective_values) != len(objectives.objectives):
            raise ValueError('valid VRP assessment objective vector length is invalid')
        if context.ensure_feasible and evaluation.feasible is not True:
            continue
        scored.append(
            (working, modifications, evaluation.objective_values[objective_position])
        )

    objective = objectives.objectives[objective_position]

    def compare(
        left: tuple[VrpSolution, int, object],
        right: tuple[VrpSolution, int, object],
    ) -> int:
        comparison = objective.compare(left[2], right[2])
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    scored.sort(key=cmp_to_key(compare))
    ranked: list[tuple[VrpSolution, int, object]] = []
    group_start = 0
    while group_start < len(scored):
        group_end = group_start + 1
        while (
            group_end < len(scored)
            and compare(scored[group_start], scored[group_end]) == 0
        ):
            group_end += 1
        ranked.extend(
            randomized_order(scored[group_start:group_end], context.random_source)
        )
        group_start = group_end

    return OperatorResult(
        candidates=tuple(
            CandidateProposal(Candidate(solution), modifications)
            for solution, modifications, _value in ranked[: context.max_candidates]
        )
    )


def _analysis(evaluation: ApplicationEvaluation) -> VrpOperatorAnalysis | None:
    if isinstance(evaluation.analysis, VrpOperatorAnalysis):
        return evaluation.analysis
    return None


def _route_legs(solution: VrpSolution) -> tuple[tuple[int, int], ...]:
    return tuple(
        (route_index, leg_index)
        for route_index, route in enumerate(solution.routes)
        for leg_index in range(len(route.legs))
    )


def _scenario_variable_legs(
    solution: VrpSolution, analysis: VrpOperatorAnalysis | None
) -> tuple[tuple[int, int], ...]:
    if (
        analysis is None
        or not analysis.is_complete
        or not analysis.scenario_evaluations
    ):
        return ()
    durations: dict[tuple[int, int], list[float]] = {
        key: [] for key in _route_legs(solution)
    }
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
                return ()
            for leg in trace.legs:
                if leg.leg_index >= len(route.legs):
                    return ()
                durations[(route_index, leg.leg_index)].append(
                    sum(item.travel_time for item in leg.arc_traversals)
                )
    return tuple(
        key
        for key, values in durations.items()
        if len(values) == len(analysis.scenario_evaluations)
        and values
        and max(values) > min(values)
    )


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


def _windowed_activity_occurrences(
    solution: VrpSolution,
    windowed_activity_keys: frozenset[tuple[str, str, int | None]],
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (route_index, stop_index)
        for route_index, route in enumerate(solution.routes)
        for stop_index, stop in enumerate(route.stops)
        if type(stop) is ActivityStop
        and (
            (
                stop.activity_ref.kind,
                stop.activity_ref.activity_id,
                None,
            )
            in windowed_activity_keys
            if stop.activity_ref.kind in {'pickup', 'delivery'}
            else (
                stop.activity_ref.kind,
                stop.activity_ref.activity_id,
                route.day_index,
            )
            in windowed_activity_keys
        )
    )


def _window_guided_activities(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    objective_id: str,
    eligible_activities: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    if analysis is None or not analysis.scenario_evaluations:
        return ()
    eligible_set = set(eligible_activities)
    guidance_by_stop = dict.fromkeys(eligible_activities, 0.0)
    for scenario in analysis.scenario_evaluations:
        traces_by_route = {
            (item.vehicle_id, item.day_index): item for item in scenario.vehicle_days
        }
        for route_index, route in enumerate(solution.routes):
            trace = traces_by_route.get((route.vehicle_id, route.day_index))
            if trace is None:
                continue
            for stop in trace.stops:
                key = (route_index, stop.stop_index)
                if key not in eligible_set or stop.stop_index >= len(route.stops):
                    continue
                target_stop = route.stops[stop.stop_index]
                if (
                    type(target_stop) is not ActivityStop
                    or stop.activity_ref != target_stop.activity_ref
                ):
                    continue
                guidance = next(
                    (
                        item.guidance_value
                        for item in stop.window_assessments_by_objective
                        if item.objective_id == objective_id
                    ),
                    0.0,
                )
                guidance_by_stop[key] = max(guidance_by_stop[key], guidance)
    positive = tuple(
        (key, value) for key, value in guidance_by_stop.items() if value > 0.0
    )
    if not positive:
        return ()
    largest = max(value for _key, value in positive)
    return tuple(key for key, value in positive if value == largest)


def _event_legs(
    instance: LoadedVrpInstance, solution: VrpSolution, objective_id: ObjectiveId
) -> tuple[tuple[int, int], ...]:
    arc_event_ids, node_event_ids, arc_heads = _objective_event_locations(
        instance, objective_id
    )
    start_nodes = tuple(
        route_node_sequence(instance, solution, route)[0] for route in solution.routes
    )
    return _event_legs_for_locations(
        solution, arc_event_ids, node_event_ids, arc_heads, start_nodes
    )


def _objective_event_locations(
    instance: LoadedVrpInstance, objective_id: ObjectiveId
) -> tuple[frozenset[str], frozenset[str], dict[str, str]]:
    event_type_ids = {
        item.event_type_id
        for item in instance.event_data.objective_event_types
        if item.objective_id == str(objective_id)
    }
    event_domains = {
        item.event_type_id: item.location_domain
        for item in instance.event_data.event_types
        if item.event_type_id in event_type_ids
    }
    arc_event_ids = frozenset(
        event.location_id
        for event in instance.event_data.events
        if event.event_type_id in event_type_ids
        and event_domains.get(event.event_type_id) == 'arc'
    )
    arc_edges = {item.arc_id: item.head_node_id for item in instance.network_data.arcs}
    node_event_ids = frozenset(
        event.location_id
        for event in instance.event_data.events
        if event.event_type_id in event_type_ids
        and event_domains.get(event.event_type_id) == 'node'
    )
    return arc_event_ids, node_event_ids, arc_edges


def _event_legs_for_locations(
    solution: VrpSolution,
    arc_event_ids: frozenset[str],
    node_event_ids: frozenset[str],
    arc_heads: dict[str, str],
    start_nodes: tuple[str, ...],
) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    for route_index, route in enumerate(solution.routes):
        for leg_index, leg in enumerate(route.legs):
            has_event = (
                leg_index == 0 and start_nodes[route_index] in node_event_ids
            ) or any(
                arc_id in arc_event_ids or arc_heads[arc_id] in node_event_ids
                for arc_id in leg.arc_ids
            )
            if has_event:
                result.append((route_index, leg_index))
    return tuple(result)


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
