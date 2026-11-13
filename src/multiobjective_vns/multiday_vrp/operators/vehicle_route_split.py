"""Vehicle-route split Operator.

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
    choose_uniformly,
    operator_result,
    randomized_order,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    assess_complete_candidate,
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_assignment_helpers import (
    _append_unique,
    _bounded_limit,
    _choose_scored,
    _overnight_depots,
    _put_depot_pickups_first,
    _qualified_drivers,
    _rebase_service_waits,
    _rebuild_paths,
    _sample_subsets,
    _ScoredPlan,
    _sort_routes,
    _split_service_units,
)
from multiobjective_vns.multiday_vrp.solution import (
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.values import require_integer

__all__ = ['VehicleRouteSplitOperator']


class VehicleRouteSplitOperator:
    """Split service work from one route into a new vehicle-day route.

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
        """Apply up to ``k`` route splits sequentially.

        :param context: Base plan, requested size, feasibility request, and RNG.
        :returns: Changed plan and number of created routes, or ``m=0``.
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
            targets = self._eligible_route_targets(working)
            if not targets:
                break
            route_index = choose_uniformly(targets, context.random_source)
            candidates = self._split_candidates(working, route_index, context)
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

    def _eligible_route_targets(self, solution: VrpSolution) -> tuple[int, ...]:
        eligible: list[int] = []
        for route_index, route in enumerate(solution.routes):
            units = _split_service_units(solution, route_index)
            receiving_vehicles = _eligible_receiving_vehicle_ids(
                self._instance, solution, route.day_index
            )
            if len(units) >= 2 and receiving_vehicles:
                eligible.append(route_index)
        return tuple(eligible)

    def _split_candidates(
        self,
        base: VrpSolution,
        route_index: int,
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        source_route = base.routes[route_index]
        units = _split_service_units(base, route_index)
        if len(units) < 2:
            return ()
        free_vehicles = _eligible_receiving_vehicle_ids(
            self._instance, base, source_route.day_index
        )
        driver_by_vehicle = _qualified_drivers(self._instance)
        vehicle_depots = _overnight_depots(self._instance)
        partitions = _sample_subsets(len(units), self._candidate_limit, context)
        candidates: list[VrpSolution] = []
        for subset in partitions:
            moved_indices = {
                stop_index for unit_index in subset for stop_index in units[unit_index]
            }
            retained_stops = tuple(
                stop
                for stop_index, stop in enumerate(source_route.stops)
                if stop_index not in moved_indices
            )
            new_stops = tuple(
                stop
                for stop_index, stop in enumerate(source_route.stops)
                if stop_index in moved_indices
            )
            retained_stops = _put_depot_pickups_first(
                self._instance, base, source_route, retained_stops
            )
            for vehicle_id in randomized_order(free_vehicles, context.random_source):
                driver_ids = driver_by_vehicle.get(vehicle_id, ())
                if not driver_ids:
                    continue
                eligible_depots = vehicle_depots.get(vehicle_id, ())
                if not eligible_depots:
                    continue
                overnight_depot_id = (
                    source_route.overnight_depot_id
                    if source_route.overnight_depot_id in eligible_depots
                    else choose_uniformly(eligible_depots, context.random_source)
                )
                for driver_id in randomized_order(driver_ids, context.random_source):
                    route_without_source_stops = replace(
                        source_route,
                        stops=retained_stops,
                    )
                    provisional_routes = list(base.routes)
                    provisional_routes[route_index] = route_without_source_stops
                    new_route = VehicleDayRoute(
                        vehicle_id=vehicle_id,
                        day_index=source_route.day_index,
                        overnight_depot_id=overnight_depot_id,
                        start_time=source_route.start_time,
                        driver_id=driver_id,
                        stops=new_stops,
                        legs=(),
                    )
                    provisional_routes.append(new_route)
                    provisional = _sort_routes(
                        replace(base, routes=tuple(provisional_routes)),
                        self._instance,
                    )
                    new_route_index = next(
                        index
                        for index, route in enumerate(provisional.routes)
                        if route.vehicle_id == vehicle_id
                        and route.day_index == source_route.day_index
                    )
                    new_route = provisional.routes[new_route_index]
                    adjusted_stops = _put_depot_pickups_first(
                        self._instance,
                        provisional,
                        new_route,
                        new_route.stops,
                    )
                    if adjusted_stops != new_route.stops:
                        provisional_routes = list(provisional.routes)
                        provisional_routes[new_route_index] = replace(
                            new_route, stops=adjusted_stops
                        )
                        provisional = replace(
                            provisional, routes=tuple(provisional_routes)
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
                    candidate = _rebase_service_waits(
                        self._instance,
                        self._objectives,
                        base,
                        with_paths,
                        context,
                        self._assess_plan,
                    )
                    _append_unique(candidates, candidate)
                    if len(candidates) >= self._candidate_limit:
                        return tuple(candidates)
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


def _eligible_receiving_vehicle_ids(
    instance: LoadedVrpInstance, solution: VrpSolution, day_index: int
) -> tuple[str, ...]:
    active_on_day = {
        route.vehicle_id for route in solution.routes if route.day_index == day_index
    }
    active_over_horizon = {route.vehicle_id for route in solution.routes}
    exact_count = instance.settings.exact_active_vehicle_count
    return tuple(
        vehicle.vehicle_id
        for vehicle in instance.fleet_data.vehicles
        if vehicle.vehicle_id not in active_on_day
        and (exact_count is None or vehicle.vehicle_id in active_over_horizon)
    )
