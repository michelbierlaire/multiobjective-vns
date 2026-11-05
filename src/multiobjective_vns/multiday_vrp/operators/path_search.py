"""Directed loopless path sampling and route endpoint resolution.

Created: 2026-11-03T19:21:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import randomized_order
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
    RouteStop,
    TransferOperationStop,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.values import require_integer

__all__ = [
    'rebuild_changed_routes',
    'route_node_sequence',
    'sample_loopless_paths',
    'stop_node_id',
]


@dataclass(slots=True)
class _SearchFrame:
    node_id: str
    node_path: tuple[str, ...]
    arc_path: tuple[str, ...]
    outgoing_arcs: tuple[tuple[str, str], ...] | None = None
    next_arc_index: int = 0


def sample_loopless_paths(
    instance: LoadedVrpInstance,
    origin_node_id: str,
    destination_node_id: str,
    *,
    random_source: RandomSource,
    max_paths: int,
    max_arc_expansion_steps: int,
) -> tuple[tuple[str, ...], ...]:
    """Sample bounded directed loopless paths in randomized depth-first order.

    At every visited node, outgoing arcs are sorted by ID and then shuffled
    through the shared random source. Each outgoing arc considered consumes
    one expansion step before the looplessness check. Parallel arcs remain
    distinct paths.

    :param instance: Validated network containing the directed arc catalog.
    :param origin_node_id: Path origin node.
    :param destination_node_id: Path destination node.
    :param random_source: Shared search random source.
    :param max_paths: Maximum number of paths to return.
    :param max_arc_expansion_steps: Maximum outgoing-arc considerations.
    :returns: Distinct arc-ID paths in randomized depth-first order.
    :raises TypeError: If either limit is not an integer excluding bool.
    :raises ValueError: If a limit is nonpositive or an endpoint is unknown.
    """
    path_limit = require_integer(max_paths, name='max_paths', minimum=1)
    expansion_limit = require_integer(
        max_arc_expansion_steps,
        name='max_arc_expansion_steps',
        minimum=1,
    )
    known_nodes = {record.node_id for record in instance.network_data.nodes}
    if origin_node_id not in known_nodes or destination_node_id not in known_nodes:
        raise ValueError('route path endpoints must be known network nodes')
    if origin_node_id == destination_node_id:
        return ((),)

    outgoing: dict[str, list[tuple[str, str]]] = {}
    for arc in instance.network_data.arcs:
        outgoing.setdefault(arc.tail_node_id, []).append((arc.arc_id, arc.head_node_id))

    paths: list[tuple[str, ...]] = []
    stack = [_SearchFrame(origin_node_id, (origin_node_id,), ())]
    expansion_steps = 0
    while stack and len(paths) < path_limit:
        current = stack[-1]
        if current.node_id == destination_node_id:
            paths.append(current.arc_path)
            stack.pop()
            continue
        if expansion_steps >= expansion_limit:
            break
        if current.outgoing_arcs is None:
            current = _SearchFrame(
                current.node_id,
                current.node_path,
                current.arc_path,
                tuple(
                    randomized_order(
                        tuple(sorted(outgoing.get(current.node_id, ()))),
                        random_source,
                    )
                ),
                current.next_arc_index,
            )
            stack[-1] = current
        assert current.outgoing_arcs is not None
        if current.next_arc_index >= len(current.outgoing_arcs):
            stack.pop()
            continue
        arc_id, head_node_id = current.outgoing_arcs[current.next_arc_index]
        stack[-1] = _SearchFrame(
            current.node_id,
            current.node_path,
            current.arc_path,
            current.outgoing_arcs,
            current.next_arc_index + 1,
        )
        expansion_steps += 1
        if head_node_id in current.node_path:
            continue
        stack.append(
            _SearchFrame(
                head_node_id,
                (*current.node_path, head_node_id),
                (*current.arc_path, arc_id),
            )
        )
    return tuple(paths)


def stop_node_id(
    instance: LoadedVrpInstance, solution: VrpSolution, stop: RouteStop
) -> str:
    """Resolve a route stop's physical node through the immutable instance."""
    if type(stop) is DepotOnlyStop:
        return stop.node_id
    if type(stop) is ActivityStop:
        activity_reference = stop.activity_ref
        if activity_reference.kind == 'pickup':
            for package in instance.package_data.packages:
                if package.package_id == activity_reference.activity_id:
                    return package.pickup_node_id
        elif activity_reference.kind == 'delivery':
            for package in instance.package_data.packages:
                if package.package_id == activity_reference.activity_id:
                    return package.delivery_node_id
        elif activity_reference.kind == 'intervention':
            for intervention in instance.intervention_data.interventions:
                if intervention.intervention_id == activity_reference.activity_id:
                    return intervention.node_id
        raise ValueError(f'unknown route activity {activity_reference!r}')
    if type(stop) is TransferOperationStop:
        transfer_reference = stop.transfer_step_ref
        for transfer in solution.transfer_steps:
            if (
                transfer.package_id == transfer_reference.package_id
                and transfer.step_number == transfer_reference.step_number
            ):
                return transfer.facility_id
        raise ValueError(f'unknown transfer step {transfer_reference!r}')
    raise TypeError('route stop must be a supported RouteStop variant')


def route_node_sequence(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route: VehicleDayRoute,
) -> tuple[str, ...]:
    """Return a route's start node, stop nodes, and overnight depot node."""
    vehicle = next(
        (
            item
            for item in instance.fleet_data.vehicles
            if item.vehicle_id == route.vehicle_id
        ),
        None,
    )
    if vehicle is None:
        raise ValueError(f'unknown route vehicle {route.vehicle_id!r}')
    earlier_routes = tuple(
        item
        for item in solution.routes
        if item.vehicle_id == route.vehicle_id and item.day_index < route.day_index
    )
    if earlier_routes:
        prior_route = max(earlier_routes, key=lambda item: item.day_index)
        start_depot_id = prior_route.overnight_depot_id
    else:
        start_depot_id = vehicle.initial_depot_id
    depot_nodes = {item.depot_id: item.node_id for item in instance.network_data.depots}
    try:
        start_node = depot_nodes[start_depot_id]
        end_node = depot_nodes[route.overnight_depot_id]
    except KeyError as error:
        raise ValueError(f'unknown route depot {error.args[0]!r}') from error
    return (
        start_node,
        *(stop_node_id(instance, solution, stop) for stop in route.stops),
        end_node,
    )


def rebuild_changed_routes(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    stops_by_route_index: Mapping[int, tuple[RouteStop, ...]],
    *,
    random_source: RandomSource,
    max_paths_per_leg: int,
    max_arc_expansion_steps: int,
    max_candidate_alternatives: int,
) -> tuple[VrpSolution, ...]:
    """Rebuild changed route legs and sample complete path assignments.

    Existing legs are retained wherever their ordered endpoint pair remains
    unchanged. Every new endpoint pair receives bounded loopless paths from
    :func:`sample_loopless_paths`; complete assignments are sampled uniformly
    without replacement from their Cartesian product.
    """
    path_limit = require_integer(max_paths_per_leg, name='max_paths_per_leg', minimum=1)
    expansion_limit = require_integer(
        max_arc_expansion_steps,
        name='max_arc_expansion_steps',
        minimum=1,
    )
    candidate_limit = require_integer(
        max_candidate_alternatives,
        name='max_candidate_alternatives',
        minimum=1,
    )
    provisional_routes = list(solution.routes)
    for route_index, stops in stops_by_route_index.items():
        index = require_integer(route_index, name='route index', minimum=0)
        if index >= len(provisional_routes):
            raise ValueError('route index is outside the solution route tuple')
        if type(stops) is not tuple:
            raise TypeError('replacement stops must be a tuple')
        provisional_routes[index] = replace(provisional_routes[index], stops=stops)
    provisional_solution = replace(solution, routes=tuple(provisional_routes))

    path_dimensions: list[tuple[int, int, tuple[tuple[str, ...], ...]]] = []
    leg_blueprints: dict[int, list[tuple[str, ...] | None]] = {}
    for route_index in sorted(stops_by_route_index):
        original_route = solution.routes[route_index]
        candidate_route = provisional_solution.routes[route_index]
        old_nodes = route_node_sequence(instance, solution, original_route)
        new_nodes = route_node_sequence(instance, provisional_solution, candidate_route)
        if len(original_route.legs) != len(old_nodes) - 1:
            raise ValueError('base route legs do not align with route endpoints')
        old_by_endpoints: dict[tuple[str, str], list[tuple[str, ...]]] = defaultdict(
            list
        )
        for leg_index, leg in enumerate(original_route.legs):
            old_by_endpoints[(old_nodes[leg_index], old_nodes[leg_index + 1])].append(
                leg.arc_ids
            )
        blueprint: list[tuple[str, ...] | None] = []
        for leg_index, (origin, destination) in enumerate(
            zip(new_nodes[:-1], new_nodes[1:], strict=True)
        ):
            key = (origin, destination)
            if old_by_endpoints[key]:
                blueprint.append(old_by_endpoints[key].pop(0))
                continue
            paths = sample_loopless_paths(
                instance,
                origin,
                destination,
                random_source=random_source,
                max_paths=path_limit,
                max_arc_expansion_steps=expansion_limit,
            )
            if not paths:
                return ()
            blueprint.append(None)
            path_dimensions.append((route_index, leg_index, paths))
        leg_blueprints[route_index] = blueprint

    if not path_dimensions:
        candidate = provisional_solution
        return () if candidate == solution else (candidate,)

    dimension_sizes = tuple(len(paths) for _, _, paths in path_dimensions)
    assignment_count = 1
    for size in dimension_sizes:
        assignment_count *= size
    ranks = _sample_integer_indices(
        assignment_count,
        min(candidate_limit, assignment_count),
        random_source,
    )
    candidates: list[VrpSolution] = []
    for rank in ranks:
        choices = _unrank_product(rank, dimension_sizes)
        completed_routes = list(provisional_solution.routes)
        for route_index in sorted(stops_by_route_index):
            blueprint = leg_blueprints[route_index]
            completed_legs = [
                RouteLeg(path) if path is not None else None for path in blueprint
            ]
            for dimension_index, (dimension_route, leg_index, paths) in enumerate(
                path_dimensions
            ):
                if dimension_route == route_index:
                    completed_legs[leg_index] = RouteLeg(
                        paths[choices[dimension_index]]
                    )
            if any(leg is None for leg in completed_legs):
                raise RuntimeError('sampled path assignment left a route leg unset')
            completed_routes[route_index] = replace(
                completed_routes[route_index],
                legs=tuple(leg for leg in completed_legs if leg is not None),
            )
        candidate = replace(provisional_solution, routes=tuple(completed_routes))
        if candidate != solution:
            candidates.append(candidate)
    return tuple(candidates)


def _sample_integer_indices(
    population_size: int,
    sample_size: int,
    random_source: RandomSource,
) -> tuple[int, ...]:
    """Sample distinct integer ranks with sparse partial Fisher-Yates."""
    substitutions: dict[int, int] = {}
    selected: list[int] = []
    for position in range(sample_size):
        remaining = population_size - position
        target = position + _random_below(remaining, random_source)
        selected_value = substitutions.get(target, target)
        substitutions[target] = substitutions.get(position, position)
        selected.append(selected_value)
    return tuple(selected)


def _random_below(population_size: int, random_source: RandomSource) -> int:
    """Draw a uniform integer below an arbitrary positive bound."""
    if population_size == 1:
        return 0
    bit_count = population_size.bit_length()
    draw_count = (bit_count + 52) // 53
    mask = (1 << bit_count) - 1
    while True:
        value = 0
        for _ in range(draw_count):
            draw = random_source.random()
            if type(draw) is not float:
                raise TypeError('RandomSource.random() must return a Python float')
            if not 0.0 <= draw < 1.0:
                raise ValueError('RandomSource.random() must return a float in [0, 1)')
            value = (value << 53) | int(draw * (1 << 53))
        value &= mask
        if value < population_size:
            return value


def _unrank_product(rank: int, dimensions: tuple[int, ...]) -> tuple[int, ...]:
    """Map a product rank to one zero-based choice per dimension."""
    choices = [0] * len(dimensions)
    for index in range(len(dimensions) - 1, -1, -1):
        choices[index] = rank % dimensions[index]
        rank //= dimensions[index]
    return tuple(choices)
