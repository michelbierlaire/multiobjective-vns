"""Shared candidate construction and scoring helpers.

Created: 2026-11-04T19:12:49+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import replace
from math import isfinite

from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    randomized_order,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    PathSearchIndex,
    route_node_sequence,
    sample_loopless_paths,
    stop_node_id,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteLeg,
    RouteStop,
    TransferOperationStop,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = []

type _StopKey = tuple[str, ...]


type _ScoredPlan = tuple[VrpSolution, tuple[object, ...]]


def _sample_interleavings(
    destination: tuple[RouteStop, ...],
    source: tuple[RouteStop, ...],
    limit: int,
    context: CandidateContext,
) -> tuple[tuple[RouteStop, ...], ...]:
    if not destination or not source:
        return ((*destination, *source),)
    candidates: list[tuple[RouteStop, ...]] = []
    _append_tuple_unique(candidates, (*destination, *source))
    if len(candidates) < limit:
        _append_tuple_unique(candidates, (*source, *destination))
    labels = (*((0,) * len(destination)), *((1,) * len(source)))
    attempts = max(limit * 20, 20)
    for _ in range(attempts):
        order = randomized_order(labels, context.random_source)
        candidate = _interleave_from_labels(destination, source, order)
        _append_tuple_unique(candidates, candidate)
        if len(candidates) >= limit:
            return tuple(candidates[:limit])
    for candidate in _iter_interleavings(destination, source):
        _append_tuple_unique(candidates, candidate)
        if len(candidates) >= limit:
            break
    return tuple(candidates[:limit])


def _interleave_from_labels(
    destination: tuple[RouteStop, ...],
    source: tuple[RouteStop, ...],
    labels: Sequence[int],
) -> tuple[RouteStop, ...]:
    left_index = 0
    right_index = 0
    merged: list[RouteStop] = []
    for label in labels:
        if label == 0:
            merged.append(destination[left_index])
            left_index += 1
        else:
            merged.append(source[right_index])
            right_index += 1
    return tuple(merged)


def _iter_interleavings(
    destination: tuple[RouteStop, ...], source: tuple[RouteStop, ...]
) -> Iterator[tuple[RouteStop, ...]]:
    def visit(
        left_index: int,
        right_index: int,
        result: tuple[RouteStop, ...],
    ) -> Iterator[tuple[RouteStop, ...]]:
        if left_index == len(destination) and right_index == len(source):
            yield result
            return
        if left_index < len(destination):
            yield from visit(
                left_index + 1,
                right_index,
                (*result, destination[left_index]),
            )
        if right_index < len(source):
            yield from visit(
                left_index,
                right_index + 1,
                (*result, source[right_index]),
            )

    yield from visit(0, 0, ())


def _sort_routes(solution: VrpSolution, instance: LoadedVrpInstance) -> VrpSolution:
    vehicle_order = {
        vehicle.vehicle_id: position
        for position, vehicle in enumerate(instance.fleet_data.vehicles)
    }
    routes = tuple(
        sorted(
            solution.routes,
            key=lambda route: (
                route.day_index,
                vehicle_order.get(route.vehicle_id, len(vehicle_order)),
            ),
        )
    )
    return replace(solution, routes=routes)


def _rebuild_paths(
    instance: LoadedVrpInstance,
    base: VrpSolution,
    candidate: VrpSolution,
    context: CandidateContext,
    *,
    max_paths: int,
    max_expansions: int,
    path_search_index: PathSearchIndex | None = None,
) -> VrpSolution | None:
    existing_paths: dict[tuple[str, str], list[tuple[str, ...]]] = defaultdict(list)
    for route in base.routes:
        nodes = route_node_sequence(instance, base, route)
        if len(route.legs) != len(nodes) - 1:
            return None
        for (origin, destination), leg in zip(
            zip(nodes[:-1], nodes[1:], strict=True), route.legs, strict=True
        ):
            existing_paths[(origin, destination)].append(leg.arc_ids)

    rebuilt_routes: list[VehicleDayRoute] = []
    for route in candidate.routes:
        nodes = route_node_sequence(instance, candidate, route)
        legs: list[RouteLeg] = []
        for origin, destination in zip(nodes[:-1], nodes[1:], strict=True):
            known = existing_paths.get((origin, destination), [])
            if known:
                arc_ids = choose_uniformly(known, context.random_source)
            else:
                paths = sample_loopless_paths(
                    instance,
                    origin,
                    destination,
                    random_source=context.random_source,
                    max_paths=max_paths,
                    max_arc_expansion_steps=max_expansions,
                    path_search_index=path_search_index,
                )
                if not paths:
                    return None
                arc_ids = choose_uniformly(paths, context.random_source)
            legs.append(RouteLeg(arc_ids))
        rebuilt_routes.append(replace(route, legs=tuple(legs)))
    return replace(candidate, routes=tuple(rebuilt_routes))


def _split_service_units(
    solution: VrpSolution, route_index: int
) -> tuple[tuple[int, ...], ...]:
    package_indices: dict[str, list[int]] = defaultdict(list)
    package_occurrences: dict[str, list[tuple[int, int]]] = defaultdict(list)
    transfer_occurrences: dict[tuple[str, int], dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    intervention_units: list[tuple[int, ...]] = []
    for current_route_index, current_route in enumerate(solution.routes):
        for stop_index, stop in enumerate(current_route.stops):
            package_id: str | None = None
            if isinstance(stop, ActivityStop):
                if stop.activity_ref.kind in {'pickup', 'delivery'}:
                    package_id = stop.activity_ref.activity_id
                elif stop.activity_ref.kind == 'intervention':
                    if current_route_index == route_index:
                        intervention_units.append((stop_index,))
            elif isinstance(stop, TransferOperationStop):
                package_id = stop.transfer_step_ref.package_id
                transfer_key = (package_id, stop.transfer_step_ref.step_number)
                transfer_occurrences[transfer_key][stop.role].append(
                    current_route_index
                )

            if package_id is not None:
                package_occurrences[package_id].append(
                    (current_route_index, stop_index)
                )
                if current_route_index == route_index:
                    package_indices[package_id].append(stop_index)

    declared_steps: dict[str, list[int]] = defaultdict(list)
    for step in solution.transfer_steps:
        declared_steps[step.package_id].append(step.step_number)
    observed_steps: dict[str, set[int]] = defaultdict(set)
    for package_id, step_number in transfer_occurrences:
        observed_steps[package_id].add(step_number)

    movable_package_indices: list[tuple[int, ...]] = []
    for package_id, indices in package_indices.items():
        occurrences = package_occurrences[package_id]
        if any(occurrence_route != route_index for occurrence_route, _ in occurrences):
            continue
        declared = declared_steps.get(package_id, ())
        if len(declared) != len(set(declared)) or set(declared) != observed_steps.get(
            package_id, set()
        ):
            continue
        if any(
            len(transfer_occurrences[(package_id, step_number)].get(role, ())) != 1
            or transfer_occurrences[(package_id, step_number)][role][0] != route_index
            for step_number in declared_steps.get(package_id, ())
            for role in ('out', 'in')
        ):
            continue
        movable_package_indices.append(tuple(indices))

    units = [
        *intervention_units,
        *movable_package_indices,
    ]
    units.sort(key=lambda indices: min(indices))
    return tuple(units)


def _sample_subsets(
    unit_count: int,
    limit: int,
    context: CandidateContext,
) -> tuple[tuple[int, ...], ...]:
    total = (1 << unit_count) - 2
    if total <= 0:
        return ()
    selected: list[tuple[int, ...]] = []
    seen: set[int] = set()
    full_mask = (1 << unit_count) - 1
    for _ in range(max(20, limit * 20)):
        mask = 0
        for bit in range(unit_count):
            draw = context.random_source.random()
            _validate_draw(draw)
            if draw < 0.5:
                mask |= 1 << bit
        if mask in {0, full_mask} or mask in seen:
            continue
        seen.add(mask)
        selected.append(tuple(bit for bit in range(unit_count) if mask & (1 << bit)))
        if len(selected) >= min(limit, total):
            return tuple(selected)
    mask = 1
    while len(selected) < min(limit, total):
        if mask not in seen and mask != full_mask:
            seen.add(mask)
            selected.append(
                tuple(bit for bit in range(unit_count) if mask & (1 << bit))
            )
        mask += 1
    return tuple(selected)


def _put_depot_pickups_first(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route: VehicleDayRoute,
    stops: tuple[RouteStop, ...],
) -> tuple[RouteStop, ...]:
    start_node = route_node_sequence(instance, solution, route)[0]
    pickups: list[RouteStop] = []
    remaining: list[RouteStop] = []
    for stop in stops:
        if (
            isinstance(stop, ActivityStop)
            and stop.activity_ref.kind == 'pickup'
            and stop_node_id(instance, solution, stop) == start_node
        ):
            pickups.append(stop)
        else:
            remaining.append(stop)
    return (*pickups, *remaining)


def _overnight_depots(instance: LoadedVrpInstance) -> dict[str, tuple[str, ...]]:
    domains: dict[str, list[str]] = defaultdict(list)
    for record in instance.fleet_data.vehicle_overnight_depots:
        domains[record.vehicle_id].append(record.depot_id)
    return {vehicle_id: tuple(sorted(depots)) for vehicle_id, depots in domains.items()}


def _qualified_drivers(instance: LoadedVrpInstance) -> dict[str, tuple[str, ...]]:
    skills_by_vehicle: dict[str, set[str]] = defaultdict(set)
    for vehicle_skill in instance.fleet_data.vehicle_driving_skills:
        skills_by_vehicle[vehicle_skill.vehicle_id].add(vehicle_skill.skill_id)
    skills_by_person: dict[str, set[str]] = defaultdict(set)
    for person_skill in instance.personnel_data.personnel_driving_skills:
        skills_by_person[person_skill.person_id].add(person_skill.skill_id)
    return {
        vehicle.vehicle_id: tuple(
            sorted(
                person.person_id
                for person in instance.personnel_data.personnel
                if person.authorized_driver
                and skills_by_vehicle[vehicle.vehicle_id]
                <= skills_by_person[person.person_id]
            )
        )
        for vehicle in instance.fleet_data.vehicles
    }


def _rebase_service_waits(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    base: VrpSolution,
    candidate: VrpSolution,
    context: CandidateContext,
    assess_plan: VrpPlanAssessor,
) -> VrpSolution:
    base_evaluation = _stored_application_evaluation(context)
    if base_evaluation is None:
        base_evaluation = assess_plan(instance, objectives, base)
    if not isinstance(base_evaluation, ApplicationEvaluation):
        raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
    base_analysis = base_evaluation.analysis
    if (
        not isinstance(base_analysis, VrpOperatorAnalysis)
        or not base_analysis.is_complete
    ):
        return candidate

    targets: dict[_StopKey, float] = {}
    for key in _plan_stop_keys(base):
        mean = _weighted_trace_value(
            base_analysis,
            base,
            key,
            'service_start',
            instance,
        )
        if mean is not None:
            targets[key] = mean
    if not targets:
        return candidate

    working = candidate
    for route_index in range(len(working.routes)):
        for stop_index in range(len(working.routes[route_index].stops)):
            route = working.routes[route_index]
            stop = route.stops[stop_index]
            if not isinstance(stop, (ActivityStop, TransferOperationStop)):
                continue
            stop_key = _stop_key(stop)
            if stop_key is None:
                continue
            target = targets.get(stop_key)
            if target is None:
                continue
            evaluation = assess_plan(instance, objectives, working)
            if not isinstance(evaluation, ApplicationEvaluation):
                raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
            analysis = evaluation.analysis
            if (
                not isinstance(analysis, VrpOperatorAnalysis)
                or not analysis.is_complete
            ):
                continue
            arrival = _weighted_trace_value(
                analysis,
                working,
                stop_key,
                'arrival_time',
                instance,
            )
            if arrival is None:
                continue
            wait = max(0.0, target - arrival)
            if wait == stop.pre_service_wait:
                continue
            current_routes = list(working.routes)
            current_stops = list(route.stops)
            current_stops[stop_index] = replace(stop, pre_service_wait=wait)
            current_routes[route_index] = replace(route, stops=tuple(current_stops))
            working = replace(working, routes=tuple(current_routes))
    return working


def _stored_application_evaluation(
    context: CandidateContext,
) -> ApplicationEvaluation | None:
    record = context.base_evaluation
    if record is None:
        return None
    evaluation = record.application_evaluation
    return evaluation if isinstance(evaluation, ApplicationEvaluation) else None


def _plan_stop_keys(solution: VrpSolution) -> tuple[_StopKey, ...]:
    keys = [
        key
        for route in solution.routes
        for stop in route.stops
        if (key := _stop_key(stop)) is not None
    ]
    counts: dict[_StopKey, int] = defaultdict(int)
    for key in keys:
        counts[key] += 1
    return tuple(key for key in keys if counts[key] == 1)


def _stop_key(stop: RouteStop) -> _StopKey | None:
    if isinstance(stop, ActivityStop):
        return ('activity', stop.activity_ref.kind, stop.activity_ref.activity_id)
    if isinstance(stop, TransferOperationStop):
        return (
            'transfer',
            stop.transfer_step_ref.package_id,
            str(stop.transfer_step_ref.step_number),
            stop.role,
        )
    return None


def _weighted_trace_value(
    analysis: VrpOperatorAnalysis,
    solution: VrpSolution,
    key: _StopKey,
    field: str,
    instance: LoadedVrpInstance,
) -> float | None:
    scenario_by_id = {item.scenario_id: item for item in analysis.scenario_evaluations}
    expected_ids = tuple(item.scenario_id for item in instance.scenario_bank.scenarios)
    if not expected_ids or any(
        scenario_id not in scenario_by_id for scenario_id in expected_ids
    ):
        return None
    routes = {(route.vehicle_id, route.day_index): route for route in solution.routes}
    weighted_total = 0.0
    total_probability = 0.0
    for scenario_id in expected_ids:
        scenario = scenario_by_id[scenario_id]
        matches: list[float] = []
        for vehicle_day in scenario.vehicle_days:
            route = routes.get((vehicle_day.vehicle_id, vehicle_day.day_index))
            if route is None:
                continue
            for stop_trace in vehicle_day.stops:
                if not 0 <= stop_trace.stop_index < len(route.stops):
                    continue
                if _stop_key(route.stops[stop_trace.stop_index]) != key:
                    continue
                value = getattr(stop_trace, field)
                if type(value) is float and isfinite(value):
                    matches.append(value)
        if len(matches) != 1:
            return None
        weighted_total += scenario.probability * matches[0]
        total_probability += scenario.probability
    if total_probability <= 0.0 or not isfinite(weighted_total):
        return None
    return weighted_total / total_probability


def _choose_scored(
    candidates: Sequence[_ScoredPlan],
    objectives: ObjectiveDeclaration,
    position: int | None,
    context: CandidateContext,
) -> _ScoredPlan:
    if not candidates:
        raise ValueError('at least one assessed candidate is required')
    if position is None:
        return choose_uniformly(candidates, context.random_source)
    objective = objectives.objectives[position]
    best = candidates[0]
    tied = [best]
    for candidate in candidates[1:]:
        comparison = objective.compare(candidate[1][position], best[1][position])
        if comparison is ObjectiveComparison.LESS:
            best = candidate
            tied = [candidate]
        elif comparison is ObjectiveComparison.EQUAL:
            tied.append(candidate)
    return choose_uniformly(tied, context.random_source)


def _append_unique(candidates: list[VrpSolution], candidate: VrpSolution) -> None:
    if candidate not in candidates:
        candidates.append(candidate)


def _append_tuple_unique(
    candidates: list[tuple[RouteStop, ...]], candidate: tuple[RouteStop, ...]
) -> None:
    if candidate not in candidates:
        candidates.append(candidate)


def _validate_draw(value: object) -> float:
    if type(value) is not float:
        raise TypeError('RandomSource.random() must return a Python float')
    if not isfinite(value) or not 0.0 <= value < 1.0:
        raise ValueError('RandomSource.random() must return a float in [0, 1)')
    return value


def _bounded_limit(value: int, name: str, maximum: int) -> int:
    normalized = require_integer(value, name=name, minimum=1)
    if normalized > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return normalized
