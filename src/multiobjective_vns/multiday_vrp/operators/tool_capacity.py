"""Relocate interventions to improve depot-segment tool capacity.

Created: 2026-11-04T12:09:06+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.capacity_profile import (
    capacity_excess_by_stop,
)
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import objective_position
from multiobjective_vns.multiday_vrp.operators.path_search import (
    rebuild_changed_routes,
    stop_node_id,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = ['ToolReservationGuidedInterventionRelocationOperator']


class ToolReservationGuidedInterventionRelocationOperator:
    """Move tool-using interventions between route positions and vehicles.

    Proposals retain the intervention identity and team assignment. Complete
    plan assessment enforces tool/cargo capacity and all other application
    constraints; candidates are ranked by the largest tool load on any
    depot-to-depot segment.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
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
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
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
        """Apply up to ``k`` intervention relocations.

        :param context: Search base, size, feasibility requirement, and random
            source supplied by the generic engine.
        :returns: A complete candidate when at least one move succeeds.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        working = require_vrp_base(context)
        moved: set[str] = set()
        modifications = 0
        for _ in range(size):
            targets = tuple(
                target
                for target in _targets(self._instance, working)
                if target[2].activity_ref.activity_id not in moved
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            activity_id = target[2].activity_ref.activity_id
            moved.add(activity_id)
            candidates = self._candidates(working, target, context)
            if not candidates:
                continue
            working = self._choose(candidates, context)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _candidates(
        self,
        solution: VrpSolution,
        target: tuple[int, int, ActivityStop],
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...], float], ...]:
        source_route_index, source_stop_index, selected = target
        positions = tuple(
            (route_index, insertion)
            for route_index, route in enumerate(solution.routes)
            for insertion in range(len(route.stops) + 1)
            if not (
                route_index == source_route_index
                and insertion in {source_stop_index, source_stop_index + 1}
            )
        )
        sampled = randomized_sample(
            positions, self._candidate_limit, context.random_source
        )
        results: list[tuple[VrpSolution, tuple[object, ...], float]] = []
        seen: set[tuple[tuple[int, tuple[RouteStop, ...]], ...]] = set()
        for destination_route_index, insertion in sampled:
            stops_by_route: dict[int, list[RouteStop]] = {
                source_route_index: list(solution.routes[source_route_index].stops)
            }
            if destination_route_index != source_route_index:
                stops_by_route[destination_route_index] = list(
                    solution.routes[destination_route_index].stops
                )
            del stops_by_route[source_route_index][source_stop_index]
            if (
                destination_route_index == source_route_index
                and insertion > source_stop_index
            ):
                insertion -= 1
            stops_by_route[destination_route_index].insert(insertion, selected)
            replacement = {
                route_index: tuple(stops)
                for route_index, stops in stops_by_route.items()
            }
            signature = tuple(sorted(replacement.items()))
            if signature in seen:
                continue
            seen.add(signature)
            rebuilt = rebuild_changed_routes(
                self._instance,
                solution,
                replacement,
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=self._candidate_limit,
            )
            for candidate in rebuilt:
                if candidate == solution:
                    continue
                evaluation = self._assess_plan(
                    self._instance, self._objectives, candidate
                )
                if not isinstance(evaluation, ApplicationEvaluation):
                    raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
                if not _usable(evaluation, self._objectives):
                    continue
                if context.ensure_feasible and evaluation.feasible is not True:
                    continue
                assert evaluation.objective_values is not None
                results.append(
                    (
                        candidate,
                        evaluation.objective_values,
                        _maximum_tool_segment_load(self._instance, candidate),
                    )
                )
                if len(results) >= self._candidate_limit:
                    return tuple(results)
        return tuple(results)

    def _choose(
        self,
        candidates: tuple[tuple[VrpSolution, tuple[object, ...], float], ...],
        context: CandidateContext,
    ) -> VrpSolution:
        best_load = min(candidate[2] for candidate in candidates)
        tied = [candidate for candidate in candidates if candidate[2] == best_load]
        if self._objective_position is not None:
            objective = self._objectives.objectives[self._objective_position]
            best = tied[0]
            objective_ties = [best]
            for candidate in tied[1:]:
                comparison = objective.compare(
                    candidate[1][self._objective_position],
                    best[1][self._objective_position],
                )
                if comparison is ObjectiveComparison.LESS:
                    best = candidate
                    objective_ties = [candidate]
                elif comparison is ObjectiveComparison.EQUAL:
                    objective_ties.append(candidate)
            tied = objective_ties
        return choose_uniformly(tied, context.random_source)[0]


def _targets(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
) -> tuple[tuple[int, int, ActivityStop], ...]:
    tool_ids_by_intervention: dict[str, set[str]] = defaultdict(set)
    for item in instance.intervention_data.intervention_tools:
        tool_ids_by_intervention[item.intervention_id].add(item.tool_id)
    all_targets = tuple(
        (route_index, stop_index, stop)
        for route_index, route in enumerate(solution.routes)
        for stop_index, stop in enumerate(route.stops)
        if type(stop) is ActivityStop
        and stop.activity_ref.kind == 'intervention'
        and tool_ids_by_intervention.get(stop.activity_ref.activity_id)
    )
    profile = capacity_excess_by_stop(instance, solution)
    maximum_excess = max(profile.values(), default=0.0)
    overloaded_segments = {
        (route_index, _segment_for_stop(instance, solution, route_index, stop_index))
        for (route_index, stop_index), excess in profile.items()
        if maximum_excess > 0.0 and excess == maximum_excess
    }
    if overloaded_segments:
        affected = tuple(
            target
            for target in all_targets
            if (
                target[0],
                _segment_for_stop(instance, solution, target[0], target[1]),
            )
            in overloaded_segments
        )
        if affected:
            return affected
    segment_weights = _intervention_segment_weights(instance, solution)
    if not segment_weights:
        return ()
    maximum = max(segment_weights.values())
    return tuple(
        target
        for target in all_targets
        if segment_weights.get(
            (target[0], _segment_for_stop(instance, solution, target[0], target[1])),
            0.0,
        )
        == maximum
    )


def _intervention_segment_weights(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> dict[tuple[int, int], float]:
    tool_demands = {
        item.tool_id: item.capacity_demand for item in instance.intervention_data.tools
    }
    required: dict[str, set[str]] = defaultdict(set)
    for item in instance.intervention_data.intervention_tools:
        required[item.intervention_id].add(item.tool_id)
    result: dict[tuple[int, int], float] = {}
    for route_index, route in enumerate(solution.routes):
        segments = _route_segments(instance, solution, route_index)
        for segment_index, start, end in segments:
            tool_ids = {
                tool_id
                for stop in route.stops[start : end + 1]
                if type(stop) is ActivityStop
                and stop.activity_ref.kind == 'intervention'
                for tool_id in required[stop.activity_ref.activity_id]
            }
            result[(route_index, segment_index)] = sum(
                tool_demands[tool_id] for tool_id in sorted(tool_ids)
            )
    return result


def _segment_for_stop(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route_index: int,
    stop_index: int,
) -> int:
    for segment_index, start, end in _route_segments(instance, solution, route_index):
        if start <= stop_index <= end:
            return segment_index
    return 0


def _route_segments(
    instance: LoadedVrpInstance, solution: VrpSolution, route_index: int
) -> tuple[tuple[int, int, int], ...]:
    route = solution.routes[route_index]
    depot_nodes = {item.node_id for item in instance.network_data.depots}
    segments: list[tuple[int, int, int]] = []
    start = 0
    segment_index = 0
    for stop_index, stop in enumerate(route.stops):
        node = stop_node_id(instance, solution, stop)
        if node in depot_nodes:
            segments.append((segment_index, start, stop_index))
            segment_index += 1
            start = stop_index + 1
    if start < len(route.stops):
        segments.append((segment_index, start, len(route.stops) - 1))
    return tuple(segments)


def _maximum_tool_segment_load(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> float:
    return max(_intervention_segment_weights(instance, solution).values(), default=0.0)


def _usable(
    evaluation: ApplicationEvaluation, objectives: ObjectiveDeclaration
) -> bool:
    return (
        evaluation.representation_valid
        and evaluation.feasible is not None
        and evaluation.objective_values is not None
        and type(evaluation.objective_values) is tuple
        and len(evaluation.objective_values) == len(objectives.objectives)
    )


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
