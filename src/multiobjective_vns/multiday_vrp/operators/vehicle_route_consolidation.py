"""Vehicle-route consolidation Operator.

Created: 2026-11-04T19:12:49+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    operator_result,
    randomized_order,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    assess_complete_candidate,
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    route_node_sequence,
    stop_node_id,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_assignment_helpers import (
    _append_unique,
    _bounded_limit,
    _choose_scored,
    _rebase_service_waits,
    _rebuild_paths,
    _sample_interleavings,
    _ScoredPlan,
    _sort_routes,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteStop,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.values import require_integer

__all__ = ['VehicleRouteConsolidationOperator']

type _RoutePair = tuple[int, int]


class VehicleRouteConsolidationOperator:
    """Move one same-day vehicle route into another and remove its route record.

    :param instance: Validated VRP instance.
    :param objective_declaration: Ordered objectives for complete-plan ranking.
    :param assess_plan: Shared complete-plan assessment callback.
    :param max_neighborhood_size: Largest supported value of ``k``.
    :param objective_id: Optional objective used to rank alternatives.
    :param max_candidate_alternatives: Maximum complete alternatives assessed.
    :param max_paths_per_leg: Maximum sampled paths for a route leg.
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
        """Apply up to ``k`` route consolidations sequentially.

        :param context: Base plan, requested size, feasibility request, and RNG.
        :returns: Changed plan and number of removed source routes, or ``m=0``.
        :raises TypeError: If a base or complete assessment is malformed.
        :raises ValueError: If ``k`` exceeds the registered maximum.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        if context.ensure_feasible and not self._is_eligible(base, True):
            return operator_result(None, modifications=0, requested_size=size)
        working = base
        modifications = 0
        for _ in range(size):
            pairs = randomized_order(
                _ordered_route_pairs(working), context.random_source
            )
            if not pairs:
                break
            pair_count = min(len(pairs), self._candidate_limit)
            base_quota, extra_quota = divmod(self._candidate_limit, pair_count)
            candidates: list[VrpSolution] = []
            for pair_index, pair in enumerate(pairs[:pair_count]):
                pair_limit = base_quota + (pair_index < extra_quota)
                for candidate in self._consolidation_candidates(
                    working,
                    pair,
                    context,
                    candidate_limit=pair_limit,
                ):
                    _append_unique(candidates, candidate)
            assessed = self._assess_candidates(candidates, context)
            if not assessed:
                break
            working = self._choose(assessed, context)[0]
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _consolidation_candidates(
        self,
        base: VrpSolution,
        pair: _RoutePair,
        context: CandidateContext,
        *,
        candidate_limit: int,
    ) -> tuple[VrpSolution, ...]:
        source_index, destination_index = pair
        source = base.routes[source_index]
        destination = base.routes[destination_index]
        destination_start = route_node_sequence(self._instance, base, destination)[0]
        source_pickups, source_remaining = _initial_pickup_block(
            self._instance, base, source, destination_start
        )
        destination_pickups, destination_remaining = _initial_pickup_block(
            self._instance, base, destination, destination_start
        )
        pickup_block = (*destination_pickups, *source_pickups)
        interleavings = _sample_interleavings(
            destination_remaining,
            source_remaining,
            candidate_limit,
            context,
        )
        candidates: list[VrpSolution] = []
        for interleaving in interleavings:
            merged_stops = (*pickup_block, *interleaving)
            provisional = _remove_route_and_replace(
                base,
                source_index,
                destination_index,
                replace(destination, stops=tuple(merged_stops)),
                self._instance,
            )
            with_paths = _rebuild_paths(
                self._instance,
                base,
                provisional,
                context,
                max_paths=self._path_limit,
                max_expansions=self._expansion_limit,
            )
            if with_paths is None:
                continue
            rebased = _rebase_service_waits(
                self._instance,
                self._objectives,
                base,
                with_paths,
                context,
                self._assess_plan,
            )
            _append_unique(candidates, rebased)
            if len(candidates) >= candidate_limit:
                break
        return tuple(candidates)

    def _assess_candidates(
        self, candidates: Sequence[VrpSolution], context: CandidateContext
    ) -> tuple[_ScoredPlan, ...]:
        assessed: list[_ScoredPlan] = []
        for candidate in candidates[: self._candidate_limit]:
            evaluation = assess_complete_candidate(
                self._instance,
                self._objectives,
                candidate,
                self._assess_plan,
                ensure_feasible=context.ensure_feasible,
            )
            if evaluation is not None and evaluation.objective_values is not None:
                assessed.append((candidate, evaluation.objective_values))
        return tuple(assessed)

    def _is_eligible(self, candidate: VrpSolution, ensure_feasible: bool) -> bool:
        return (
            assess_complete_candidate(
                self._instance,
                self._objectives,
                candidate,
                self._assess_plan,
                ensure_feasible=ensure_feasible,
            )
            is not None
        )

    def _choose(
        self, candidates: Sequence[_ScoredPlan], context: CandidateContext
    ) -> _ScoredPlan:
        return _choose_scored(
            candidates,
            self._objectives,
            self._objective_position,
            context,
        )


def _ordered_route_pairs(solution: VrpSolution) -> tuple[_RoutePair, ...]:
    return tuple(
        (source_index, destination_index)
        for source_index, source in enumerate(solution.routes)
        for destination_index, destination in enumerate(solution.routes)
        if source_index != destination_index
        and source.day_index == destination.day_index
        and source.vehicle_id != destination.vehicle_id
    )


def _initial_pickup_block(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route: VehicleDayRoute,
    start_node_id: str,
) -> tuple[tuple[RouteStop, ...], tuple[RouteStop, ...]]:
    pickups: list[RouteStop] = []
    remaining: list[RouteStop] = []
    for stop in route.stops:
        if (
            isinstance(stop, ActivityStop)
            and stop.activity_ref.kind == 'pickup'
            and stop_node_id(instance, solution, stop) == start_node_id
        ):
            pickups.append(stop)
        else:
            remaining.append(stop)
    return tuple(pickups), tuple(remaining)


def _remove_route_and_replace(
    solution: VrpSolution,
    source_index: int,
    destination_index: int,
    destination: VehicleDayRoute,
    instance: LoadedVrpInstance,
) -> VrpSolution:
    routes = [
        replace(route, stops=destination.stops) if index == destination_index else route
        for index, route in enumerate(solution.routes)
        if index != source_index
    ]
    return _sort_routes(replace(solution, routes=tuple(routes)), instance)
