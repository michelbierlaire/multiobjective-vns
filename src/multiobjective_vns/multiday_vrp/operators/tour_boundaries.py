"""Split and merge multi-tour vehicle-day routes.

Created: 2026-11-04T16:49:23+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
    randomized_order,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    assess_complete_candidate,
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    route_node_sequence,
    sample_loopless_paths,
    stop_node_id,
)
from multiobjective_vns.multiday_vrp.solution import (
    DepotOnlyStop,
    RouteLeg,
    RouteStop,
    VrpSolution,
)
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.values import require_integer

__all__ = ['MergeToursOperator', 'SplitTourOperator']

type _ScoredCandidate = tuple[VrpSolution, tuple[object, ...]]


@dataclass(frozen=True, slots=True)
class _ExistingVisitTarget:
    route_index: int
    stop_index: int


@dataclass(frozen=True, slots=True)
class _InsertedVisitTarget:
    route_index: int
    leg_index: int
    depot_node_id: str
    outbound_paths: tuple[tuple[str, ...], ...]
    inbound_paths: tuple[tuple[str, ...], ...]


type _SplitTarget = _ExistingVisitTarget | _InsertedVisitTarget


class SplitTourOperator:
    """Add tour boundaries at depot visits or by inserting a depot visit.

    :param instance: Validated VRP instance.
    :param objective_declaration: Ordered objectives for complete-plan ranking.
    :param assess_plan: Shared complete-plan assessment callback.
    :param max_neighborhood_size: Largest supported value of ``k``.
    :param objective_id: Optional objective used to rank sampled alternatives.
    :param max_candidate_alternatives: Maximum complete plans assessed per move.
    :param max_paths_per_leg: Maximum sampled paths per endpoint pair.
    :param max_arc_expansion_steps: Search bound for each sampled path.
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
        self._candidate_limit = _bounded_limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _bounded_limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _bounded_limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` tour splits sequentially.

        :param context: Immutable base, requested size, feasibility requirement,
            and shared random source.
        :returns: The changed plan and number of boundaries added, or ``m=0``.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        modifications = 0
        for _ in range(size):
            candidates = self._assessed_candidates(
                working, self._candidate_plans(working, context), context
            )
            if not candidates:
                break
            working = self._choose(candidates, context)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _candidate_plans(
        self, solution: VrpSolution, context: CandidateContext
    ) -> tuple[VrpSolution, ...]:
        targets = randomized_order(
            _split_targets(
                self._instance,
                solution,
                context,
                path_limit=self._path_limit,
                expansion_limit=self._expansion_limit,
            ),
            context.random_source,
        )
        candidates: list[VrpSolution] = []
        for target in targets:
            if isinstance(target, _ExistingVisitTarget):
                stops = list(solution.routes[target.route_index].stops)
                stop = stops[target.stop_index]
                stops[target.stop_index] = replace(stop, tour_end=True)
                candidate = _replace_route_stops(
                    solution, target.route_index, tuple(stops)
                )
                _append_distinct(candidates, candidate)
            else:
                path_pairs = tuple(product(target.outbound_paths, target.inbound_paths))
                for outbound_path, inbound_path in randomized_order(
                    path_pairs, context.random_source
                ):
                    candidate = _insert_tour_boundary(
                        solution,
                        target,
                        outbound_path,
                        inbound_path,
                    )
                    _append_distinct(candidates, candidate)
                    if len(candidates) >= self._candidate_limit:
                        return tuple(candidates)
            if len(candidates) >= self._candidate_limit:
                break
        return tuple(candidates)

    def _assessed_candidates(
        self,
        base: VrpSolution,
        candidates: tuple[VrpSolution, ...],
        context: CandidateContext,
    ) -> tuple[_ScoredCandidate, ...]:
        assessed: list[_ScoredCandidate] = []
        for candidate in candidates[: self._candidate_limit]:
            evaluation = assess_complete_candidate(
                self._instance,
                self._objectives,
                candidate,
                self._assess_plan,
                ensure_feasible=context.ensure_feasible,
            )
            if evaluation is None or evaluation.objective_values is None:
                continue
            if candidate != base:
                assessed.append((candidate, evaluation.objective_values))
        return tuple(assessed)

    def _choose(
        self, candidates: tuple[_ScoredCandidate, ...], context: CandidateContext
    ) -> VrpSolution:
        return _choose_candidate(
            candidates,
            self._objectives,
            self._objective_position,
            context,
        )[0]


class MergeToursOperator:
    """Remove tour boundaries while retaining each intermediate depot visit.

    :param instance: Validated VRP instance.
    :param objective_declaration: Ordered objectives for complete-plan ranking.
    :param assess_plan: Shared complete-plan assessment callback.
    :param max_neighborhood_size: Largest supported value of ``k``.
    :param objective_id: Optional objective used to rank sampled alternatives.
    :param max_candidate_alternatives: Maximum complete plans assessed per move.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_position',
        '_maximum_size',
        '_candidate_limit',
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
        self._candidate_limit = _bounded_limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` tour merges sequentially.

        :param context: Immutable base, requested size, feasibility requirement,
            and shared random source.
        :returns: The changed plan and number of boundaries removed, or ``m=0``.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        modifications = 0
        for _ in range(size):
            targets = _merge_targets(self._instance, working)
            selected = randomized_sample(
                targets, self._candidate_limit, context.random_source
            )
            candidates = tuple(
                _clear_tour_boundary(working, route_index, stop_index)
                for route_index, stop_index in selected
            )
            assessed = self._assessed_candidates(working, candidates, context)
            if not assessed:
                break
            working = self._choose(assessed, context)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _assessed_candidates(
        self,
        base: VrpSolution,
        candidates: tuple[VrpSolution, ...],
        context: CandidateContext,
    ) -> tuple[_ScoredCandidate, ...]:
        assessed: list[_ScoredCandidate] = []
        for candidate in candidates:
            evaluation = assess_complete_candidate(
                self._instance,
                self._objectives,
                candidate,
                self._assess_plan,
                ensure_feasible=context.ensure_feasible,
            )
            if evaluation is None or evaluation.objective_values is None:
                continue
            if candidate != base:
                assessed.append((candidate, evaluation.objective_values))
        return tuple(assessed)

    def _choose(
        self, candidates: tuple[_ScoredCandidate, ...], context: CandidateContext
    ) -> VrpSolution:
        return _choose_candidate(
            candidates,
            self._objectives,
            self._objective_position,
            context,
        )[0]


def _split_targets(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    context: CandidateContext,
    *,
    path_limit: int,
    expansion_limit: int,
) -> tuple[_SplitTarget, ...]:
    max_tours = {
        item.vehicle_id: item.max_tours_per_day for item in instance.fleet_data.vehicles
    }
    depot_nodes = tuple(sorted({item.node_id for item in instance.network_data.depots}))
    targets: list[_SplitTarget] = []
    for route_index, route in enumerate(solution.routes):
        if 1 + sum(stop.tour_end for stop in route.stops) >= max_tours.get(
            route.vehicle_id, 1
        ):
            continue
        for stop_index, stop in enumerate(route.stops):
            if (
                stop_node_id(instance, solution, stop) in depot_nodes
                and not stop.tour_end
                and _is_final_depot_stop_in_visit(
                    instance,
                    solution,
                    route_index,
                    stop_index,
                    stop_node_id(instance, solution, stop),
                )
                and _is_intermediate_depot_visit(
                    instance, solution, route_index, stop_index
                )
            ):
                targets.append(_ExistingVisitTarget(route_index, stop_index))

        route_nodes = route_node_sequence(instance, solution, route)
        for leg_index, (origin, destination) in enumerate(
            zip(route_nodes[:-1], route_nodes[1:], strict=True)
        ):
            for depot_node_id in depot_nodes:
                if depot_node_id in {origin, destination}:
                    continue
                outbound = sample_loopless_paths(
                    instance,
                    origin,
                    depot_node_id,
                    random_source=context.random_source,
                    max_paths=path_limit,
                    max_arc_expansion_steps=expansion_limit,
                )
                if not outbound:
                    continue
                inbound = sample_loopless_paths(
                    instance,
                    depot_node_id,
                    destination,
                    random_source=context.random_source,
                    max_paths=path_limit,
                    max_arc_expansion_steps=expansion_limit,
                )
                if inbound:
                    targets.append(
                        _InsertedVisitTarget(
                            route_index,
                            leg_index,
                            depot_node_id,
                            outbound,
                            inbound,
                        )
                    )
    return tuple(targets)


def _merge_targets(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> tuple[tuple[int, int], ...]:
    depot_nodes = {item.node_id for item in instance.network_data.depots}
    return tuple(
        (route_index, stop_index)
        for route_index, route in enumerate(solution.routes)
        for stop_index, stop in enumerate(route.stops)
        if (
            stop_node_id(instance, solution, stop) in depot_nodes
            and stop.tour_end
            and _is_final_depot_stop_in_visit(
                instance,
                solution,
                route_index,
                stop_index,
                stop_node_id(instance, solution, stop),
            )
            and _is_intermediate_depot_visit(
                instance, solution, route_index, stop_index
            )
        )
    )


def _is_final_depot_stop_in_visit(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route_index: int,
    stop_index: int,
    node_id: str,
) -> bool:
    stops = solution.routes[route_index].stops
    return (
        stop_index + 1 == len(stops)
        or stop_node_id(instance, solution, stops[stop_index + 1]) != node_id
    )


def _is_intermediate_depot_visit(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route_index: int,
    stop_index: int,
) -> bool:
    route = solution.routes[route_index]
    stop = route.stops[stop_index]
    nodes = route_node_sequence(instance, solution, route)
    start_node, end_node = nodes[0], nodes[-1]
    stop_node = stop_node_id(instance, solution, stop)
    if stop_node == start_node:
        start_block_end = 0
        while (
            start_block_end + 1 < len(route.stops)
            and stop_node_id(instance, solution, route.stops[start_block_end + 1])
            == start_node
        ):
            start_block_end += 1
        if stop_index <= start_block_end:
            return False
    if stop_node == end_node:
        end_block_start = len(route.stops) - 1
        while (
            end_block_start > 0
            and stop_node_id(instance, solution, route.stops[end_block_start - 1])
            == end_node
        ):
            end_block_start -= 1
        if stop_index >= end_block_start:
            return False
    return True


def _insert_tour_boundary(
    solution: VrpSolution,
    target: _InsertedVisitTarget,
    outbound_path: tuple[str, ...],
    inbound_path: tuple[str, ...],
) -> VrpSolution:
    route = solution.routes[target.route_index]
    stops = (
        *route.stops[: target.leg_index],
        DepotOnlyStop(target.depot_node_id, 0.0, 0.0, 0.0, True),
        *route.stops[target.leg_index :],
    )
    legs = (
        *route.legs[: target.leg_index],
        RouteLeg(outbound_path),
        RouteLeg(inbound_path),
        *route.legs[target.leg_index + 1 :],
    )
    routes = list(solution.routes)
    routes[target.route_index] = replace(route, stops=stops, legs=legs)
    return replace(solution, routes=tuple(routes))


def _clear_tour_boundary(
    solution: VrpSolution, route_index: int, stop_index: int
) -> VrpSolution:
    route = solution.routes[route_index]
    stops = list(route.stops)
    stop = stops[stop_index]
    stops[stop_index] = replace(stop, tour_end=False)
    routes = list(solution.routes)
    routes[route_index] = replace(route, stops=tuple(stops))
    return replace(solution, routes=tuple(routes))


def _replace_route_stops(
    solution: VrpSolution, route_index: int, stops: tuple[RouteStop, ...]
) -> VrpSolution:
    route = solution.routes[route_index]
    routes = list(solution.routes)
    routes[route_index] = replace(route, stops=stops)
    return replace(solution, routes=tuple(routes))


def _append_distinct(candidates: list[VrpSolution], candidate: VrpSolution) -> None:
    if all(candidate != existing for existing in candidates):
        candidates.append(candidate)


def _choose_candidate(
    candidates: tuple[_ScoredCandidate, ...],
    objectives: ObjectiveDeclaration,
    objective_index: int | None,
    context: CandidateContext,
) -> _ScoredCandidate:
    if objective_index is None:
        return choose_uniformly(candidates, context.random_source)
    objective = objectives.objectives[objective_index]
    best = candidates[0]
    tied = [best]
    for candidate in candidates[1:]:
        comparison = objective.compare(
            candidate[1][objective_index], best[1][objective_index]
        )
        if comparison is ObjectiveComparison.LESS:
            best = candidate
            tied = [candidate]
        elif comparison is ObjectiveComparison.EQUAL:
            tied.append(candidate)
    return choose_uniformly(tied, context.random_source)


def _bounded_limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
