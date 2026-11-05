"""Instance-aware validation of authoritative VRP solution structure.

Created: 2026-11-03T18:15:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite

from multiobjective_vns.multiday_vrp.input_records import ArcRecord, LoadedVrpInstance
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
    RouteStop,
    TransferOperationStop,
    TransferStep,
    TransferStepRef,
    VehicleDayRoute,
    VrpSolution,
)

__all__ = ['representation_error']

_TOL_ABSOLUTE = 1e-9
_TOL_RELATIVE = 1e-12


def representation_error(instance: LoadedVrpInstance, solution: object) -> str | None:
    """Return the first structural error, or ``None`` for a valid representation.

    Operational feasibility conditions such as compatibility, service coverage,
    time windows, and capacity are assessed after this structural boundary.

    :param instance: Fixed, validated application instance.
    :param solution: Candidate plan to inspect.
    :returns: A concise invalid-representation reason, or ``None``.
    """
    if type(solution) is not VrpSolution:
        return 'solution must be a VrpSolution record'

    node_ids = {item.node_id for item in instance.network_data.nodes}
    arcs = {item.arc_id: item for item in instance.network_data.arcs}
    depots = {item.depot_id: item.node_id for item in instance.network_data.depots}
    depot_nodes = set(depots.values())
    vehicles = {item.vehicle_id: item for item in instance.fleet_data.vehicles}
    vehicle_order = {
        item.vehicle_id: index
        for index, item in enumerate(instance.fleet_data.vehicles)
    }
    people = {item.person_id for item in instance.personnel_data.personnel}
    packages = {item.package_id: item for item in instance.package_data.packages}
    interventions = {
        item.intervention_id: item for item in instance.intervention_data.interventions
    }
    facilities = {item.node_id for item in instance.network_data.transfer_facilities}
    days = len(instance.scenario_bank.planning_dates)

    route_order_error = _route_order_error(solution, vehicle_order, days)
    if route_order_error is not None:
        return route_order_error

    transfer_steps: dict[tuple[str, int], TransferStep] = {}
    previous_step_by_package: dict[str, int] = {}
    for step in solution.transfer_steps:
        if type(step) is not TransferStep:
            return 'transfer_steps must contain TransferStep records'
        package = packages.get(step.package_id)
        if package is None:
            return f'transfer step refers to unknown package {step.package_id!r}'
        if type(step.step_number) is not int or step.step_number < 1:
            return 'transfer step numbers must be positive integers'
        if step.step_number > package.max_transfers:
            return f'transfer step exceeds the limit for package {step.package_id!r}'
        key = step.package_id, step.step_number
        if key in transfer_steps:
            return f'duplicate transfer step {key!r}'
        previous = previous_step_by_package.get(step.package_id, 0)
        if step.step_number != previous + 1:
            return f'transfer steps for package {step.package_id!r} must be contiguous and ordered'
        if step.facility_id not in facilities:
            return f'transfer step uses unknown facility node {step.facility_id!r}'
        transfer_steps[key] = step
        previous_step_by_package[step.package_id] = step.step_number

    routes_by_vehicle_day: dict[tuple[str, int], VehicleDayRoute] = {}
    for route in solution.routes:
        if route.overnight_depot_id not in depots:
            return (
                f'route refers to unknown overnight depot {route.overnight_depot_id!r}'
            )
        if route.driver_id not in people:
            return f'route refers to unknown driver/person {route.driver_id!r}'
        if not _finite_nonnegative(route.start_time):
            return 'route start_time must be finite and nonnegative'
        key = route.vehicle_id, route.day_index
        routes_by_vehicle_day[key] = route

        if len(route.legs) != len(route.stops) + 1:
            return 'each route must have exactly one more leg than stops'
        for leg in route.legs:
            if type(leg) is not RouteLeg:
                return 'route legs must be RouteLeg records'
            if type(leg.arc_ids) is not tuple:
                return 'route leg arc_ids must be a tuple'
            for arc_id in leg.arc_ids:
                if arc_id not in arcs:
                    return f'route leg refers to unknown arc {arc_id!r}'

        stop_nodes: list[str] = []
        for stop in route.stops:
            if type(stop) is ActivityStop:
                ref = stop.activity_ref
                if type(ref) is not ActivityRef:
                    return 'activity stops require an ActivityRef'
                if ref.kind == 'pickup':
                    package = packages.get(ref.activity_id)
                    if package is None:
                        return f'activity refers to unknown pickup {ref.activity_id!r}'
                    node_id = package.pickup_node_id
                elif ref.kind == 'delivery':
                    package = packages.get(ref.activity_id)
                    if package is None:
                        return (
                            f'activity refers to unknown delivery {ref.activity_id!r}'
                        )
                    node_id = package.delivery_node_id
                elif ref.kind == 'intervention':
                    intervention = interventions.get(ref.activity_id)
                    if intervention is None:
                        return f'activity refers to unknown intervention {ref.activity_id!r}'
                    node_id = intervention.node_id
                else:
                    return f'unsupported activity kind {ref.kind!r}'
                if any(person_id not in people for person_id in stop.team_member_ids):
                    return 'activity team refers to unknown personnel'
                if ref.kind != 'intervention' and stop.team_member_ids:
                    return (
                        'package activity stops cannot carry intervention team members'
                    )
                if not _valid_stop_values(stop):
                    return 'activity stop timing fields must be finite and nonnegative, with break no longer than wait'
                stop_nodes.append(node_id)
            elif type(stop) is TransferOperationStop:
                reference = stop.transfer_step_ref
                if type(reference) is not TransferStepRef:
                    return 'transfer operation requires a TransferStepRef'
                if stop.role not in {'out', 'in'}:
                    return 'transfer operation role must be out or in'
                transfer_step = transfer_steps.get(
                    (reference.package_id, reference.step_number)
                )
                if transfer_step is None:
                    return 'transfer operation refers to an unknown transfer step'
                if not _valid_stop_values(stop):
                    return 'transfer stop timing fields must be finite and nonnegative, with break no longer than wait'
                stop_nodes.append(transfer_step.facility_id)
            elif type(stop) is DepotOnlyStop:
                if stop.node_id not in node_ids:
                    return f'depot-only stop refers to unknown node {stop.node_id!r}'
                if stop.node_id not in depot_nodes:
                    return 'depot-only stops must identify a depot-location node'
                if not _valid_stop_values(stop):
                    return 'depot-only stop timing fields must be finite and nonnegative, with break no longer than wait'
                if abs(stop.pre_service_wait) > _tolerance(stop.pre_service_wait, 0.0):
                    return 'depot-only stops must have zero pre_service_wait'
                stop_nodes.append(stop.node_id)
            else:
                return 'route stops must use a supported RouteStop record type'

        current_depot = vehicles[route.vehicle_id].initial_depot_id
        for earlier_day in range(1, route.day_index):
            earlier = routes_by_vehicle_day.get((route.vehicle_id, earlier_day))
            if earlier is not None:
                current_depot = earlier.overnight_depot_id
        start_node = depots[current_depot]
        destination_node = depots[route.overnight_depot_id]
        endpoints = (start_node, *stop_nodes, destination_node)
        for leg_index, leg in enumerate(route.legs):
            error = _path_error(
                leg, endpoints[leg_index], endpoints[leg_index + 1], arcs
            )
            if error is not None:
                return error

        has_activity = any(type(stop) is not DepotOnlyStop for stop in route.stops)
        has_travel = any(leg.arc_ids for leg in route.legs)
        if not has_activity and not has_travel:
            return 'inactive vehicle-days must be omitted from solution.routes'

        error = _tour_marker_error(route, stop_nodes, start_node, depot_nodes)
        if error is not None:
            return error

    return None


def _route_order_error(
    solution: VrpSolution,
    vehicle_order: Mapping[str, int],
    day_count: int,
) -> str | None:
    """Validate route records, unique vehicle-days, and canonical tuple order."""
    previous_order: tuple[int, int] | None = None
    seen_vehicle_days: set[tuple[str, int]] = set()
    for route in solution.routes:
        if type(route) is not VehicleDayRoute:
            return 'routes must contain VehicleDayRoute records'
        if route.vehicle_id not in vehicle_order:
            return f'route refers to unknown vehicle {route.vehicle_id!r}'
        if type(route.day_index) is not int or not 1 <= route.day_index <= day_count:
            return 'route day_index must identify a planning day'
        key = route.vehicle_id, route.day_index
        if key in seen_vehicle_days:
            return f'duplicate vehicle-day route {key!r}'
        seen_vehicle_days.add(key)

        current_order = route.day_index, vehicle_order[route.vehicle_id]
        if previous_order is not None and current_order <= previous_order:
            return 'routes must be ordered by day_index and instance vehicle order'
        previous_order = current_order
    return None


def _path_error(
    leg: RouteLeg,
    origin: str,
    destination: str,
    arcs: Mapping[str, ArcRecord],
) -> str | None:
    current = origin
    for arc_id in leg.arc_ids:
        arc = arcs[arc_id]
        if arc.tail_node_id != current:
            return f'route path is not a directed walk at arc {arc_id!r}'
        current = arc.head_node_id
    if current != destination:
        return 'route path endpoint does not match its prescribed destination'
    return None


def _valid_stop_values(stop: RouteStop) -> bool:
    pre_service_wait = stop.pre_service_wait
    wait = stop.wait
    break_duration = stop.break_duration
    if not all(
        _finite_nonnegative(value) for value in (pre_service_wait, wait, break_duration)
    ):
        return False
    return break_duration <= wait + _tolerance(break_duration, wait)


def _tour_marker_error(
    route: VehicleDayRoute,
    stop_nodes: list[str],
    start_node: str,
    depot_nodes: set[str],
) -> str | None:
    start_side: set[int] = set()
    if route.stops and stop_nodes[0] == start_node and not route.legs[0].arc_ids:
        start_side.add(0)
        index = 1
        while (
            index < len(route.stops)
            and stop_nodes[index] == start_node
            and not route.legs[index].arc_ids
        ):
            start_side.add(index)
            index += 1

    for index, stop in enumerate(route.stops):
        if not stop.tour_end:
            continue
        node_id = stop_nodes[index]
        if node_id not in depot_nodes:
            return 'tour_end may be set only on a depot-location stop'
        if index in start_side:
            return 'tour_end cannot mark a start-side depot visit'
        if (
            index + 1 < len(route.stops)
            and stop_nodes[index + 1] == node_id
            and not route.legs[index + 1].arc_ids
        ):
            return 'tour_end must mark the final stop in a depot visit'
        continues = index + 1 < len(route.stops) or bool(route.legs[-1].arc_ids)
        if not continues:
            return 'tour_end must mark a depot visit that the route leaves'
    return None


def _finite_nonnegative(value: object) -> bool:
    return type(value) is float and isfinite(value) and value >= 0.0


def _tolerance(left: float, right: float) -> float:
    return _TOL_ABSOLUTE + _TOL_RELATIVE * max(abs(left), abs(right))
