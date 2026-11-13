"""Depot-visit insertion and removal Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from functools import cmp_to_key
from typing import Literal

from multiobjective_vns.identifiers import ObjectiveId
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
    rebuild_changed_routes,
    route_node_sequence,
    sample_loopless_paths,
    stop_node_id,
)
from multiobjective_vns.multiday_vrp.solution import (
    DepotOnlyStop,
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

type DepotVisitMode = Literal['insert', 'remove']
type _VisitTarget = tuple[int, int, str]

__all__ = ['DepotVisitMode', 'DepotVisitOperator']


class DepotVisitOperator:
    """Insert or remove an intermediate depot stop and rebuild route paths."""

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
        mode: DepotVisitMode,
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        if mode not in {'insert', 'remove'}:
            raise ValueError(f'unsupported depot visit mode: {mode!r}')
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
        """Apply up to ``k`` depot-stop insertion or removal units."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        if self._mode == 'insert':
            return self._call_insertion(base, size, context)
        return self._call_removal(base, size, context)

    def _call_removal(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        working = base
        modifications = 0
        last_candidates: tuple[tuple[VrpSolution, tuple[object, ...]], ...] = ()
        for _ in range(size):
            targets = self._targets(working, context)
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            candidates = self._removal_candidates(working, target, context)
            if not candidates:
                continue
            last_candidates = _order_candidates(
                candidates,
                self._objectives,
                self._objective_position,
                context,
            )
            working = last_candidates[0][0]
            modifications += 1
        if not modifications:
            return operator_result(None, modifications=0, requested_size=size)
        proposals = tuple(
            CandidateProposal(Candidate(solution), modifications)
            for solution, _values in last_candidates[: context.max_candidates]
        )
        return OperatorResult(candidates=proposals)

    def _call_insertion(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        working = base
        modifications = 0
        last_candidates: tuple[tuple[VrpSolution, tuple[object, ...]], ...] = ()
        for _ in range(size):
            targets = self._targets(working, context)
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            candidates = self._insertion_candidates(working, target, context)
            if not candidates:
                continue
            last_candidates = _order_candidates(
                candidates,
                self._objectives,
                self._objective_position,
                context,
            )
            working = last_candidates[0][0]
            modifications += 1
        if not modifications:
            return operator_result(None, modifications=0, requested_size=size)
        proposals = tuple(
            CandidateProposal(Candidate(solution), modifications)
            for solution, _values in last_candidates[: context.max_candidates]
        )
        return OperatorResult(candidates=proposals)

    def _targets(
        self, solution: VrpSolution, context: CandidateContext
    ) -> tuple[_VisitTarget, ...]:
        if self._mode == 'remove':
            depot_nodes = {item.node_id for item in self._instance.network_data.depots}
            removal_path_cache: dict[tuple[str, str], bool] = {}

            def removal_has_path(origin: str, destination: str) -> bool:
                endpoints = (origin, destination)
                if endpoints not in removal_path_cache:
                    removal_path_cache[endpoints] = bool(
                        sample_loopless_paths(
                            self._instance,
                            origin,
                            destination,
                            random_source=context.random_source,
                            max_paths=1,
                            max_arc_expansion_steps=self._expansion_limit,
                        )
                    )
                return removal_path_cache[endpoints]

            removal_targets: list[_VisitTarget] = []
            for route_index, route in enumerate(solution.routes):
                nodes = route_node_sequence(self._instance, solution, route)
                stop_nodes = tuple(
                    stop_node_id(self._instance, solution, stop) for stop in route.stops
                )
                start_node, end_node = nodes[0], nodes[-1]
                start_block_end = 0
                while (
                    start_block_end + 1 < len(stop_nodes)
                    and stop_nodes[start_block_end + 1] == start_node
                ):
                    start_block_end += 1
                end_block_start = len(stop_nodes) - 1
                while (
                    end_block_start > 0 and stop_nodes[end_block_start - 1] == end_node
                ):
                    end_block_start -= 1

                for stop_index, stop in enumerate(route.stops):
                    if (
                        type(stop) is not DepotOnlyStop
                        or stop.node_id not in depot_nodes
                    ):
                        continue
                    if (
                        stop.node_id == start_node and stop_index <= start_block_end
                    ) or (stop.node_id == end_node and stop_index >= end_block_start):
                        continue
                    if removal_has_path(nodes[stop_index], nodes[stop_index + 2]):
                        removal_targets.append((route_index, stop_index, stop.node_id))
            return tuple(removal_targets)
        targets: list[_VisitTarget] = []
        depot_node_ids = tuple(
            sorted({item.node_id for item in self._instance.network_data.depots})
        )
        path_cache: dict[tuple[str, str], bool] = {}

        def has_path(origin: str, destination: str) -> bool:
            endpoints = (origin, destination)
            if endpoints not in path_cache:
                path_cache[endpoints] = bool(
                    sample_loopless_paths(
                        self._instance,
                        origin,
                        destination,
                        random_source=context.random_source,
                        max_paths=1,
                        max_arc_expansion_steps=self._expansion_limit,
                    )
                )
            return path_cache[endpoints]

        for route_index, route in enumerate(solution.routes):
            nodes = route_node_sequence(self._instance, solution, route)
            for leg_index, (origin, destination) in enumerate(
                zip(nodes[:-1], nodes[1:], strict=True)
            ):
                for node_id in depot_node_ids:
                    if node_id == origin or node_id == destination:
                        continue
                    if has_path(origin, node_id) and has_path(node_id, destination):
                        targets.append((route_index, leg_index, node_id))
        return tuple(targets)

    def _insertion_candidates(
        self,
        solution: VrpSolution,
        target: _VisitTarget,
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
        route_index, leg_index, node_id = target
        stops = list(solution.routes[route_index].stops)
        stops.insert(
            leg_index,
            DepotOnlyStop(node_id, 0.0, 0.0, 0.0, False),
        )
        rebuilt = rebuild_changed_routes(
            self._instance,
            solution,
            {route_index: tuple(stops)},
            random_source=context.random_source,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_candidate_alternatives=self._candidate_limit,
        )
        return self._assess_candidates(rebuilt, context)

    def _removal_candidates(
        self,
        solution: VrpSolution,
        target: _VisitTarget,
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
        route_index, stop_index, node_id = target
        route = solution.routes[route_index]
        stops = list(route.stops)
        removed = stops.pop(stop_index)
        if type(removed) is not DepotOnlyStop:
            return ()
        if removed.tour_end:
            block_start = stop_index - 1
            while block_start >= 0 and _same_route_node(
                self._instance, solution, stops[block_start], node_id
            ):
                block_start -= 1
            block_end = stop_index
            while block_end < len(stops) and _same_route_node(
                self._instance, solution, stops[block_end], node_id
            ):
                block_end += 1
            if block_end > block_start + 1:
                last_stop = block_end - 1
                stops[last_stop] = replace(stops[last_stop], tour_end=True)
        rebuilt = rebuild_changed_routes(
            self._instance,
            solution,
            {route_index: tuple(stops)},
            random_source=context.random_source,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_candidate_alternatives=self._candidate_limit,
        )
        return self._assess_candidates(rebuilt, context)

    def _assess_candidates(
        self,
        candidates: tuple[VrpSolution, ...],
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
        eligible: list[tuple[VrpSolution, tuple[object, ...]]] = []
        for candidate in candidates:
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
            eligible.append((candidate, evaluation.objective_values))
        return tuple(eligible)


def _same_route_node(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    stop: RouteStop,
    node_id: str,
) -> bool:
    return stop_node_id(instance, solution, stop) == node_id


def _order_candidates(
    candidates: tuple[tuple[VrpSolution, tuple[object, ...]], ...],
    objectives: ObjectiveDeclaration,
    objective_index: int | None,
    context: CandidateContext,
) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
    if objective_index is None:
        return randomized_order(candidates, context.random_source)

    objective = objectives.objectives[objective_index]

    def compare(
        left: tuple[VrpSolution, tuple[object, ...]],
        right: tuple[VrpSolution, tuple[object, ...]],
    ) -> int:
        comparison = objective.compare(
            left[1][objective_index], right[1][objective_index]
        )
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    ranked = sorted(candidates, key=cmp_to_key(compare))
    ordered: list[tuple[VrpSolution, tuple[object, ...]]] = []
    group_start = 0
    while group_start < len(ranked):
        group_end = group_start + 1
        while (
            group_end < len(ranked)
            and compare(ranked[group_start], ranked[group_end]) == 0
        ):
            group_end += 1
        ordered.extend(
            randomized_order(ranked[group_start:group_end], context.random_source)
        )
        group_start = group_end
    return tuple(ordered)


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
