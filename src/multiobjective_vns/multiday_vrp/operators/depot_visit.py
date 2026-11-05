"""Depot-visit insertion and removal Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
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
from multiobjective_vns.multiday_vrp.solution import DepotOnlyStop, VrpSolution
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
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
        working = base
        modifications = 0
        for _ in range(size):
            targets = self._targets(working, context)
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            candidates = (
                self._insertion_candidates(working, target, context)
                if self._mode == 'insert'
                else self._removal_candidates(working, target, context)
            )
            if not candidates:
                continue
            working = self._choose(candidates, context)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _targets(
        self, solution: VrpSolution, context: CandidateContext
    ) -> tuple[_VisitTarget, ...]:
        if self._mode == 'remove':
            depot_nodes = {item.node_id for item in self._instance.network_data.depots}
            return tuple(
                (route_index, stop_index, stop.node_id)
                for route_index, route in enumerate(solution.routes)
                for stop_index, stop in enumerate(route.stops)
                if type(stop) is DepotOnlyStop
                and stop.node_id in depot_nodes
                and _is_intermediate_visit(
                    self._instance, solution, route_index, stop_index
                )
            )
        targets: list[_VisitTarget] = []
        depot_node_ids = tuple(
            sorted({item.node_id for item in self._instance.network_data.depots})
        )
        for route_index, route in enumerate(solution.routes):
            nodes = route_node_sequence(self._instance, solution, route)
            for leg_index, (origin, destination) in enumerate(
                zip(nodes[:-1], nodes[1:], strict=True)
            ):
                for node_id in depot_node_ids:
                    if node_id == origin or node_id == destination:
                        continue
                    outbound = sample_loopless_paths(
                        self._instance,
                        origin,
                        node_id,
                        random_source=context.random_source,
                        max_paths=1,
                        max_arc_expansion_steps=self._expansion_limit,
                    )
                    inbound = sample_loopless_paths(
                        self._instance,
                        node_id,
                        destination,
                        random_source=context.random_source,
                        max_paths=1,
                        max_arc_expansion_steps=self._expansion_limit,
                    )
                    if outbound and inbound:
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
            while block_start >= 0 and _same_depot_node(stops[block_start], node_id):
                block_start -= 1
            block_end = stop_index
            while block_end < len(stops) and _same_depot_node(
                stops[block_end], node_id
            ):
                block_end += 1
            if block_end > block_start + 1:
                last_stop = block_end - 1
                if type(stops[last_stop]) is DepotOnlyStop:
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


def _is_intermediate_visit(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route_index: int,
    stop_index: int,
) -> bool:
    route = solution.routes[route_index]
    nodes = route_node_sequence(instance, solution, route)
    start_node, end_node = nodes[0], nodes[-1]
    current = route.stops[stop_index]
    if type(current) is not DepotOnlyStop:
        return False
    start_block_end = 0
    while (
        start_block_end + 1 < len(route.stops)
        and stop_node_id(instance, solution, route.stops[start_block_end + 1])
        == start_node
    ):
        start_block_end += 1
    end_block_start = len(route.stops) - 1
    while (
        end_block_start > 0
        and stop_node_id(instance, solution, route.stops[end_block_start - 1])
        == end_node
    ):
        end_block_start -= 1
    if current.node_id == start_node and stop_index <= start_block_end:
        return False
    if current.node_id == end_node and stop_index >= end_block_start:
        return False
    return True


def _same_depot_node(stop: object, node_id: str) -> bool:
    return type(stop) is DepotOnlyStop and stop.node_id == node_id


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
