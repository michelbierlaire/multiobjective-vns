"""Window-compatible reassignment of package-chain clusters.

Created: 2026-11-10T15:44:20+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import cmp_to_key

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    randomized_order,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    assess_complete_candidate,
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    _sample_integer_indices,
    _unrank_product,
    route_node_sequence,
    stop_node_id,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_elimination import (
    _relationship_groups,
    _service_units,
    _stop_unit_key,
    _UnitKey,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_assignment_helpers import (
    _bounded_limit,
    _rebase_service_waits,
    _rebuild_paths,
    _sort_routes,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteStop,
    TransferOperationStop,
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

__all__ = ['WindowCompatiblePackageClusterReassignmentOperator']

type _RepairUnit = tuple[_UnitKey, ...]
type _ScoredCandidate = tuple[VrpSolution, tuple[object, ...], int]


@dataclass(frozen=True, slots=True)
class _DayPlacement:
    """One day's source removal and destination insertion."""

    source_stops: tuple[RouteStop, ...]
    destination_stops: tuple[RouteStop, ...]


class WindowCompatiblePackageClusterReassignmentOperator:
    """Reassign complete package repair units among the base's active vehicles.

    A bounded randomized beam grows a cluster one package repair unit at a
    time. Each complete alternative is rebuilt, assessed, and ranked with the
    plan's full objective vector. The dedicated service-window objective, when
    present, selects the least-violating alternatives before objective ranking.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_target_position',
        '_window_position',
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
        self._maximum_size = max_neighborhood_size
        validate_neighborhood_size(1, self._maximum_size)
        self._candidate_limit = _bounded_limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _bounded_limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _bounded_limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return an ordered batch of complete reassignment candidates.

        :param context: Base plan, neighborhood size, feasibility request, and RNG.
        :returns: Distinct candidate plans with repair-unit modification counts.
        :raises TypeError: If the base or assessment callback has an invalid type.
        :raises ValueError: If a requested limit or objective vector is invalid.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        units = self._eligible_units(base)
        package_vehicles = self._package_serving_vehicles(base)
        if len(package_vehicles) < 2 or not units:
            return OperatorResult(candidates=())

        unit_order = randomized_order(units, context.random_source)
        beam: tuple[VrpSolution, ...] = (base,)
        collected: list[_ScoredCandidate] = []
        modifications = 0

        for unit in unit_order:
            if modifications >= size:
                break
            extensions: list[_ScoredCandidate] = []
            inspected = 0
            for partial_index, partial in enumerate(beam):
                if inspected >= self._candidate_limit:
                    break
                remaining_beam_states = len(beam) - partial_index
                remaining_budget = self._candidate_limit - inspected
                partial_limit = max(
                    1,
                    (remaining_budget + remaining_beam_states - 1)
                    // remaining_beam_states,
                )
                alternatives = self._reassign_unit(
                    partial,
                    unit,
                    context,
                    package_vehicles=package_vehicles,
                    alternative_limit=partial_limit,
                )
                for candidate in alternatives:
                    if inspected >= self._candidate_limit:
                        break
                    inspected += 1
                    if self._active_vehicles(candidate) != self._active_vehicles(base):
                        continue
                    evaluation = assess_complete_candidate(
                        self._instance,
                        self._objectives,
                        candidate,
                        self._assess_plan,
                        ensure_feasible=context.ensure_feasible,
                    )
                    if evaluation is None or evaluation.objective_values is None:
                        continue
                    self._append_unique(
                        extensions,
                        (candidate, evaluation.objective_values, modifications + 1),
                    )

            if not extensions:
                continue

            modifications += 1
            # Every branch in this growth layer has reassigned the same number
            # of distinct repair units, so the stored m is the layer number.
            extensions = [
                (plan, values, modifications) for plan, values, _old_m in extensions
            ]
            collected.extend(extensions)
            beam = tuple(
                item[0]
                for item in self._rank(extensions, context)[: self._candidate_limit]
            )

        ranked = self._rank(collected, context)
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution=plan), modifications_count)
                for plan, _values, modifications_count in ranked[
                    : context.max_candidates
                ]
            )
        )

    def _eligible_units(self, solution: VrpSolution) -> tuple[_RepairUnit, ...]:
        service_units = _service_units(solution)
        groups = _relationship_groups(self._instance, service_units)
        active_vehicles = self._active_vehicles(solution)
        package_vehicles = self._package_serving_vehicles(solution)
        if len(package_vehicles) < 2:
            return ()
        compatibility = {
            (item.vehicle_id, item.package_id): item.compatible
            for item in self._instance.package_data.vehicle_package_compatibility
        }
        transfer_packages = {item.package_id for item in solution.transfer_steps}
        transfer_packages.update(
            stop.transfer_step_ref.package_id
            for route in solution.routes
            for stop in route.stops
            if isinstance(stop, TransferOperationStop)
        )
        route_days = {(route.vehicle_id, route.day_index) for route in solution.routes}
        eligible: list[_RepairUnit] = []
        for group in groups:
            keys = tuple(sorted(unit.key for unit in group))
            package_ids = tuple(key[1] for key in keys if key and key[0] == 'package')
            if not package_ids or any(
                package_id in transfer_packages for package_id in package_ids
            ):
                continue
            occurrences = tuple(
                occurrence for unit in group for occurrence in unit.occurrences
            )
            if not occurrences or any(
                isinstance(stop, TransferOperationStop)
                for _route_index, _stop_index, stop in occurrences
            ):
                continue
            source_vehicles = {
                solution.routes[route_index].vehicle_id
                for route_index, _stop_index, _stop in occurrences
            }
            if len(source_vehicles) != 1:
                continue
            source_vehicle = next(iter(source_vehicles))
            required_days = {
                solution.routes[route_index].day_index
                for route_index, _stop_index, _stop in occurrences
            }
            has_complete_chain = all(
                self._has_complete_package_chain(solution, package_id)
                for package_id in package_ids
            )
            if not has_complete_chain:
                continue
            if not any(
                vehicle_id != source_vehicle
                and vehicle_id in package_vehicles
                and all((vehicle_id, day) in route_days for day in required_days)
                and all(
                    compatibility.get((vehicle_id, package_id), True)
                    for package_id in package_ids
                )
                for vehicle_id in active_vehicles
            ):
                continue
            eligible.append(keys)
        return tuple(eligible)

    def _has_complete_package_chain(
        self, solution: VrpSolution, package_id: str
    ) -> bool:
        kinds = {
            stop.activity_ref.kind
            for route in solution.routes
            for stop in route.stops
            if isinstance(stop, ActivityStop)
            and stop.activity_ref.activity_id == package_id
            and stop.activity_ref.kind in {'pickup', 'delivery'}
        }
        return kinds == {'pickup', 'delivery'}

    def _reassign_unit(
        self,
        solution: VrpSolution,
        unit: _RepairUnit,
        context: CandidateContext,
        *,
        package_vehicles: frozenset[str],
        alternative_limit: int,
    ) -> tuple[VrpSolution, ...]:
        key_set = frozenset(unit)
        occurrences = tuple(
            (route_index, stop_index, stop)
            for route_index, route in enumerate(solution.routes)
            for stop_index, stop in enumerate(route.stops)
            if _stop_unit_key(stop) in key_set
        )
        if not occurrences:
            return ()
        source_vehicles = {
            solution.routes[route_index].vehicle_id
            for route_index, _stop_index, _stop in occurrences
        }
        if len(source_vehicles) != 1:
            return ()
        source_vehicle = next(iter(source_vehicles))
        days = tuple(
            sorted(
                {
                    solution.routes[route_index].day_index
                    for route_index, _stop_index, _stop in occurrences
                }
            )
        )
        source_by_day = {
            day: next(
                index
                for index, route in enumerate(solution.routes)
                if route.vehicle_id == source_vehicle and route.day_index == day
            )
            for day in days
        }
        active_vehicles = self._active_vehicles(solution)
        package_ids = tuple(key[1] for key in unit if key[0] == 'package')
        compatibility = {
            (item.vehicle_id, item.package_id): item.compatible
            for item in self._instance.package_data.vehicle_package_compatibility
        }
        destinations = tuple(
            vehicle_id
            for vehicle_id in active_vehicles
            if vehicle_id in package_vehicles
            and vehicle_id != source_vehicle
            and all(
                (vehicle_id, day)
                in {(route.vehicle_id, route.day_index) for route in solution.routes}
                for day in days
            )
            and all(
                compatibility.get((vehicle_id, package_id), True)
                for package_id in package_ids
            )
        )
        if not destinations:
            return ()

        limit = min(alternative_limit, self._candidate_limit)
        result: list[VrpSolution] = []
        for destination_vehicle in randomized_order(
            destinations, context.random_source
        ):
            destination_by_day = {
                day: next(
                    index
                    for index, route in enumerate(solution.routes)
                    if route.vehicle_id == destination_vehicle
                    and route.day_index == day
                )
                for day in days
            }
            day_options: list[tuple[_DayPlacement, ...]] = []
            for day in days:
                source_index = source_by_day[day]
                destination_index = destination_by_day[day]
                source_route = solution.routes[source_index]
                destination_route = solution.routes[destination_index]
                moved_stops = tuple(
                    stop
                    for stop in source_route.stops
                    if _stop_unit_key(stop) in key_set
                )
                if not moved_stops:
                    day_options = []
                    break
                source_remaining = tuple(
                    stop
                    for stop in source_route.stops
                    if _stop_unit_key(stop) not in key_set
                )
                destination_start = route_node_sequence(
                    self._instance, solution, destination_route
                )[0]
                depot_pickups = tuple(
                    stop
                    for stop in moved_stops
                    if isinstance(stop, ActivityStop)
                    and stop.activity_ref.kind == 'pickup'
                    and stop_node_id(self._instance, solution, stop)
                    == destination_start
                )
                remaining_moved = tuple(
                    stop for stop in moved_stops if stop not in depot_pickups
                )
                destination_prefix = _initial_depot_pickup_count(
                    self._instance,
                    solution,
                    destination_route,
                    destination_start,
                )
                after_pickups = (
                    *destination_route.stops[:destination_prefix],
                    *depot_pickups,
                    *destination_route.stops[destination_prefix:],
                )
                first_position = destination_prefix + len(depot_pickups)
                positions = (
                    tuple(range(first_position, len(after_pickups) + 1))
                    if remaining_moved
                    else (first_position,)
                )
                day_options.append(
                    tuple(
                        _DayPlacement(
                            source_stops=source_remaining,
                            destination_stops=(
                                *after_pickups[:position],
                                *remaining_moved,
                                *after_pickups[position:],
                            ),
                        )
                        for position in positions
                    )
                )
            if not day_options:
                continue
            combinations = _sample_day_placements(
                day_options,
                limit - len(result),
                context,
            )
            for combination in combinations:
                if len(result) >= limit:
                    return tuple(result)
                route_updates: dict[tuple[str, int], tuple[RouteStop, ...]] = {}
                for day, placement in zip(days, combination, strict=True):
                    route_updates[(source_vehicle, day)] = placement.source_stops
                    route_updates[(destination_vehicle, day)] = (
                        placement.destination_stops
                    )
                routes = tuple(
                    replace(
                        route,
                        stops=route_updates[(route.vehicle_id, route.day_index)],
                    )
                    if (route.vehicle_id, route.day_index) in route_updates
                    else route
                    for route in solution.routes
                )
                routes = tuple(route for route in routes if route.stops)
                candidate = _sort_routes(
                    replace(solution, routes=routes), self._instance
                )
                with_paths = _rebuild_paths(
                    self._instance,
                    solution,
                    candidate,
                    context,
                    max_paths=self._path_limit,
                    max_expansions=self._expansion_limit,
                )
                if with_paths is None:
                    continue
                candidate = with_paths
                candidate = _rebase_service_waits(
                    self._instance,
                    self._objectives,
                    solution,
                    candidate,
                    context,
                    self._assess_plan,
                )
                if candidate != solution and candidate not in result:
                    result.append(candidate)
        return tuple(result)

    def _rank(
        self,
        candidates: Sequence[_ScoredCandidate],
        context: CandidateContext,
    ) -> tuple[_ScoredCandidate, ...]:
        remaining = list(_unique_scored(candidates))
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

        ordered: list[_ScoredCandidate] = []
        if self._target_position is None:
            ordered.extend(
                self._pareto_front_order(
                    remaining,
                    tuple(range(len(self._objectives.objectives))),
                    context,
                )
            )
            return tuple(ordered)

        target_position = self._target_position
        target_objective = self._objectives.objectives[target_position]

        def compare_target(
            left: _ScoredCandidate,
            right: _ScoredCandidate,
        ) -> int:
            comparison = target_objective.compare(
                left[1][target_position], right[1][target_position]
            )
            if comparison is ObjectiveComparison.LESS:
                return -1
            if comparison is ObjectiveComparison.GREATER:
                return 1
            return 0

        remaining.sort(key=cmp_to_key(compare_target))
        groups: list[list[_ScoredCandidate]] = []
        for candidate in remaining:
            if not groups or compare_target(groups[-1][0], candidate) != 0:
                groups.append([candidate])
            else:
                groups[-1].append(candidate)
        other_positions = tuple(
            position
            for position in range(len(self._objectives.objectives))
            if position != target_position
        )
        for group in groups:
            ordered.extend(self._pareto_front_order(group, other_positions, context))
        return tuple(ordered)

    def _pareto_front_order(
        self,
        candidates: Sequence[_ScoredCandidate],
        positions: tuple[int, ...],
        context: CandidateContext,
    ) -> tuple[_ScoredCandidate, ...]:
        remaining = list(candidates)
        ordered: list[_ScoredCandidate] = []
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
        left: _ScoredCandidate,
        right: _ScoredCandidate,
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
    def _append_unique(
        candidates: list[_ScoredCandidate], candidate: _ScoredCandidate
    ) -> None:
        if not any(previous[0] == candidate[0] for previous in candidates):
            candidates.append(candidate)

    @staticmethod
    def _active_vehicles(solution: VrpSolution) -> frozenset[str]:
        return frozenset(route.vehicle_id for route in solution.routes)

    @staticmethod
    def _package_serving_vehicles(solution: VrpSolution) -> frozenset[str]:
        return frozenset(
            route.vehicle_id
            for route in solution.routes
            if any(
                isinstance(stop, ActivityStop)
                and stop.activity_ref.kind in {'pickup', 'delivery'}
                for stop in route.stops
            )
        )


def _initial_depot_pickup_count(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route: VehicleDayRoute,
    start_node: str,
) -> int:
    count = 0
    for stop in route.stops:
        if (
            isinstance(stop, ActivityStop)
            and stop.activity_ref.kind == 'pickup'
            and stop_node_id(instance, solution, stop) == start_node
        ):
            count += 1
        else:
            break
    return count


def _sample_day_placements(
    day_options: Sequence[Sequence[_DayPlacement]],
    limit: int,
    context: CandidateContext,
) -> tuple[tuple[_DayPlacement, ...], ...]:
    if limit <= 0 or not day_options:
        return ()
    dimensions = tuple(len(options) for options in day_options)
    total = 1
    for dimension in dimensions:
        total *= dimension
    ranks = _sample_integer_indices(
        total,
        min(limit, total),
        context.random_source,
    )
    return tuple(
        tuple(
            options[index]
            for options, index in zip(
                day_options,
                _unrank_product(rank, dimensions),
                strict=True,
            )
        )
        for rank in ranks
    )


def _unique_scored(
    candidates: Sequence[_ScoredCandidate],
) -> tuple[_ScoredCandidate, ...]:
    unique: list[_ScoredCandidate] = []
    for candidate in candidates:
        if not any(previous[0] == candidate[0] for previous in unique):
            unique.append(candidate)
    return tuple(unique)
