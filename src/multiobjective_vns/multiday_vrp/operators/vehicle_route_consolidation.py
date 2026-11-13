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
from multiobjective_vns.multiday_vrp.operators.vehicle_elimination import (
    _relationship_groups,
    _service_units,
    _stop_unit_key,
    _UnitKey,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_insertion_search import (
    InsertionStrategy,
    build_insertion_beam,
    nondominated_scored_plans,
    target_first_nondominated_scored_plans,
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
    :param insertion_strategy: Best insertion or regret insertion.
    :param max_candidate_alternatives: Per-unit proposal limit and beam width.
    :param max_paths_per_leg: Maximum sampled paths for a route leg.
    :param max_arc_expansion_steps: Search bound for each sampled path.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_position',
        '_insertion_strategy',
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
        insertion_strategy: InsertionStrategy = 'best',
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
        if insertion_strategy not in {'best', 'regret'}:
            raise ValueError("insertion_strategy must be 'best' or 'regret'")
        self._insertion_strategy = insertion_strategy
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
            route_units = {
                source_index: units
                for source_index in range(len(working.routes))
                if (units := _route_repair_units(self._instance, working, source_index))
            }
            pairs = tuple(
                pair for pair in _ordered_route_pairs(working) if pair[0] in route_units
            )
            if self._instance.settings.exact_active_vehicle_count is not None:
                pairs = tuple(
                    pair
                    for pair in pairs
                    if _vehicle_remains_active_on_another_day(
                        working, working.routes[pair[0]]
                    )
                )
            pairs = randomized_order(pairs, context.random_source)
            if not pairs:
                break
            scored: list[_ScoredPlan] = []
            for pair in pairs[: self._candidate_limit]:
                scored.extend(
                    self._consolidation_candidates(
                        working,
                        pair,
                        route_units[pair[0]],
                        context,
                    )
                )
            assessed = _unique_scored_plans(scored)
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
        repair_units: tuple[tuple[_UnitKey, ...], ...],
        context: CandidateContext,
    ) -> tuple[_ScoredPlan, ...]:
        def propose(
            plan: VrpSolution,
            unit: tuple[_UnitKey, ...],
            proposal_context: CandidateContext,
        ) -> tuple[VrpSolution, ...]:
            return self._consolidation_placements(
                base,
                plan,
                pair,
                unit,
                proposal_context,
            )

        def assess(plan: VrpSolution) -> tuple[object, ...] | None:
            evaluation = assess_complete_candidate(
                self._instance,
                self._objectives,
                plan,
                self._assess_plan,
                ensure_feasible=context.ensure_feasible,
            )
            return None if evaluation is None else evaluation.objective_values

        return build_insertion_beam(
            base,
            repair_units,
            propose,
            assess,
            self._objectives,
            objective_position=self._objective_position,
            strategy=self._insertion_strategy,
            context=context,
            candidate_limit=self._candidate_limit,
        )

    def _consolidation_placements(
        self,
        base: VrpSolution,
        plan: VrpSolution,
        pair: _RoutePair,
        repair_unit: tuple[_UnitKey, ...],
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        source_index, destination_index = pair
        if source_index >= len(plan.routes) or destination_index >= len(plan.routes):
            return ()
        source = plan.routes[source_index]
        destination = plan.routes[destination_index]
        unit_keys = set(repair_unit)
        unit_stops = tuple(
            replace(stop, tour_end=False)
            for stop in source.stops
            if _stop_unit_key(stop) in unit_keys
        )
        if not unit_stops:
            return ()

        destination_start = route_node_sequence(self._instance, plan, destination)[0]
        source_pickups, source_remaining = _start_pickups(
            self._instance,
            plan,
            unit_stops,
            destination_start,
        )
        destination_pickups, destination_remaining = _start_pickups(
            self._instance,
            plan,
            destination.stops,
            destination_start,
        )
        pickup_block = (*destination_pickups, *source_pickups)
        interleavings = _sample_interleavings(
            destination_remaining,
            source_remaining,
            self._candidate_limit,
            context,
        )
        candidates: list[VrpSolution] = []
        for interleaving in interleavings:
            remaining_source_stops = tuple(
                stop for stop in source.stops if _stop_unit_key(stop) not in unit_keys
            )
            merged_stops = (*pickup_block, *interleaving)
            updated_destination = replace(destination, stops=tuple(merged_stops))
            updated_routes: list[VehicleDayRoute] = []
            for route_index, route in enumerate(plan.routes):
                if route_index == source_index:
                    if any(
                        _stop_unit_key(stop) is not None
                        for stop in remaining_source_stops
                    ):
                        updated_routes.append(
                            replace(route, stops=remaining_source_stops)
                        )
                elif route_index == destination_index:
                    updated_routes.append(updated_destination)
                else:
                    updated_routes.append(route)
            provisional = _sort_routes(
                replace(plan, routes=tuple(updated_routes)),
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
        return tuple(candidates)

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
        if self._objective_position is None:
            candidates = nondominated_scored_plans(candidates, self._objectives)
        else:
            candidates = target_first_nondominated_scored_plans(
                candidates,
                self._objectives,
                objective_position=self._objective_position,
            )
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


def _vehicle_remains_active_on_another_day(
    solution: VrpSolution, source: VehicleDayRoute
) -> bool:
    return any(
        route.vehicle_id == source.vehicle_id and route.day_index != source.day_index
        for route in solution.routes
    )


def _route_repair_units(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    source_index: int,
) -> tuple[tuple[_UnitKey, ...], ...]:
    units = _service_units(solution)
    groups = _relationship_groups(instance, units)
    touched = tuple(
        group
        for group in groups
        if any(
            route_index == source_index
            for unit in group
            for route_index, _stop_index, _stop in unit.occurrences
        )
    )
    if not touched or any(
        route_index != source_index
        for group in touched
        for unit in group
        for route_index, _stop_index, _stop in unit.occurrences
    ):
        return ()
    return tuple(tuple(unit.key for unit in group) for group in touched)


def _start_pickups(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    stops: tuple[RouteStop, ...],
    start_node_id: str,
) -> tuple[tuple[RouteStop, ...], tuple[RouteStop, ...]]:
    pickups: list[RouteStop] = []
    remaining: list[RouteStop] = []
    for stop in stops:
        if (
            isinstance(stop, ActivityStop)
            and stop.activity_ref.kind == 'pickup'
            and stop_node_id(instance, solution, stop) == start_node_id
        ):
            pickups.append(stop)
        else:
            remaining.append(stop)
    return tuple(pickups), tuple(remaining)


def _unique_scored_plans(
    candidates: Sequence[_ScoredPlan],
) -> tuple[_ScoredPlan, ...]:
    unique: list[_ScoredPlan] = []
    for candidate in candidates:
        if not any(previous[0] == candidate[0] for previous in unique):
            unique.append(candidate)
    return tuple(unique)
