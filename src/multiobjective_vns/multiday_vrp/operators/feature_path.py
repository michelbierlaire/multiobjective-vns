"""Scenario- and event-aware route path Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace

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
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    route_node_sequence,
    sample_loopless_paths,
)
from multiobjective_vns.multiday_vrp.solution import RouteLeg, VrpSolution
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation
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
        """Change up to ``k`` legs, targeting scenario-variable legs first."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        changed: set[tuple[int, int]] = set()
        for _ in range(size):
            analysis = _analysis(
                self._assess_plan(self._instance, self._objectives, working)
            )
            all_legs = _route_legs(working)
            variable_legs = _scenario_variable_legs(working, analysis)
            targets = tuple(leg for leg in variable_legs if leg not in changed)
            if not targets:
                targets = tuple(leg for leg in all_legs if leg not in changed)
            if not targets:
                break
            key = choose_uniformly(targets, context.random_source)
            candidates = self._path_candidates(working, key, context)
            if not candidates:
                changed.add(key)
                continue
            working = _choose_best(
                candidates,
                self._objectives,
                self._objective_position,
                context,
            )
            changed.add(key)
        modifications = sum(
            working.routes[route_index].legs[leg_index]
            != base.routes[route_index].legs[leg_index]
            for route_index, leg_index in changed
        )
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _path_candidates(
        self,
        working: VrpSolution,
        key: tuple[int, int],
        context: CandidateContext,
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
        """Change up to ``k`` event-exposed legs, using all legs as fallback."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        changed: set[tuple[int, int]] = set()
        for _ in range(size):
            targets = tuple(
                key
                for key in _event_legs(self._instance, working, self._objective_id)
                if key not in changed
            )
            if not targets:
                targets = tuple(
                    key for key in _route_legs(working) if key not in changed
                )
            if not targets:
                break
            key = choose_uniformly(targets, context.random_source)
            candidates = self._path_candidates(working, key, context)
            if not candidates:
                changed.add(key)
                continue
            working = _choose_best(
                candidates,
                self._objectives,
                self._objective_position,
                context,
            )
            changed.add(key)
        modifications = sum(
            working.routes[route_index].legs[leg_index]
            != base.routes[route_index].legs[leg_index]
            for route_index, leg_index in changed
        )
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )


class TimeWindowGuidedRouteRerouteOperator(ScenarioDifferentialRerouteOperator):
    """Reroute legs leading to an activity with the largest window guidance."""

    def call(self, context: CandidateContext) -> OperatorResult:
        """Change up to ``k`` paths, targeting window guidance when available."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        changed: set[tuple[int, int]] = set()
        for _ in range(size):
            analysis = _analysis(
                self._assess_plan(self._instance, self._objectives, working)
            )
            targets = tuple(
                key
                for key in _window_guided_legs(
                    working, analysis, str(self._objective_id)
                )
                if key not in changed
            )
            if not targets:
                targets = tuple(
                    key for key in _route_legs(working) if key not in changed
                )
            if not targets:
                break
            key = choose_uniformly(targets, context.random_source)
            candidates = self._path_candidates(working, key, context)
            changed.add(key)
            if not candidates:
                continue
            working = _choose_best(
                candidates,
                self._objectives,
                self._objective_position,
                context,
            )
        modifications = sum(
            working.routes[route_index].legs[leg_index]
            != base.routes[route_index].legs[leg_index]
            for route_index, leg_index in changed
        )
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
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


def _choose_best(
    candidates: tuple[tuple[VrpSolution, tuple[object, ...]], ...],
    objectives: ObjectiveDeclaration,
    position: int,
    context: CandidateContext,
) -> VrpSolution:
    objective = objectives.objectives[position]
    best = candidates[0]
    tied = [best]
    for candidate in candidates[1:]:
        comparison = objective.compare(candidate[1][position], best[1][position])
        if comparison is ObjectiveComparison.LESS:
            best = candidate
            tied = [candidate]
        elif comparison is ObjectiveComparison.EQUAL:
            tied.append(candidate)
    return choose_uniformly(tied, context.random_source)[0]


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
    if analysis is None or not analysis.scenario_evaluations:
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


def _window_guided_legs(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    objective_id: str,
) -> tuple[tuple[int, int], ...]:
    if analysis is None:
        return ()
    guidance_by_stop: dict[tuple[int, int], float] = {}
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
                guidance = next(
                    (
                        item.guidance_value
                        for item in stop.window_assessments_by_objective
                        if item.objective_id == objective_id
                    ),
                    0.0,
                )
                key = (route_index, stop.stop_index)
                guidance_by_stop[key] = max(guidance_by_stop.get(key, 0.0), guidance)
    positive = tuple(
        (key, value) for key, value in guidance_by_stop.items() if value > 0.0
    )
    if not positive:
        return ()
    largest = max(value for _key, value in positive)
    selected_stops = tuple(key for key, value in positive if value == largest)
    return tuple(
        (route_index, leg_index)
        for route_index, stop_index in selected_stops
        for leg_index in range(
            min(stop_index + 1, len(solution.routes[route_index].legs))
        )
    )


def _event_legs(
    instance: LoadedVrpInstance, solution: VrpSolution, objective_id: ObjectiveId
) -> tuple[tuple[int, int], ...]:
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
    events = tuple(
        item
        for item in instance.event_data.events
        if item.event_type_id in event_type_ids
    )
    arc_edges = {
        item.arc_id: (item.tail_node_id, item.head_node_id)
        for item in instance.network_data.arcs
    }
    result: list[tuple[int, int]] = []
    for route_index, route in enumerate(solution.routes):
        nodes = route_node_sequence(instance, solution, route)
        for leg_index, leg in enumerate(route.legs):
            path_nodes = [nodes[leg_index]]
            path_nodes.extend(arc_edges[arc][1] for arc in leg.arc_ids)
            has_event = any(
                (
                    event.location_id in leg.arc_ids
                    if event_domains.get(event.event_type_id) == 'arc'
                    else event.location_id in path_nodes
                )
                for event in events
            )
            if has_event:
                result.append((route_index, leg_index))
    return tuple(result)


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
