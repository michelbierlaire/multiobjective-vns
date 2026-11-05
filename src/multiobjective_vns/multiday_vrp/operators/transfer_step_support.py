"""Shared validation and path helpers for transfer-step Operators.

Created: 2026-11-04T12:32:23+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Literal

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import choose_uniformly
from multiobjective_vns.multiday_vrp.operators.path_search import (
    route_node_sequence,
    sample_loopless_paths,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteLeg,
    RouteStop,
    TransferOperationStop,
    TransferStepRef,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = [
    'activity_occurrence',
    'compatibility',
    'delete_identity',
    'limit',
    'rebuild_routes',
    'route_index',
    'transfer_occurrence',
    'transfer_occurrences',
    'usable_evaluation',
]


def activity_occurrence(
    solution: VrpSolution,
    package_id: str,
    kind: Literal['pickup', 'delivery'],
) -> tuple[int, int, ActivityStop] | None:
    """Find one package activity occurrence by route and stop position."""
    for route_index, route in enumerate(solution.routes):
        for stop_index, stop in enumerate(route.stops):
            if type(stop) is ActivityStop and stop.activity_ref == ActivityRef(
                kind, package_id
            ):
                return route_index, stop_index, stop
    return None


def transfer_occurrence(
    solution: VrpSolution,
    package_id: str,
    step_number: int,
    role: str,
) -> tuple[int, int, TransferOperationStop] | None:
    """Find one outgoing or incoming transfer operation."""
    for route_index, route in enumerate(solution.routes):
        for stop_index, stop in enumerate(route.stops):
            if (
                type(stop) is TransferOperationStop
                and stop.transfer_step_ref == TransferStepRef(package_id, step_number)
                and stop.role == role
            ):
                return route_index, stop_index, stop
    return None


def transfer_occurrences(
    solution: VrpSolution, package_id: str, step_number: int
) -> (
    tuple[
        tuple[int, int, TransferOperationStop],
        tuple[int, int, TransferOperationStop],
    ]
    | None
):
    """Find the outgoing and incoming operations for one transfer step."""
    outgoing = transfer_occurrence(solution, package_id, step_number, 'out')
    incoming = transfer_occurrence(solution, package_id, step_number, 'in')
    return None if outgoing is None or incoming is None else (outgoing, incoming)


def route_index(solution: VrpSolution, vehicle_id: str, day: int) -> int | None:
    """Find a vehicle-day route index, when it is present in the plan."""
    return next(
        (
            index
            for index, route in enumerate(solution.routes)
            if route.vehicle_id == vehicle_id and route.day_index == day
        ),
        None,
    )


def delete_identity(
    stops: list[list[RouteStop]], route_index: int, target: RouteStop
) -> None:
    """Delete the exact stop object selected by a prior occurrence lookup."""
    index = next(
        (index for index, stop in enumerate(stops[route_index]) if stop is target),
        None,
    )
    if index is None:
        raise ValueError('selected transfer operation is absent from its route')
    del stops[route_index][index]


def compatibility(instance: LoadedVrpInstance, package_id: str) -> dict[str, bool]:
    """Return explicit vehicle compatibility for one package."""
    return {
        item.vehicle_id: item.compatible
        for item in instance.package_data.vehicle_package_compatibility
        if item.package_id == package_id
    }


def usable_evaluation(
    evaluation: ApplicationEvaluation, objectives: ObjectiveDeclaration
) -> bool:
    """Check that a plan evaluation contains a usable ordered objective vector."""
    return (
        evaluation.representation_valid
        and evaluation.feasible is not None
        and evaluation.objective_values is not None
        and type(evaluation.objective_values) is tuple
        and len(evaluation.objective_values) == len(objectives.objectives)
    )


def limit(value: int, name: str, maximum: int) -> int:
    """Validate a bounded positive Operator setting."""
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result


def rebuild_routes(
    instance: LoadedVrpInstance,
    original: VrpSolution,
    provisional: VrpSolution,
    route_indices: set[int],
    *,
    random_source: RandomSource,
    max_paths_per_leg: int,
    max_arc_expansion_steps: int,
    max_candidate_alternatives: int,
) -> tuple[VrpSolution, ...]:
    """Rebuild paths when transfer edits change stop counts or endpoint nodes."""
    dimensions: list[tuple[int, int, tuple[tuple[str, ...], ...]]] = []
    fixed: dict[int, list[tuple[str, ...] | None]] = {}
    for route_number in sorted(route_indices):
        old_route = original.routes[route_number]
        new_route = provisional.routes[route_number]
        old_nodes = route_node_sequence(instance, original, old_route)
        new_nodes = route_node_sequence(instance, provisional, new_route)
        if len(old_route.legs) != len(old_nodes) - 1:
            raise ValueError('base route legs do not align with route endpoints')
        old_by_endpoints: dict[tuple[str, str], list[tuple[str, ...]]] = defaultdict(
            list
        )
        for leg_number, leg in enumerate(old_route.legs):
            old_by_endpoints[(old_nodes[leg_number], old_nodes[leg_number + 1])].append(
                leg.arc_ids
            )
        route_legs: list[tuple[str, ...] | None] = []
        for leg_number, (origin, destination) in enumerate(
            zip(new_nodes[:-1], new_nodes[1:], strict=True)
        ):
            old_paths = old_by_endpoints[(origin, destination)]
            if old_paths:
                route_legs.append(old_paths.pop(0))
                continue
            paths = sample_loopless_paths(
                instance,
                origin,
                destination,
                random_source=random_source,
                max_paths=max_paths_per_leg,
                max_arc_expansion_steps=max_arc_expansion_steps,
            )
            if not paths:
                return ()
            route_legs.append(None)
            dimensions.append((route_number, leg_number, paths))
        fixed[route_number] = route_legs
    if not dimensions:
        return (provisional,) if provisional != original else ()

    candidates: list[VrpSolution] = []
    seen: set[tuple[tuple[str, ...], ...]] = set()
    for _ in range(max_candidate_alternatives * 4):
        routes = list(provisional.routes)
        for route_number, route_legs in fixed.items():
            completed = list(route_legs)
            for dimension_route, leg_number, paths in dimensions:
                if dimension_route == route_number:
                    completed[leg_number] = _choose_path(paths, random_source)
            if any(leg is None for leg in completed):
                raise RuntimeError('route path rebuilding left a leg unset')
            routes[route_number] = replace(
                routes[route_number],
                legs=tuple(RouteLeg(leg) for leg in completed if leg is not None),
            )
        candidate = replace(provisional, routes=tuple(routes))
        signature = tuple(
            tuple(arc for leg in route.legs for arc in leg.arc_ids)
            for route in candidate.routes
        )
        if signature in seen:
            continue
        seen.add(signature)
        if candidate != original:
            candidates.append(candidate)
        if len(candidates) >= max_candidate_alternatives:
            break
    return tuple(candidates)


def _choose_path(
    paths: tuple[tuple[str, ...], ...], random_source: RandomSource
) -> tuple[str, ...]:
    return choose_uniformly(paths, random_source)
