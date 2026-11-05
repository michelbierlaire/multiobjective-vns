"""Calculate package and tool loads along vehicle routes.

Created: 2026-11-04T12:29:51+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from math import fsum

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.path_search import stop_node_id
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    TransferOperationStop,
    VrpSolution,
)

__all__ = ['capacity_excess_by_stop', 'capacity_states_by_stop']


def capacity_excess_by_stop(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> dict[tuple[int, int], float]:
    """Return cargo-plus-tool capacity excess at each route stop.

    Each key is ``(route_index, stop_index)``. The value is the maximum excess
    observed immediately before or after that stop's package load change.
    """
    return {
        key: excess
        for key, (excess, _loaded) in capacity_states_by_stop(
            instance, solution
        ).items()
    }


def capacity_states_by_stop(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> dict[tuple[int, int], tuple[float, frozenset[str]]]:
    """Return each stop's excess and the onboard package state causing it.

    Capacity is checked before and after each pickup, delivery, transfer-in,
    and transfer-out. For a stop, the returned package set corresponds to the
    larger of its before/after capacity measurements.
    """
    package_loads = {
        item.package_id: item.load for item in instance.package_data.packages
    }
    capacities = {
        item.vehicle_id: item.cargo_capacity for item in instance.fleet_data.vehicles
    }
    onboard: dict[str, set[str]] = defaultdict(set)
    profile: dict[tuple[int, int], tuple[float, frozenset[str]]] = {}
    ordered = sorted(
        enumerate(solution.routes),
        key=lambda item: (item[1].day_index, item[1].vehicle_id),
    )
    for route_index, route in ordered:
        capacity = capacities.get(route.vehicle_id, 0.0)
        tool_loads = _tool_load_by_stop(instance, solution, route_index)
        loaded = onboard[route.vehicle_id]
        load = fsum(package_loads.get(item, 0.0) for item in sorted(loaded))
        if route.stops:
            profile[(route_index, 0)] = (
                max(0.0, load + tool_loads[0] - capacity),
                frozenset(loaded),
            )
        for stop_index, stop in enumerate(route.stops):
            tool_load = tool_loads[stop_index]
            load = fsum(package_loads.get(item, 0.0) for item in sorted(loaded))
            excess = max(0.0, load + tool_load - capacity)
            load_state = frozenset(loaded)
            package_id: str | None = None
            action: str | None = None
            if type(stop) is ActivityStop and stop.activity_ref.kind in {
                'pickup',
                'delivery',
            }:
                package_id = stop.activity_ref.activity_id
                action = stop.activity_ref.kind
            elif type(stop) is TransferOperationStop:
                package_id = stop.transfer_step_ref.package_id
                action = 'transfer_in' if stop.role == 'in' else 'transfer_out'
            if package_id is not None and action is not None:
                if action in {'pickup', 'transfer_in'}:
                    loaded.add(package_id)
                else:
                    loaded.discard(package_id)
                load = fsum(package_loads.get(item, 0.0) for item in sorted(loaded))
                after_excess = max(load + tool_load - capacity, 0.0)
                if after_excess > excess:
                    excess = after_excess
                    load_state = frozenset(loaded)
            profile[(route_index, stop_index)] = (excess, load_state)
    return profile


def _tool_load_by_stop(
    instance: LoadedVrpInstance, solution: VrpSolution, route_index: int
) -> tuple[float, ...]:
    route = solution.routes[route_index]
    if not route.stops:
        return ()
    depot_nodes = {item.node_id for item in instance.network_data.depots}
    depot_by_id = {item.depot_id: item.node_id for item in instance.network_data.depots}
    vehicle = next(
        item
        for item in instance.fleet_data.vehicles
        if item.vehicle_id == route.vehicle_id
    )
    prior_routes = [
        item
        for item in solution.routes
        if item.vehicle_id == route.vehicle_id and item.day_index < route.day_index
    ]
    start_depot_id = (
        max(prior_routes, key=lambda item: item.day_index).overnight_depot_id
        if prior_routes
        else vehicle.initial_depot_id
    )
    start_node = depot_by_id[start_depot_id]
    stop_nodes = tuple(stop_node_id(instance, solution, stop) for stop in route.stops)
    tools_by_intervention: dict[str, set[str]] = defaultdict(set)
    for relation in instance.intervention_data.intervention_tools:
        tools_by_intervention[relation.intervention_id].add(relation.tool_id)
    demand_by_tool = {
        item.tool_id: item.capacity_demand for item in instance.intervention_data.tools
    }
    loads = [0.0] * len(route.stops)
    segment_start = 0
    start_side_end = -1
    if stop_nodes[0] == start_node and not route.legs[0].arc_ids:
        start_side_end = 0
        while (
            start_side_end + 1 < len(route.stops)
            and stop_nodes[start_side_end + 1] == start_node
            and not route.legs[start_side_end + 1].arc_ids
        ):
            start_side_end += 1

    def assign_segment(start: int, end: int) -> None:
        required_tools = {
            tool_id
            for index in range(start, end + 1)
            for stop in (route.stops[index],)
            if type(stop) is ActivityStop and stop.activity_ref.kind == 'intervention'
            for tool_id in tools_by_intervention[stop.activity_ref.activity_id]
        }
        segment_load = fsum(demand_by_tool[item] for item in sorted(required_tools))
        for index in range(start, end + 1):
            loads[index] = segment_load

    index = start_side_end + 1
    while index < len(route.stops):
        if stop_nodes[index] not in depot_nodes:
            index += 1
            continue
        block_end = index
        while (
            block_end + 1 < len(route.stops)
            and stop_nodes[block_end + 1] == stop_nodes[index]
            and not route.legs[block_end + 1].arc_ids
        ):
            block_end += 1
        assign_segment(segment_start, block_end)
        segment_start = block_end + 1
        index = block_end + 1
    if segment_start < len(route.stops):
        assign_segment(segment_start, len(route.stops) - 1)
    elif start_side_end >= 0 and segment_start == 0:
        assign_segment(0, len(route.stops) - 1)
    return tuple(loads)
