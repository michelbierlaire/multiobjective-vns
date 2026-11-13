"""Spatial and time-window based route-partition rebuilding.

Created: 2026-11-11T09:30:18+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import cmp_to_key
from heapq import heappop, heappush
from itertools import combinations, permutations
from math import comb, inf, isclose, isfinite, perm

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    PackageRecord,
    PackageTimeWindowRecord,
)
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
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
    stop_node_id,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_elimination import (
    _relationship_groups,
    _service_units,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.values import require_integer

__all__ = ['SpatialTimeWindowRouteClusterRebuildOperator']

type _ScoredPlan = tuple[VrpSolution, tuple[object, ...], int]
type _Path = tuple[str, ...]
type _AssignmentAlternative = tuple[
    float,
    dict[str, str],
    int,
    dict[str, tuple[str, ...]],
]


@dataclass(frozen=True, slots=True)
class _PackageRepairUnit:
    """A linked set of complete package chains that must move together."""

    package_ids: tuple[str, ...]
    load: float


@dataclass(frozen=True, slots=True)
class _RouteData:
    """One active vehicle's route shell and its endpoint nodes."""

    route: VehicleDayRoute
    start_node_id: str
    end_node_id: str


class SpatialTimeWindowRouteClusterRebuildOperator:
    """Consolidate spatially and temporally compatible package groups.

    The Operator derives customer groups from minimum network travel times and
    delivery windows, independently of their current route assignments. It
    consolidates up to ``k`` whole groups on distinct active vehicles, rebuilds
    package-only route orders, and returns complete plans for application
    evaluation. The application evaluator remains authoritative for schedule,
    capacity, and objective values.

    This first version applies to single-day plans with complete package
    pickup/delivery chains, depot pickups, and one route per active vehicle.
    Plans containing other service or transfer operations, multiple routes per
    vehicle, assigned teams, post-service waits, breaks, or tour-end markers
    return no candidates.

    :param instance: Validated VRP instance.
    :param objective_declaration: Ordered objectives used to rank candidates.
    :param assess_plan: Shared complete-plan assessment callback.
    :param max_neighborhood_size: Largest supported number of groups consolidated.
    :param objective_id: Optional target objective for candidate ranking.
    :param max_candidate_alternatives: Maximum internal plans assessed per call.
    :raises TypeError: If a captured dependency has an invalid type.
    :raises ValueError: If an objective ID or search limit is invalid.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_target_position',
        '_window_position',
        '_maximum_size',
        '_candidate_limit',
        '_packages',
        '_windows',
        '_vehicle_capacities',
        '_compatibility',
        '_shortest_times',
        '_shortest_paths',
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
        if not isinstance(instance, LoadedVrpInstance):
            raise TypeError('instance must be a LoadedVrpInstance')
        if not isinstance(objective_declaration, ObjectiveDeclaration):
            raise TypeError('objective_declaration must be an ObjectiveDeclaration')
        if not callable(assess_plan):
            raise TypeError('assess_plan must be callable')
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._target_position = (
            None
            if objective_id is None
            else objective_position(objective_declaration, objective_id)
        )
        window_id = instance.time_window_violation_objective_id
        self._window_position = (
            None
            if window_id is None
            else objective_position(objective_declaration, window_id)
        )
        self._maximum_size = require_integer(
            max_neighborhood_size,
            name='max_neighborhood_size',
            minimum=1,
        )
        if type(max_candidate_alternatives) is not int:
            raise TypeError('max_candidate_alternatives must be an integer')
        if not 1 <= max_candidate_alternatives <= 32:
            raise ValueError('max_candidate_alternatives must be in 1..32')
        self._candidate_limit = max_candidate_alternatives
        self._packages = {
            item.package_id: item for item in instance.package_data.packages
        }
        windows: dict[str, dict[str, list[PackageTimeWindowRecord]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for item in instance.package_data.package_time_windows:
            windows[item.package_id][item.activity].append(item)
        self._windows = {
            package_id: {
                activity: tuple(
                    sorted(records, key=lambda item: (item.start_time, item.end_time))
                )
                for activity, records in by_activity.items()
            }
            for package_id, by_activity in windows.items()
        }
        self._vehicle_capacities = {
            item.vehicle_id: item.cargo_capacity
            for item in instance.fleet_data.vehicles
        }
        self._compatibility = {
            (item.vehicle_id, item.package_id): item.compatible
            for item in instance.package_data.vehicle_package_compatibility
        }
        self._shortest_times, self._shortest_paths = self._build_shortest_travel_data()

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return an ordered batch of complete route-rebuild candidates.

        :param context: Base plan, neighborhood size, RNG, and candidate limit.
        :returns: Complete plans whose package groups changed vehicle.
        :raises TypeError: If the base is not a VRP solution.
        :raises ValueError: If ``k`` exceeds this Operator's registered maximum.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        route_data = self._supported_routes(base)
        if route_data is None:
            return OperatorResult(candidates=())
        units, package_vehicle = self._repair_units(base)
        if not units:
            return OperatorResult(candidates=())
        vehicles = tuple(sorted(route_data))
        group_count = len(vehicles)
        if group_count < 2 or len(units) < group_count:
            return OperatorResult(candidates=())

        maximum_group_load = max(
            self._vehicle_capacities[vehicle] for vehicle in vehicles
        )
        scored: list[_ScoredPlan] = []
        partition_limit = min(4, self._candidate_limit)
        partitions: list[tuple[tuple[int, ...], ...]] = []
        for _ in range(partition_limit):
            partition = self._agglomerative_partition(
                units,
                group_count,
                maximum_group_load,
                route_data,
                context,
            )
            if partition is None or partition in partitions:
                continue
            partitions.append(partition)

        for partition in partitions:
            for selected_count in range(1, min(size, group_count) + 1):
                subsets = _sample_combinations(
                    len(partition),
                    selected_count,
                    self._candidate_limit,
                    context,
                )
                for selected_groups in subsets:
                    remaining = self._candidate_limit - len(scored)
                    if remaining <= 0:
                        break
                    targets = _sample_injections(
                        vehicles,
                        selected_count,
                        remaining,
                        context,
                    )
                    alternatives: list[_AssignmentAlternative] = []
                    for target_vehicles in targets:
                        assignment = dict(package_vehicle)
                        for group_index, target_vehicle in zip(
                            selected_groups, target_vehicles, strict=True
                        ):
                            for unit_index in partition[group_index]:
                                for package_id in units[unit_index].package_ids:
                                    assignment[package_id] = target_vehicle
                        moved_groups = sum(
                            any(
                                assignment[package_id] != package_vehicle[package_id]
                                for unit_index in partition[group_index]
                                for package_id in units[unit_index].package_ids
                            )
                            for group_index in selected_groups
                        )
                        if moved_groups == 0 or moved_groups > context.k:
                            continue
                        if not self._assignment_is_valid(
                            assignment, vehicles, route_data, units
                        ):
                            continue
                        delivery_orders = self._delivery_orders(
                            assignment,
                            package_vehicle,
                            units,
                            vehicles,
                            route_data,
                            context,
                        )
                        if delivery_orders is None:
                            continue
                        estimated_cost = sum(
                            self._route_order_score(
                                delivery_orders[vehicle_id],
                                tuple(
                                    package_id
                                    for package_id, assigned_vehicle in assignment.items()
                                    if assigned_vehicle == vehicle_id
                                ),
                                route_data[vehicle_id],
                            )[2]
                            for vehicle_id in vehicles
                        )
                        alternatives.append(
                            (
                                estimated_cost,
                                assignment,
                                moved_groups,
                                delivery_orders,
                            )
                        )
                    ranked_alternatives = _rank_assignment_alternatives(
                        alternatives, context
                    )
                    if ranked_alternatives:
                        minimum_cost = ranked_alternatives[0][0]
                        ranked_alternatives = tuple(
                            item
                            for item in ranked_alternatives
                            if isclose(
                                item[0],
                                minimum_cost,
                                rel_tol=1e-12,
                                abs_tol=1e-9,
                            )
                        )
                    for (
                        _estimated_cost,
                        assignment,
                        moved_groups,
                        delivery_orders,
                    ) in ranked_alternatives:
                        candidate = self._rebuild_plan(
                            base, assignment, route_data, delivery_orders
                        )
                        if candidate is None or candidate == base:
                            continue
                        assessment = assess_complete_candidate(
                            self._instance,
                            self._objectives,
                            candidate,
                            self._assess_plan,
                            ensure_feasible=context.ensure_feasible,
                        )
                        if assessment is None or assessment.objective_values is None:
                            continue
                        self._append_scored(
                            scored,
                            (candidate, assessment.objective_values, moved_groups),
                        )
                    if len(scored) >= self._candidate_limit:
                        break
                if len(scored) >= self._candidate_limit:
                    break
            if len(scored) >= self._candidate_limit:
                break

        ranked = self._rank(scored, context)
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution=solution), modifications)
                for solution, _values, modifications in ranked[: context.max_candidates]
            )
        )

    def _supported_routes(self, solution: VrpSolution) -> dict[str, _RouteData] | None:
        if solution.transfer_steps:
            return None
        routes_by_vehicle: dict[str, list[VehicleDayRoute]] = defaultdict(list)
        for route in solution.routes:
            routes_by_vehicle[route.vehicle_id].append(route)
            for stop in route.stops:
                if type(stop) is not ActivityStop or stop.activity_ref.kind not in {
                    'pickup',
                    'delivery',
                }:
                    return None
                if (
                    stop.team_member_ids
                    or stop.wait != 0.0
                    or stop.break_duration != 0.0
                    or stop.tour_end
                ):
                    return None
        if len(routes_by_vehicle) < 2 or any(
            len(routes) != 1 for routes in routes_by_vehicle.values()
        ):
            return None
        days = {routes[0].day_index for routes in routes_by_vehicle.values()}
        if len(days) != 1:
            return None
        result: dict[str, _RouteData] = {}
        for vehicle_id, routes in routes_by_vehicle.items():
            route = routes[0]
            nodes = route_node_sequence(self._instance, solution, route)
            if not nodes:
                return None
            result[vehicle_id] = _RouteData(route, nodes[0], nodes[-1])
        return result

    def _repair_units(
        self, solution: VrpSolution
    ) -> tuple[tuple[_PackageRepairUnit, ...], dict[str, str]]:
        occurrences: dict[str, dict[str, str]] = defaultdict(dict)
        for route in solution.routes:
            for stop in route.stops:
                assert isinstance(stop, ActivityStop)
                package_id = stop.activity_ref.activity_id
                occurrences[package_id][stop.activity_ref.kind] = route.vehicle_id
        if set(occurrences) != set(self._packages) or any(
            set(kinds) != {'pickup', 'delivery'} or kinds['pickup'] != kinds['delivery']
            for kinds in occurrences.values()
        ):
            return (), {}

        base_vehicle = {
            package_id: kinds['delivery'] for package_id, kinds in occurrences.items()
        }
        service_units = _service_units(solution)
        related_units = _relationship_groups(self._instance, service_units)
        units: list[_PackageRepairUnit] = []
        seen_packages: set[str] = set()
        for related in related_units:
            keys = tuple(sorted(unit.key for unit in related))
            if not keys or any(key[0] != 'package' for key in keys):
                return (), {}
            package_ids = tuple(sorted({key[1] for key in keys}))
            if not package_ids or seen_packages.intersection(package_ids):
                return (), {}
            if any(
                not self._windows.get(package_id, {}).get('delivery')
                or not self._windows.get(package_id, {}).get('pickup')
                for package_id in package_ids
            ):
                return (), {}
            seen_packages.update(package_ids)
            units.append(
                _PackageRepairUnit(
                    package_ids,
                    sum(self._packages[package_id].load for package_id in package_ids),
                )
            )
        if seen_packages != set(self._packages):
            return (), {}
        return tuple(units), base_vehicle

    def _agglomerative_partition(
        self,
        units: tuple[_PackageRepairUnit, ...],
        group_count: int,
        maximum_group_load: float,
        routes: dict[str, _RouteData],
        context: CandidateContext,
    ) -> tuple[tuple[int, ...], ...] | None:
        clusters: list[tuple[int, ...]] = [(index,) for index in range(len(units))]
        while len(clusters) > group_count:
            merges: list[tuple[float, int, int]] = []
            for left_index, right_index in combinations(range(len(clusters)), 2):
                left = clusters[left_index]
                right = clusters[right_index]
                combined = (*left, *right)
                package_ids = tuple(
                    package_id
                    for unit_index in combined
                    for package_id in units[unit_index].package_ids
                )
                combined_load = sum(units[index].load for index in combined)
                if combined_load > maximum_group_load + 1e-9:
                    continue
                if not any(
                    self._group_fits_vehicle(package_ids, vehicle_id, routes)
                    for vehicle_id in routes
                ):
                    continue
                affinities = tuple(
                    self._unit_affinity(units[left_unit], units[right_unit])
                    for left_unit in left
                    for right_unit in right
                )
                if not affinities or any(not isfinite(value) for value in affinities):
                    continue
                merges.append(
                    (sum(affinities) / len(affinities), left_index, right_index)
                )
            if not merges:
                return None
            best_score = min(item[0] for item in merges)
            tied = tuple(
                (left, right)
                for score, left, right in merges
                if isclose(score, best_score, rel_tol=1e-12, abs_tol=1e-9)
            )
            left_index, right_index = randomized_order(tied, context.random_source)[0]
            merged = tuple(sorted((*clusters[left_index], *clusters[right_index])))
            clusters = [
                cluster
                for index, cluster in enumerate(clusters)
                if index not in {left_index, right_index}
            ]
            clusters.append(merged)
            clusters.sort(key=lambda item: item[0])
        return tuple(clusters)

    def _group_fits_vehicle(
        self,
        package_ids: tuple[str, ...],
        vehicle_id: str,
        routes: dict[str, _RouteData],
    ) -> bool:
        if vehicle_id not in routes:
            return False
        if sum(self._packages[item].load for item in package_ids) > (
            self._vehicle_capacities[vehicle_id] + 1e-9
        ):
            return False
        for package_id in package_ids:
            package = self._packages[package_id]
            if package.pickup_node_id != routes[vehicle_id].start_node_id:
                return False
            if not self._compatibility.get((vehicle_id, package_id), True):
                return False
        return True

    def _unit_affinity(
        self, left: _PackageRepairUnit, right: _PackageRepairUnit
    ) -> float:
        values = tuple(
            self._package_pair_affinity(
                self._packages[left_id], self._packages[right_id]
            )
            for left_id in left.package_ids
            for right_id in right.package_ids
        )
        if not values:
            return inf
        return sum(values) / len(values)

    def _package_pair_affinity(
        self, left: PackageRecord, right: PackageRecord
    ) -> float:
        left_to_right = self._shortest_times.get(
            (left.delivery_node_id, right.delivery_node_id), inf
        )
        right_to_left = self._shortest_times.get(
            (right.delivery_node_id, left.delivery_node_id), inf
        )
        finite_directed = tuple(
            value for value in (left_to_right, right_to_left) if isfinite(value)
        )
        if not finite_directed:
            return inf
        spatial = sum(finite_directed) / len(finite_directed)
        temporal = self._minimum_pair_window_penalty(
            left,
            right,
            left_to_right,
            right_to_left,
        )
        return spatial + temporal

    def _minimum_pair_window_penalty(
        self,
        left: PackageRecord,
        right: PackageRecord,
        left_to_right: float,
        right_to_left: float,
    ) -> float:
        left_windows = self._windows[left.package_id]['delivery']
        right_windows = self._windows[right.package_id]['delivery']
        penalties: list[float] = []
        for left_window in left_windows:
            for right_window in right_windows:
                left_first = (
                    inf
                    if not isfinite(left_to_right)
                    else max(
                        0.0,
                        left_window.start_time
                        + left.delivery_service_duration
                        - left_window.end_time,
                    )
                    + max(
                        0.0,
                        max(
                            left_window.start_time
                            + left.delivery_service_duration
                            + left_to_right,
                            right_window.start_time,
                        )
                        + right.delivery_service_duration
                        - right_window.end_time,
                    )
                )
                right_first = (
                    inf
                    if not isfinite(right_to_left)
                    else max(
                        0.0,
                        right_window.start_time
                        + right.delivery_service_duration
                        - right_window.end_time,
                    )
                    + max(
                        0.0,
                        max(
                            right_window.start_time
                            + right.delivery_service_duration
                            + right_to_left,
                            left_window.start_time,
                        )
                        + left.delivery_service_duration
                        - left_window.end_time,
                    )
                )
                penalties.append(min(left_first, right_first))
        return min(penalties, default=inf)

    def _assignment_is_valid(
        self,
        assignment: dict[str, str],
        vehicle_ids: tuple[str, ...],
        routes: dict[str, _RouteData],
        units: tuple[_PackageRepairUnit, ...],
    ) -> bool:
        if set(assignment) != set(self._packages):
            return False
        loads = {vehicle_id: 0.0 for vehicle_id in vehicle_ids}
        counts = {vehicle_id: 0 for vehicle_id in vehicle_ids}
        for package_id, vehicle_id in assignment.items():
            if not self._group_fits_vehicle((package_id,), vehicle_id, routes):
                return False
            loads[vehicle_id] += self._packages[package_id].load
            counts[vehicle_id] += 1
        if any(
            len({assignment[package_id] for package_id in unit.package_ids}) != 1
            for unit in units
        ):
            return False
        return all(
            counts[vehicle_id] > 0
            and loads[vehicle_id] <= self._vehicle_capacities[vehicle_id] + 1e-9
            for vehicle_id in vehicle_ids
        )

    def _rebuild_plan(
        self,
        base: VrpSolution,
        assignment: dict[str, str],
        route_data: dict[str, _RouteData],
        delivery_orders: dict[str, tuple[str, ...]],
    ) -> VrpSolution | None:
        base_vehicle = {
            stop.activity_ref.activity_id: route.vehicle_id
            for route in base.routes
            for stop in route.stops
            if isinstance(stop, ActivityStop) and stop.activity_ref.kind == 'delivery'
        }
        affected_vehicles = {
            vehicle_id
            for package_id, vehicle_id in assignment.items()
            if base_vehicle[package_id] != vehicle_id
            for vehicle_id in (base_vehicle[package_id], vehicle_id)
        }
        source_stops: dict[tuple[str, str], ActivityStop] = {}
        for route in base.routes:
            for stop in route.stops:
                assert isinstance(stop, ActivityStop)
                source_stops[
                    (stop.activity_ref.activity_id, stop.activity_ref.kind)
                ] = stop

        packages_by_vehicle: dict[str, list[str]] = defaultdict(list)
        for package_id, vehicle_id in assignment.items():
            packages_by_vehicle[vehicle_id].append(package_id)

        rebuilt: list[VehicleDayRoute] = []
        for vehicle_id, data in route_data.items():
            if vehicle_id not in affected_vehicles:
                rebuilt.append(data.route)
                continue
            package_ids = tuple(sorted(packages_by_vehicle[vehicle_id]))
            pickups = tuple(
                replace(
                    source_stops[(package_id, 'pickup')],
                    pre_service_wait=0.0,
                    wait=0.0,
                    break_duration=0.0,
                    tour_end=False,
                )
                for package_id in package_ids
            )
            deliveries = tuple(
                replace(
                    source_stops[(package_id, 'delivery')],
                    pre_service_wait=0.0,
                    wait=0.0,
                    break_duration=0.0,
                    tour_end=False,
                )
                for package_id in delivery_orders[vehicle_id]
            )
            stops = (*pickups, *deliveries)
            placeholder_legs = tuple(RouteLeg(()) for _ in range(len(stops) + 1))
            rebuilt.append(replace(data.route, stops=stops, legs=placeholder_legs))

        candidate = replace(base, routes=tuple(rebuilt))
        routes_with_paths: list[VehicleDayRoute] = []
        for route in candidate.routes:
            if route.vehicle_id not in affected_vehicles:
                routes_with_paths.append(route)
                continue
            nodes = route_node_sequence(self._instance, candidate, route)
            legs: list[RouteLeg] = []
            for origin, destination in zip(nodes[:-1], nodes[1:], strict=True):
                path = self._shortest_paths.get((origin, destination))
                if path is None:
                    return None
                legs.append(RouteLeg(path))
            routes_with_paths.append(replace(route, legs=tuple(legs)))
        candidate = replace(candidate, routes=tuple(routes_with_paths))
        return self._schedule_waits(candidate, affected_vehicles)

    def _delivery_orders(
        self,
        assignment: dict[str, str],
        base_assignment: dict[str, str],
        units: tuple[_PackageRepairUnit, ...],
        vehicle_ids: tuple[str, ...],
        routes: dict[str, _RouteData],
        context: CandidateContext,
    ) -> dict[str, tuple[str, ...]] | None:
        affected_vehicles = {
            affected_vehicle
            for package_id, vehicle_id in assignment.items()
            if base_assignment[package_id] != vehicle_id
            for affected_vehicle in (base_assignment[package_id], vehicle_id)
        }
        orders: dict[str, tuple[str, ...]] = {}
        for vehicle_id in vehicle_ids:
            if vehicle_id not in affected_vehicles:
                orders[vehicle_id] = tuple(
                    stop.activity_ref.activity_id
                    for stop in routes[vehicle_id].route.stops
                    if isinstance(stop, ActivityStop)
                    and stop.activity_ref.kind == 'delivery'
                )
                continue
            assigned_units = tuple(
                unit.package_ids
                for unit in units
                if assignment[unit.package_ids[0]] == vehicle_id
            )
            order = self._regret_delivery_order(
                assigned_units, routes[vehicle_id], context
            )
            if order is None:
                return None
            orders[vehicle_id] = order
        return orders

    def _regret_delivery_order(
        self,
        package_units: tuple[tuple[str, ...], ...],
        route: _RouteData,
        context: CandidateContext,
    ) -> tuple[str, ...] | None:
        """Build one route order with window-slack and travel-time regret."""
        all_package_ids = tuple(
            sorted(package_id for unit in package_units for package_id in unit)
        )
        pending = list(package_units)
        delivery_order: tuple[str, ...] = ()
        while pending:
            alternatives_by_unit: list[
                tuple[
                    tuple[str, ...],
                    tuple[tuple[float, float, float], ...],
                ]
            ] = []
            for unit in pending:
                block = tuple(sorted(unit, key=self._delivery_order_key))
                score_options: list[tuple[float, float, float]] = []
                for insertion_index in range(len(delivery_order) + 1):
                    candidate_order = (
                        *delivery_order[:insertion_index],
                        *block,
                        *delivery_order[insertion_index:],
                    )
                    score = self._route_order_score(
                        candidate_order, all_package_ids, route
                    )
                    if all(isfinite(value) for value in score):
                        score_options.append(score)
                if score_options:
                    alternatives_by_unit.append((unit, tuple(sorted(score_options))))
            if not alternatives_by_unit:
                return None

            unit_regrets: list[
                tuple[tuple[float, float, float], tuple[str, ...], tuple[float, ...]]
            ] = []
            for unit, scores in alternatives_by_unit:
                if len(scores) == 1:
                    regret = (inf, inf, inf)
                else:
                    regret = (
                        scores[1][0] - scores[0][0],
                        scores[1][1] - scores[0][1],
                        scores[1][2] - scores[0][2],
                    )
                unit_regrets.append((regret, unit, scores[0]))
            best_regret = max(item[0] for item in unit_regrets)
            urgent = tuple(item for item in unit_regrets if item[0] == best_regret)
            _regret, selected_unit, best_score = randomized_order(
                urgent, context.random_source
            )[0]

            best_positions: list[tuple[tuple[float, float, float], int]] = []
            block = tuple(sorted(selected_unit, key=self._delivery_order_key))
            for insertion_index in range(len(delivery_order) + 1):
                candidate_order = (
                    *delivery_order[:insertion_index],
                    *block,
                    *delivery_order[insertion_index:],
                )
                score = self._route_order_score(candidate_order, all_package_ids, route)
                if score == best_score:
                    best_positions.append((score, insertion_index))
            _score, selected_index = randomized_order(
                best_positions, context.random_source
            )[0]
            delivery_order = (
                *delivery_order[:selected_index],
                *block,
                *delivery_order[selected_index:],
            )
            pending.remove(selected_unit)
        return delivery_order

    def _route_order_score(
        self,
        delivery_order: tuple[str, ...],
        package_ids: tuple[str, ...],
        route: _RouteData,
    ) -> tuple[float, float, float]:
        current_time = route.route.start_time
        current_node = route.start_node_id
        total_lateness = 0.0
        minimum_slack = inf
        total_travel = 0.0
        pickup_ids = tuple(sorted(package_ids, key=self._pickup_order_key))
        stops = tuple(('pickup', package_id) for package_id in pickup_ids) + tuple(
            ('delivery', package_id) for package_id in delivery_order
        )
        for activity, package_id in stops:
            package = self._packages[package_id]
            next_node = (
                package.pickup_node_id
                if activity == 'pickup'
                else package.delivery_node_id
            )
            travel = self._shortest_times.get((current_node, next_node), inf)
            if not isfinite(travel):
                return inf, inf, inf
            total_travel += travel
            arrival = current_time + travel
            duration = (
                package.pickup_service_duration
                if activity == 'pickup'
                else package.delivery_service_duration
            )
            windows = self._windows[package_id].get(activity, ())
            if not windows:
                return inf, inf, inf
            window_options = []
            for window in windows:
                service_start = max(arrival, window.start_time)
                completion = service_start + duration
                lateness = max(0.0, completion - window.end_time)
                slack = window.end_time - completion
                window_options.append((lateness, -slack, service_start, slack))
            lateness, _negative_slack, service_start, slack = min(window_options)
            total_lateness += lateness
            minimum_slack = min(minimum_slack, slack)
            current_time = service_start + duration
            current_node = next_node
        return_node_travel = self._shortest_times.get(
            (current_node, route.end_node_id), inf
        )
        if not isfinite(return_node_travel):
            return inf, inf, inf
        total_travel += return_node_travel
        return total_lateness, -minimum_slack, total_travel

    def _pickup_order_key(self, package_id: str) -> tuple[float, float, str]:
        windows = self._windows[package_id]['pickup']
        earliest_due = min(item.end_time for item in windows)
        earliest_open = min(item.start_time for item in windows)
        return earliest_due, earliest_open, package_id

    def _delivery_order_key(self, package_id: str) -> tuple[float, float, str]:
        windows = self._windows[package_id]['delivery']
        earliest_due = min(item.end_time for item in windows)
        earliest_open = min(item.start_time for item in windows)
        return earliest_due, earliest_open, package_id

    def _schedule_waits(
        self, solution: VrpSolution, affected_vehicles: set[str]
    ) -> VrpSolution | None:
        rebuilt_routes: list[VehicleDayRoute] = []
        for route in solution.routes:
            if route.vehicle_id not in affected_vehicles:
                rebuilt_routes.append(route)
                continue
            nodes = route_node_sequence(self._instance, solution, route)
            current_time = route.start_time
            current_node = nodes[0]
            stops: list[ActivityStop] = []
            for stop in route.stops:
                assert isinstance(stop, ActivityStop)
                next_node = stop_node_id(self._instance, solution, stop)
                travel_time = self._shortest_times.get((current_node, next_node))
                if travel_time is None or not isfinite(travel_time):
                    return None
                arrival = current_time + travel_time
                package = self._packages[stop.activity_ref.activity_id]
                duration = (
                    package.pickup_service_duration
                    if stop.activity_ref.kind == 'pickup'
                    else package.delivery_service_duration
                )
                wait = self._service_wait(
                    package.package_id,
                    stop.activity_ref.kind,
                    arrival,
                    duration,
                )
                scheduled = replace(stop, pre_service_wait=wait)
                stops.append(scheduled)
                current_time = arrival + wait + duration
                current_node = next_node
            rebuilt_routes.append(replace(route, stops=tuple(stops)))
        return replace(solution, routes=tuple(rebuilt_routes))

    def _service_wait(
        self,
        package_id: str,
        activity: str,
        arrival: float,
        duration: float,
    ) -> float:
        windows = self._windows[package_id].get(activity, ())
        if not windows:
            return 0.0
        options: list[tuple[float, float, float, float]] = []
        for window in windows:
            service_start = max(arrival, window.start_time)
            completion = service_start + duration
            lateness = max(0.0, completion - window.end_time)
            slack = window.end_time - completion
            options.append((lateness, -slack, service_start, service_start - arrival))
        _lateness, _negative_slack, _service_start, wait = min(options)
        return max(0.0, wait)

    def _build_shortest_travel_data(
        self,
    ) -> tuple[
        dict[tuple[str, str], float],
        dict[tuple[str, str], _Path],
    ]:
        arc_times: dict[str, float] = {}
        for (
            _scenario,
            arc_id,
            _day,
            _boundary,
        ), value in self._instance.scenario_bank.travel_times.items():
            arc_times[arc_id] = min(arc_times.get(arc_id, inf), value)
        outgoing: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
        for arc in self._instance.network_data.arcs:
            weight = arc_times.get(arc.arc_id)
            if weight is not None and isfinite(weight):
                outgoing[arc.tail_node_id].append(
                    (arc.arc_id, arc.head_node_id, weight)
                )
        frozen_outgoing = {
            node_id: tuple(sorted(arcs)) for node_id, arcs in outgoing.items()
        }
        shortest_times: dict[tuple[str, str], float] = {}
        shortest_paths: dict[tuple[str, str], _Path] = {}
        relevant_nodes = {
            node_id
            for package in self._packages.values()
            for node_id in (package.pickup_node_id, package.delivery_node_id)
        }
        relevant_nodes.update(
            depot.node_id for depot in self._instance.network_data.depots
        )
        for source in sorted(relevant_nodes):
            distances = {source: 0.0}
            paths: dict[str, _Path] = {source: ()}
            queue: list[tuple[float, str]] = [(0.0, source)]
            while queue:
                distance, node_id = heappop(queue)
                if distances.get(node_id) != distance:
                    continue
                for arc_id, destination, weight in frozen_outgoing.get(node_id, ()):
                    next_distance = distance + weight
                    if next_distance < distances.get(destination, inf):
                        distances[destination] = next_distance
                        paths[destination] = (*paths[node_id], arc_id)
                        heappush(queue, (next_distance, destination))
            for destination, distance in distances.items():
                shortest_times[(source, destination)] = distance
                shortest_paths[(source, destination)] = paths[destination]
        return shortest_times, shortest_paths

    def _rank(
        self,
        candidates: Sequence[_ScoredPlan],
        context: CandidateContext,
    ) -> tuple[_ScoredPlan, ...]:
        remaining = list(candidates)
        if self._window_position is not None and remaining:
            objective = self._objectives.objectives[self._window_position]
            best = remaining[0][1][self._window_position]
            for candidate in remaining[1:]:
                if (
                    objective.compare(candidate[1][self._window_position], best)
                    is ObjectiveComparison.LESS
                ):
                    best = candidate[1][self._window_position]
            remaining = [
                candidate
                for candidate in remaining
                if objective.compare(candidate[1][self._window_position], best)
                is ObjectiveComparison.EQUAL
            ]
        if self._target_position is None:
            return self._pareto_front_order(
                remaining,
                tuple(range(len(self._objectives.objectives))),
                context,
            )

        target = self._target_position
        objective = self._objectives.objectives[target]

        def compare_target(left: _ScoredPlan, right: _ScoredPlan) -> int:
            comparison = objective.compare(left[1][target], right[1][target])
            if comparison is ObjectiveComparison.LESS:
                return -1
            if comparison is ObjectiveComparison.GREATER:
                return 1
            return 0

        remaining.sort(key=cmp_to_key(compare_target))
        groups: list[list[_ScoredPlan]] = []
        for candidate in remaining:
            if not groups or compare_target(groups[-1][0], candidate) != 0:
                groups.append([candidate])
            else:
                groups[-1].append(candidate)
        other_positions = tuple(
            position
            for position in range(len(self._objectives.objectives))
            if position != target
        )
        ordered: list[_ScoredPlan] = []
        for group in groups:
            ordered.extend(self._pareto_front_order(group, other_positions, context))
        return tuple(ordered)

    def _pareto_front_order(
        self,
        candidates: Sequence[_ScoredPlan],
        positions: tuple[int, ...],
        context: CandidateContext,
    ) -> tuple[_ScoredPlan, ...]:
        remaining = list(candidates)
        ordered: list[_ScoredPlan] = []
        while remaining:
            front = [
                candidate
                for candidate in remaining
                if not any(
                    self._dominates(other, candidate, positions)
                    for other in remaining
                    if other is not candidate
                )
            ]
            if not front:
                front = remaining[:1]
            ordered.extend(randomized_order(front, context.random_source))
            front_ids = {id(candidate) for candidate in front}
            remaining = [
                candidate for candidate in remaining if id(candidate) not in front_ids
            ]
        return tuple(ordered)

    def _dominates(
        self,
        left: _ScoredPlan,
        right: _ScoredPlan,
        positions: tuple[int, ...],
    ) -> bool:
        comparisons = tuple(
            self._objectives.objectives[position].compare(
                left[1][position], right[1][position]
            )
            for position in positions
        )
        return all(
            comparison in {ObjectiveComparison.LESS, ObjectiveComparison.EQUAL}
            for comparison in comparisons
        ) and any(comparison is ObjectiveComparison.LESS for comparison in comparisons)

    @staticmethod
    def _append_scored(candidates: list[_ScoredPlan], candidate: _ScoredPlan) -> None:
        for index, previous in enumerate(candidates):
            if previous[0] == candidate[0]:
                if candidate[2] < previous[2]:
                    candidates[index] = candidate
                return
        candidates.append(candidate)


def _sample_combinations(
    item_count: int,
    selected_count: int,
    limit: int,
    context: CandidateContext,
) -> tuple[tuple[int, ...], ...]:
    total = comb(item_count, selected_count)
    if total <= limit:
        return randomized_order(
            tuple(combinations(range(item_count), selected_count)),
            context.random_source,
        )
    selected: list[tuple[int, ...]] = []
    seen: set[tuple[int, ...]] = set()
    for _ in range(max(20, limit * 20)):
        candidate = tuple(
            sorted(
                randomized_sample(
                    tuple(range(item_count)),
                    selected_count,
                    context.random_source,
                )
            )
        )
        if candidate in seen:
            continue
        seen.add(candidate)
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return tuple(selected)


def _rank_assignment_alternatives(
    alternatives: Sequence[_AssignmentAlternative], context: CandidateContext
) -> tuple[_AssignmentAlternative, ...]:
    """Order mappings by estimated cost and randomize equal-cost mappings."""
    remaining = sorted(alternatives, key=lambda item: item[0])
    ranked: list[_AssignmentAlternative] = []
    while remaining:
        best_cost = remaining[0][0]
        tied = tuple(
            item
            for item in remaining
            if isclose(item[0], best_cost, rel_tol=1e-12, abs_tol=1e-9)
        )
        ranked.extend(randomized_order(tied, context.random_source))
        tied_ids = {id(item) for item in tied}
        remaining = [item for item in remaining if id(item) not in tied_ids]
    return tuple(ranked)


def _sample_injections(
    vehicle_ids: tuple[str, ...],
    assignment_count: int,
    limit: int,
    context: CandidateContext,
) -> tuple[tuple[str, ...], ...]:
    total = perm(len(vehicle_ids), assignment_count)
    if total <= limit:
        return randomized_order(
            tuple(permutations(vehicle_ids, assignment_count)),
            context.random_source,
        )
    selected: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for _ in range(max(20, limit * 20)):
        candidate = randomized_order(vehicle_ids, context.random_source)[
            :assignment_count
        ]
        if candidate in seen:
            continue
        seen.add(candidate)
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return tuple(selected)
