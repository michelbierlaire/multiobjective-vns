"""Generic directed route-path replacement Operators.

Created: 2026-11-03T20:52:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, replace
from functools import cmp_to_key
from math import inf, isfinite
from typing import Protocol

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
from multiobjective_vns.multiday_vrp.operators.path_search import (
    route_node_sequence,
    sample_loopless_paths,
)
from multiobjective_vns.multiday_vrp.solution import RouteLeg, VrpSolution
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = [
    'AdditivePathCostCallback',
    'AdditivePathCostRegistration',
    'AdditivePathCostRequest',
    'ObjectiveSpecificShortestPathOperator',
    'RouteLegRerouteOperator',
]


@dataclass(frozen=True, slots=True)
class AdditivePathCostRequest:
    """Immutable context for one objective-specific arc-cost query.

    :param objective_id: Objective optimized by the path choice.
    :param plan: Complete working plan before the leg replacement.
    :param vehicle_id: Route vehicle.
    :param day_index: One-based route day.
    :param leg_index: Zero-based route-leg position.
    :param origin_node_id: Leg's physical origin node.
    :param destination_node_id: Leg's physical destination node.
    """

    objective_id: ObjectiveId
    plan: VrpSolution
    vehicle_id: str
    day_index: int
    leg_index: int
    origin_node_id: str
    destination_node_id: str


class AdditivePathCostCallback(Protocol):
    """Application callback returning a static nonnegative cost for one arc."""

    def __call__(self, arc_id: str, request: AdditivePathCostRequest) -> float:
        """Return this arc's additive contribution to the requested objective."""


@dataclass(frozen=True, slots=True)
class AdditivePathCostRegistration:
    """Identify a deterministic additive arc-cost callback for one objective."""

    provider_id: str
    configuration_id: str
    callback: AdditivePathCostCallback

    def __post_init__(self) -> None:
        for name in ('provider_id', 'configuration_id'):
            value = getattr(self, name)
            if type(value) is not str or not value or value.strip() != value:
                raise ValueError(f'{name} must be a nonempty unpadded string')
        if not callable(self.callback):
            raise TypeError('callback must be callable')


class RouteLegRerouteOperator:
    """Replace sampled route-leg paths, optionally ranking by one objective."""

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_id',
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
        self._objective_id = objective_id
        self._maximum_size = _positive_integer(
            max_neighborhood_size, 'max_neighborhood_size'
        )
        self._candidate_limit = _bounded_limit(
            max_candidate_alternatives, 'max_candidate_alternatives', maximum=32
        )
        self._path_limit = _bounded_limit(
            max_paths_per_leg, 'max_paths_per_leg', maximum=32
        )
        self._expansion_limit = _bounded_limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', maximum=10000
        )
        if objective_id is not None:
            _objective_index(objective_declaration, objective_id)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Replace up to ``k`` distinct paths and return ranked alternatives.

        :param context: Base plan, requested size, feasibility, and shared RNG.
        :returns: Up to ``context.max_candidates`` feasible plans in random or
            objective-ranked order, each with its changed-leg count.
        :raises TypeError: If the base or evaluation callback is malformed.
        :raises ValueError: If size or a callback result violates its contract.
        """
        return _run_path_search(
            self._instance,
            self._objectives,
            self._assess_plan,
            context,
            max_neighborhood_size=self._maximum_size,
            max_candidate_alternatives=self._candidate_limit,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_returned_candidates=context.max_candidates,
            objective_id=self._objective_id,
            additive_path_cost=None,
        )


class ObjectiveSpecificShortestPathOperator:
    """Select a path that improves a declared objective on one route leg."""

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_id',
        '_additive_path_cost',
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
        additive_path_cost: AdditivePathCostCallback | None,
        max_neighborhood_size: int,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._objective_id = objective_id
        self._additive_path_cost = additive_path_cost
        if additive_path_cost is not None and not callable(additive_path_cost):
            raise TypeError('additive_path_cost must be callable or None')
        self._maximum_size = _positive_integer(
            max_neighborhood_size, 'max_neighborhood_size'
        )
        self._candidate_limit = _bounded_limit(
            max_candidate_alternatives, 'max_candidate_alternatives', maximum=32
        )
        self._path_limit = _bounded_limit(
            max_paths_per_leg, 'max_paths_per_leg', maximum=32
        )
        self._expansion_limit = _bounded_limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', maximum=10000
        )
        _objective_index(objective_declaration, objective_id)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Replace up to ``k`` distinct legs with objective-focused paths.

        :param context: Base plan, requested size, feasibility, and shared RNG.
        :returns: Up to ``context.max_candidates`` ranked feasible plans, or
            an empty candidate list when no requested move is feasible.
        :raises TypeError: If the base or evaluation callback is malformed.
        :raises ValueError: If size, objective values, or arc costs are invalid.
        """
        return _run_path_search(
            self._instance,
            self._objectives,
            self._assess_plan,
            context,
            max_neighborhood_size=self._maximum_size,
            max_candidate_alternatives=self._candidate_limit,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_returned_candidates=context.max_candidates,
            objective_id=self._objective_id,
            additive_path_cost=self._additive_path_cost,
        )


def _run_path_search(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    context: CandidateContext,
    *,
    max_neighborhood_size: int,
    max_candidate_alternatives: int,
    max_paths_per_leg: int,
    max_arc_expansion_steps: int,
    max_returned_candidates: int,
    objective_id: ObjectiveId | None,
    additive_path_cost: AdditivePathCostCallback | None,
) -> OperatorResult:
    size = validate_neighborhood_size(context.k, max_neighborhood_size)
    base = require_vrp_base(context)

    working = base
    remaining_legs = [
        (route_index, leg_index)
        for route_index, route in enumerate(base.routes)
        for leg_index in range(len(route.legs))
    ]
    modifications = 0
    latest_candidates: tuple[VrpSolution, ...] = ()
    for _ in range(size):
        if not remaining_legs:
            break
        leg_key = choose_uniformly(remaining_legs, context.random_source)
        remaining_legs.remove(leg_key)
        candidates = _leg_candidates(
            instance,
            objectives,
            assess_plan,
            working,
            leg_key,
            context,
            objective_id=objective_id,
            additive_path_cost=additive_path_cost,
            max_candidate_alternatives=max_candidate_alternatives,
            max_paths_per_leg=max_paths_per_leg,
            max_arc_expansion_steps=max_arc_expansion_steps,
            max_returned_candidates=max_returned_candidates,
        )
        if not candidates:
            continue
        working = candidates[0]
        latest_candidates = candidates
        modifications += 1
    if not modifications:
        return operator_result(None, modifications=0, requested_size=size)
    return OperatorResult(
        candidates=tuple(
            CandidateProposal(Candidate(candidate), modifications)
            for candidate in latest_candidates
        )
    )


def _leg_candidates(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    working: VrpSolution,
    leg_key: tuple[int, int],
    context: CandidateContext,
    *,
    objective_id: ObjectiveId | None,
    additive_path_cost: AdditivePathCostCallback | None,
    max_candidate_alternatives: int,
    max_paths_per_leg: int,
    max_arc_expansion_steps: int,
    max_returned_candidates: int,
) -> tuple[VrpSolution, ...]:
    route_index, leg_index = leg_key
    route = working.routes[route_index]
    endpoints = route_node_sequence(instance, working, route)
    origin = endpoints[leg_index]
    destination = endpoints[leg_index + 1]
    paths: tuple[tuple[str, ...], ...]
    if additive_path_cost is not None:
        if objective_id is None:
            raise ValueError('additive path costs require an objective ID')
        exact_path = _dijkstra_path(
            instance,
            origin,
            destination,
            additive_path_cost,
            AdditivePathCostRequest(
                objective_id=objective_id,
                plan=working,
                vehicle_id=route.vehicle_id,
                day_index=route.day_index,
                leg_index=leg_index,
                origin_node_id=origin,
                destination_node_id=destination,
            ),
            random_source=context.random_source,
            max_arc_expansion_steps=max_arc_expansion_steps,
        )
        paths = () if exact_path is None else (exact_path,)
    else:
        paths = sample_loopless_paths(
            instance,
            origin,
            destination,
            random_source=context.random_source,
            max_paths=max_paths_per_leg,
            max_arc_expansion_steps=max_arc_expansion_steps,
        )
    current_path = route.legs[leg_index].arc_ids
    paths = tuple(path for path in paths if path != current_path)
    sampled_paths = randomized_sample(
        paths, max_candidate_alternatives, context.random_source
    )
    candidates: list[tuple[VrpSolution, ApplicationEvaluation]] = []
    for path in sampled_paths:
        legs = list(route.legs)
        legs[leg_index] = RouteLeg(path)
        routes = list(working.routes)
        routes[route_index] = replace(route, legs=tuple(legs))
        candidate = replace(working, routes=tuple(routes))
        evaluation = _assess_candidate(
            instance,
            objectives,
            assess_plan,
            candidate,
            ensure_feasible=context.ensure_feasible,
        )
        if evaluation is not None:
            candidates.append((candidate, evaluation))
    if objective_id is None or not candidates:
        return randomized_sample(
            tuple(candidate for candidate, _ in candidates),
            max_returned_candidates,
            context.random_source,
        )
    objective_index = _objective_index(objectives, objective_id)
    objective = objectives.objectives[objective_index]
    scored: list[tuple[VrpSolution, object]] = []
    for candidate, evaluation in candidates:
        if evaluation.objective_values is None:
            raise ValueError('valid candidate assessment requires objective values')
        scored.append((candidate, evaluation.objective_values[objective_index]))

    if max_returned_candidates == 1:
        best_value = scored[0][1]
        best_candidates = [scored[0][0]]
        for candidate, value in scored[1:]:
            comparison = objective.compare(value, best_value)
            if comparison is ObjectiveComparison.LESS:
                best_value = value
                best_candidates = [candidate]
            elif comparison is ObjectiveComparison.EQUAL:
                best_candidates.append(candidate)
        return (choose_uniformly(best_candidates, context.random_source),)

    def compare_scored(
        left: tuple[VrpSolution, object], right: tuple[VrpSolution, object]
    ) -> int:
        comparison = objective.compare(left[1], right[1])
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    ordered = sorted(scored, key=cmp_to_key(compare_scored))
    ranked_candidates: list[VrpSolution] = []
    group_start = 0
    while (
        group_start < len(ordered) and len(ranked_candidates) < max_returned_candidates
    ):
        group_end = group_start + 1
        while (
            group_end < len(ordered)
            and objective.compare(ordered[group_start][1], ordered[group_end][1])
            is ObjectiveComparison.EQUAL
        ):
            group_end += 1
        ranked_candidates.extend(
            randomized_sample(
                tuple(candidate for candidate, _ in ordered[group_start:group_end]),
                max_returned_candidates - len(ranked_candidates),
                context.random_source,
            )
        )
        group_start = group_end
    return tuple(ranked_candidates[:max_returned_candidates])


def _assess_candidate(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    solution: VrpSolution,
    *,
    ensure_feasible: bool,
) -> ApplicationEvaluation | None:
    evaluation = assess_plan(instance, objectives, solution)
    if not isinstance(evaluation, ApplicationEvaluation):
        raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
    if not evaluation.representation_valid:
        return None
    if evaluation.objective_values is None:
        raise ValueError('valid VRP assessment requires an objective vector')
    if not isinstance(evaluation.objective_values, tuple):
        raise TypeError('valid VRP assessment objective_values must be a tuple')
    if len(evaluation.objective_values) != len(objectives.objectives):
        raise ValueError('objective vector length must match the declaration')
    if ensure_feasible and evaluation.feasible is not True:
        return None
    return evaluation


def _dijkstra_path(
    instance: LoadedVrpInstance,
    origin: str,
    destination: str,
    callback: AdditivePathCostCallback,
    request: AdditivePathCostRequest,
    *,
    random_source: RandomSource,
    max_arc_expansion_steps: int,
) -> tuple[str, ...] | None:
    """Find one exact minimum-cost path, randomizing equal-cost arc order."""
    expansion_limit = require_integer(
        max_arc_expansion_steps,
        name='max_arc_expansion_steps',
        minimum=1,
    )
    if origin == destination:
        return ()
    outgoing: dict[str, list[tuple[str, str]]] = {}
    for arc in instance.network_data.arcs:
        outgoing.setdefault(arc.tail_node_id, []).append((arc.arc_id, arc.head_node_id))
    ordered_outgoing: dict[str, tuple[tuple[str, str], ...]] = {}
    distances = {origin: 0.0}
    parents: dict[str, tuple[str, str]] = {}
    queue: list[tuple[float, int, str]] = [(0.0, 0, origin)]
    sequence = 1
    expansions = 0
    reached = False
    while queue and expansions < expansion_limit:
        distance, _, node = heapq.heappop(queue)
        if distance != distances.get(node):
            continue
        if node == destination:
            reached = True
            break
        if node not in ordered_outgoing:
            ordered_outgoing[node] = randomized_order(
                tuple(sorted(outgoing.get(node, ()))), random_source
            )
        for arc_id, head in ordered_outgoing[node]:
            if expansions >= expansion_limit:
                break
            expansions += 1
            cost = callback(arc_id, request)
            if type(cost) is not float:
                raise TypeError('additive path costs must be Python floats')
            if not isfinite(cost) or cost < 0.0:
                raise ValueError('additive path costs must be finite and nonnegative')
            candidate = distance + cost
            previous = distances.get(head, inf)
            if candidate < previous:
                distances[head] = candidate
                parents[head] = (node, arc_id)
                heapq.heappush(queue, (candidate, sequence, head))
                sequence += 1
    if not reached:
        return None
    path: list[str] = []
    current = destination
    while current != origin:
        parent = parents.get(current)
        if parent is None:
            return None
        current, arc_id = parent
        path.append(arc_id)
    return tuple(reversed(path))


def _objective_index(
    objectives: ObjectiveDeclaration, objective_id: ObjectiveId
) -> int:
    for index, objective in enumerate(objectives.objectives):
        if objective.objective_id == objective_id:
            return index
    raise ValueError(f'unknown ObjectiveId {objective_id!r}')


def _positive_integer(value: int, name: str) -> int:
    return require_integer(value, name=name, minimum=1)


def _bounded_limit(value: int, name: str, *, maximum: int) -> int:
    normalized = _positive_integer(value, name)
    if normalized > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return normalized
