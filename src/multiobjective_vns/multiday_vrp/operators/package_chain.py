"""Package pickup-delivery chain repair Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from functools import cache, cmp_to_key
from math import comb
from typing import Literal

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    randomized_order,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import objective_position
from multiobjective_vns.multiday_vrp.operators.path_search import (
    build_path_search_index,
    rebuild_changed_routes,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteStop,
    TransferOperationStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

type PackageChainMode = Literal['requests', 'compatibility']
type _PackageEvent = tuple[int, int, RouteStop]
type _PackageGroups = dict[int, list[_PackageEvent]]
type _PackageCandidate = tuple[VrpSolution, tuple[object, ...]]
type _InsertionSignature = tuple[tuple[str, ...], tuple[tuple[int, int, int], ...]]

__all__ = ['PackageChainMode', 'PackageChainRelocationOperator']


class PackageChainRelocationOperator:
    """Destroy and rebuild complete served package custody chains.

    Compatibility mode preserves a chain's transfer count, facilities, and
    paired operation references while assigning only compatible carriers.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_mode',
        '_objective_position',
        '_maximum_size',
        '_candidate_limit',
        '_path_limit',
        '_expansion_limit',
        '_packages_by_id',
        '_compatibility_by_package',
        '_compatible_vehicles_by_package',
        '_path_search_index',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        mode: PackageChainMode,
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        if mode not in {'requests', 'compatibility'}:
            raise ValueError(f'unsupported package chain mode: {mode!r}')
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._mode = mode
        self._objective_position = (
            None
            if objective_id is None
            else objective_position(objective_declaration, objective_id)
        )
        self._maximum_size = require_integer(
            max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )
        self._candidate_limit = _limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )
        self._path_search_index = build_path_search_index(instance)
        self._packages_by_id = {
            item.package_id: item for item in instance.package_data.packages
        }
        compatibility_by_package: dict[str, dict[str, bool]] = {}
        for record in instance.package_data.vehicle_package_compatibility:
            compatibility_by_package.setdefault(record.package_id, {})[
                record.vehicle_id
            ] = record.compatible
        self._compatibility_by_package = compatibility_by_package
        vehicle_ids = tuple(
            sorted(vehicle.vehicle_id for vehicle in instance.fleet_data.vehicles)
        )
        self._compatible_vehicles_by_package = {
            package_id: tuple(
                vehicle_id
                for vehicle_id in vehicle_ids
                if compatibility_by_package.get(package_id, {}).get(vehicle_id, True)
            )
            for package_id in self._packages_by_id
        }

    def call(self, context: CandidateContext) -> OperatorResult:
        """Repair up to ``k`` package chains and return complete candidates.

        :param context: Search base, requested neighborhood size, feasibility
            request, shared random source, and candidate batch limit.
        :returns: Up to ``context.max_candidates`` complete candidate plans.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        if self._mode == 'requests':
            return self._call_requests(base, size, context)
        working = base
        changed_packages: set[str] = set()
        modifications = 0
        last_candidates: tuple[_PackageCandidate, ...] = ()
        for _ in range(size):
            package_ids = tuple(
                package_id
                for package_id in self._eligible_packages(working)
                if package_id not in changed_packages
            )
            if not package_ids:
                break
            package_id = choose_uniformly(package_ids, context.random_source)
            candidates = self._candidates(working, package_id, context)
            changed_packages.add(package_id)
            if not candidates:
                continue
            last_candidates = _order_candidates(
                candidates,
                self._objectives,
                self._objective_position,
                context.random_source,
            )
            working = last_candidates[0][0]
            modifications += 1
        if not modifications:
            return OperatorResult(candidates=())
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), modifications)
                for solution, _values in last_candidates[: context.max_candidates]
            )
        )

    def _call_requests(
        self,
        base: VrpSolution,
        size: int,
        context: CandidateContext,
    ) -> OperatorResult:
        """Repair a fixed uniformly selected chain set in independent trials."""
        groups_by_package = _package_events_by_package(base)
        eligible = tuple(sorted(groups_by_package))
        selected = tuple(
            sorted(
                randomized_sample(
                    eligible, min(size, len(eligible)), context.random_source
                )
            )
        )
        if not selected:
            return OperatorResult(candidates=())

        selected_stop_ids = {
            (route_index, id(stop))
            for package_id in selected
            for events in groups_by_package[package_id].values()
            for route_index, _rank, stop in events
        }
        partial_stops = {
            route_index: tuple(
                stop
                for stop in route.stops
                if (route_index, id(stop)) not in selected_stop_ids
            )
            for route_index, route in enumerate(base.routes)
            if any((route_index, id(stop)) in selected_stop_ids for stop in route.stops)
        }
        partial_routes = tuple(
            replace(route, stops=partial_stops[index])
            if index in partial_stops
            else route
            for index, route in enumerate(base.routes)
        )
        partial_base = replace(base, routes=partial_routes)
        source_route_indices = set(partial_stops)
        route_by_vehicle_day = {
            (route.vehicle_id, route.day_index): index
            for index, route in enumerate(base.routes)
        }
        prepared: dict[
            str,
            tuple[
                _PackageGroups,
                tuple[int, ...],
                dict[int, tuple[str, ...]],
                dict[int, dict[str, tuple[int, ...]]],
            ],
        ] = {}
        for package_id in selected:
            groups = groups_by_package[package_id]
            group_indices = tuple(sorted(groups))
            vehicle_options: dict[int, tuple[str, ...]] = {}
            route_options: dict[int, dict[str, tuple[int, ...]]] = {}
            compatible_vehicles = self._compatible_vehicles_by_package.get(
                package_id, ()
            )
            for group_index in group_indices:
                events = tuple(
                    sorted(groups[group_index], key=lambda item: (item[1], item[0]))
                )
                required_days = {
                    base.routes[route_index].day_index
                    for route_index, _rank, _stop in events
                }
                options: list[str] = []
                routes_by_vehicle: dict[str, tuple[int, ...]] = {}
                for vehicle_id in compatible_vehicles:
                    if not all(
                        (vehicle_id, day_index) in route_by_vehicle_day
                        for day_index in required_days
                    ):
                        continue
                    options.append(vehicle_id)
                    routes_by_vehicle[vehicle_id] = tuple(
                        route_by_vehicle_day[
                            (vehicle_id, base.routes[route_index].day_index)
                        ]
                        for route_index, _rank, _stop in events
                    )
                vehicle_options[group_index] = tuple(options)
                route_options[group_index] = routes_by_vehicle
            prepared[package_id] = (
                groups,
                group_indices,
                vehicle_options,
                route_options,
            )

        candidates: list[_PackageCandidate] = []
        modifications_by_solution: dict[VrpSolution, int] = {}
        seen_solutions: set[VrpSolution] = set()
        for _trial in range(self._candidate_limit):
            working = partial_base
            trial_changed_route_indices = set(source_route_indices)
            complete = True
            for package_id in selected:
                groups, group_indices, vehicle_options, route_options = prepared[
                    package_id
                ]
                assignment = _sample_uniform_carrier_sequence(
                    group_indices,
                    vehicle_options,
                    route_options,
                    tuple(len(route.stops) for route in working.routes),
                    context.random_source,
                )
                if assignment is None:
                    complete = False
                    break
                inserted_stops = self._insert_chain_stops(
                    working,
                    package_id,
                    groups,
                    group_indices,
                    assignment,
                    route_by_vehicle_day,
                    context,
                    set(),
                    remove_existing_stops=False,
                )
                if inserted_stops is None:
                    complete = False
                    break
                routes = list(working.routes)
                for route_index, stops in inserted_stops.items():
                    routes[route_index] = replace(routes[route_index], stops=stops)
                working = replace(working, routes=tuple(routes))
                trial_changed_route_indices.update(inserted_stops)
            if not complete:
                continue
            rebuilt = rebuild_changed_routes(
                self._instance,
                base,
                {
                    route_index: working.routes[route_index].stops
                    for route_index in trial_changed_route_indices
                },
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=1,
            )
            if not rebuilt:
                continue
            candidate = rebuilt[0]
            if candidate in seen_solutions:
                continue
            modifications = sum(
                _chain_placement(candidate, package_id)
                != _chain_placement(base, package_id)
                for package_id in selected
            )
            if modifications == 0:
                continue
            seen_solutions.add(candidate)
            evaluation = self._assess_plan(self._instance, self._objectives, candidate)
            if not isinstance(evaluation, ApplicationEvaluation):
                raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
            if (
                not evaluation.representation_valid
                or evaluation.objective_values is None
            ):
                continue
            if type(evaluation.objective_values) is not tuple:
                raise TypeError('objective_values must be a tuple')
            if len(evaluation.objective_values) != len(self._objectives.objectives):
                raise ValueError('objective vector length must match declaration')
            if context.ensure_feasible and evaluation.feasible is not True:
                continue
            modifications_by_solution[candidate] = modifications
            candidates.append((candidate, evaluation.objective_values))

        ordered = _order_candidates(
            tuple(candidates),
            self._objectives,
            self._objective_position,
            context.random_source,
        )
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(
                    Candidate(solution), modifications_by_solution[solution]
                )
                for solution, _values in ordered[: context.max_candidates]
            )
        )

    def _eligible_packages(self, solution: VrpSolution) -> tuple[str, ...]:
        groups_by_package = _package_events_by_package(solution)
        candidates = tuple(sorted(groups_by_package))
        if self._mode == 'requests' or not candidates:
            return candidates
        current_incompatible = tuple(
            package_id
            for package_id in candidates
            if any(
                not self._compatibility_by_package.get(package_id, {}).get(
                    solution.routes[route_index].vehicle_id, True
                )
                for events in groups_by_package[package_id].values()
                for route_index, _rank, _stop in events
            )
        )
        if current_incompatible:
            return current_incompatible
        minimum = min(
            len(self._compatible_vehicles_by_package.get(package_id, ()))
            for package_id in candidates
        )
        return tuple(
            package_id
            for package_id in candidates
            if len(self._compatible_vehicles_by_package.get(package_id, ())) == minimum
        )

    def _candidates(
        self,
        solution: VrpSolution,
        package_id: str,
        context: CandidateContext,
    ) -> tuple[_PackageCandidate, ...]:
        groups = _package_events(solution, package_id)
        if not groups:
            return ()
        group_indices = tuple(sorted(groups))
        route_by_vehicle_day = {
            (route.vehicle_id, route.day_index): index
            for index, route in enumerate(solution.routes)
        }
        compatible_vehicles = self._compatible_vehicles_by_package.get(package_id, ())
        vehicle_options: dict[int, tuple[str, ...]] = {}
        for group_index in group_indices:
            required_days = {
                solution.routes[route_index].day_index
                for route_index, _rank, _stop in groups[group_index]
            }
            vehicle_options[group_index] = tuple(
                vehicle_id
                for vehicle_id in compatible_vehicles
                if all(
                    (vehicle_id, day_index) in route_by_vehicle_day
                    for day_index in required_days
                )
            )
            if not vehicle_options[group_index]:
                return ()

        assignments = _sample_carrier_sequences(
            group_indices,
            vehicle_options,
            self._candidate_limit,
            context.random_source,
        )
        original_placement = _chain_placement(solution, package_id)
        candidates: list[_PackageCandidate] = []
        seen_solutions: set[VrpSolution] = set()
        seen_insertions: set[_InsertionSignature] = set()
        if not assignments:
            return ()
        for attempt in range(self._candidate_limit * 8):
            assignment = assignments[attempt % len(assignments)]
            for candidate in self._insert_chain(
                solution,
                package_id,
                groups,
                group_indices,
                assignment,
                route_by_vehicle_day,
                context,
                seen_insertions,
            ):
                if (
                    candidate == solution
                    or _chain_placement(candidate, package_id) == original_placement
                    or candidate in seen_solutions
                ):
                    continue
                evaluation = self._assess_plan(
                    self._instance, self._objectives, candidate
                )
                if not isinstance(evaluation, ApplicationEvaluation):
                    raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
                if (
                    not evaluation.representation_valid
                    or evaluation.objective_values is None
                ):
                    continue
                if type(evaluation.objective_values) is not tuple:
                    raise TypeError('objective_values must be a tuple')
                if len(evaluation.objective_values) != len(self._objectives.objectives):
                    raise ValueError(
                        'objective vector length must match the declaration'
                    )
                if context.ensure_feasible and evaluation.feasible is not True:
                    continue
                seen_solutions.add(candidate)
                candidates.append((candidate, evaluation.objective_values))
                if len(candidates) >= self._candidate_limit:
                    return tuple(candidates)
        return tuple(candidates)

    def _candidate_placements_for_package(
        self,
        solution: VrpSolution,
        package_id: str,
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        """Sample distinct custody-preserving placements of one served chain."""
        groups = _package_events_by_package(solution).get(package_id, {})
        group_indices = tuple(sorted(groups))
        if not group_indices:
            return ()

        route_by_vehicle_day = {
            (route.vehicle_id, route.day_index): index
            for index, route in enumerate(solution.routes)
        }
        vehicle_options: dict[int, tuple[str, ...]] = {}
        route_options: dict[int, dict[str, tuple[int, ...]]] = {}
        compatible_vehicles = self._compatible_vehicles_by_package.get(package_id, ())
        for group_index in group_indices:
            events = tuple(
                sorted(groups[group_index], key=lambda item: (item[1], item[0]))
            )
            required_days = {
                solution.routes[route_index].day_index
                for route_index, _rank, _stop in events
            }
            options: list[str] = []
            routes_by_vehicle: dict[str, tuple[int, ...]] = {}
            for vehicle_id in compatible_vehicles:
                if not all(
                    (vehicle_id, day_index) in route_by_vehicle_day
                    for day_index in required_days
                ):
                    continue
                options.append(vehicle_id)
                routes_by_vehicle[vehicle_id] = tuple(
                    route_by_vehicle_day[
                        (vehicle_id, solution.routes[route_index].day_index)
                    ]
                    for route_index, _rank, _stop in events
                )
            if not options:
                return ()
            vehicle_options[group_index] = tuple(options)
            route_options[group_index] = routes_by_vehicle

        original_placement = _chain_placement(solution, package_id)
        seen_insertions: set[_InsertionSignature] = set()
        seen_solutions: set[VrpSolution] = set()
        candidates: list[VrpSolution] = []
        chain_stop_ids = {
            (route_index, id(stop))
            for events in groups.values()
            for route_index, _rank, stop in events
        }
        route_stop_counts = tuple(
            sum((route_index, id(stop)) not in chain_stop_ids for stop in route.stops)
            for route_index, route in enumerate(solution.routes)
        )
        for _attempt in range(self._candidate_limit * 16):
            assignment = _sample_uniform_carrier_sequence(
                group_indices,
                vehicle_options,
                route_options,
                route_stop_counts,
                context.random_source,
            )
            if assignment is None:
                return tuple(candidates)
            stops_by_route = self._insert_chain_stops(
                solution,
                package_id,
                groups,
                group_indices,
                assignment,
                route_by_vehicle_day,
                context,
                seen_insertions,
            )
            if stops_by_route is None:
                continue
            rebuilt = rebuild_changed_routes(
                self._instance,
                solution,
                stops_by_route,
                random_source=context.random_source,
                max_paths_per_leg=1,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=1,
                path_search_index=self._path_search_index,
            )
            for candidate in rebuilt:
                if (
                    candidate == solution
                    or _chain_placement(candidate, package_id) == original_placement
                    or candidate in seen_solutions
                ):
                    continue
                seen_solutions.add(candidate)
                candidates.append(candidate)
                if len(candidates) == self._candidate_limit:
                    return tuple(candidates)
        return tuple(candidates)

    def _insert_chain(
        self,
        solution: VrpSolution,
        package_id: str,
        groups: _PackageGroups,
        group_indices: tuple[int, ...],
        assignment: tuple[str, ...],
        route_by_vehicle_day: Mapping[tuple[str, int], int],
        context: CandidateContext,
        seen_insertions: set[_InsertionSignature],
        *,
        max_candidate_alternatives: int | None = None,
    ) -> tuple[VrpSolution, ...]:
        stops_by_route = self._insert_chain_stops(
            solution,
            package_id,
            groups,
            group_indices,
            assignment,
            route_by_vehicle_day,
            context,
            seen_insertions,
        )
        if stops_by_route is None:
            return ()
        return rebuild_changed_routes(
            self._instance,
            solution,
            stops_by_route,
            random_source=context.random_source,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_candidate_alternatives=(
                self._candidate_limit
                if max_candidate_alternatives is None
                else max_candidate_alternatives
            ),
            path_search_index=self._path_search_index,
        )

    def _insert_chain_stops(
        self,
        solution: VrpSolution,
        package_id: str,
        groups: _PackageGroups,
        group_indices: tuple[int, ...],
        assignment: tuple[str, ...],
        route_by_vehicle_day: Mapping[tuple[str, int], int],
        context: CandidateContext,
        seen_insertions: set[_InsertionSignature],
        *,
        remove_existing_stops: bool = True,
    ) -> dict[int, tuple[RouteStop, ...]] | None:
        """Place one chain into route stop sequences without rebuilding paths."""
        chain_stops = (
            {
                (route_index, id(stop))
                for events in groups.values()
                for route_index, _rank, stop in events
            }
            if remove_existing_stops
            else set()
        )
        changed_route_indices = {
            route_index
            for events in groups.values()
            for route_index, _rank, _stop in events
        }
        additions: dict[int, list[tuple[int, int, RouteStop]]] = defaultdict(list)
        for group_index, vehicle_id in zip(group_indices, assignment, strict=True):
            for source_route, rank, stop in sorted(
                groups[group_index], key=lambda item: (item[1], item[0])
            ):
                day_index = solution.routes[source_route].day_index
                destination = route_by_vehicle_day[(vehicle_id, day_index)]
                additions[destination].append((group_index, rank, stop))

        changed_route_indices.update(additions)
        stops_by_route: dict[int, tuple[RouteStop, ...]] = {}
        insertions: list[tuple[int, int, int]] = []
        for route_index in sorted(changed_route_indices):
            stops = [
                stop
                for stop in solution.routes[route_index].stops
                if (route_index, id(stop)) not in chain_stops
            ]
            selected = sorted(
                additions.get(route_index, ()), key=lambda item: (item[0], item[1])
            )
            if selected:
                insertion_positions = tuple(
                    sorted(
                        randomized_sample(
                            tuple(range(len(stops) + len(selected))),
                            len(selected),
                            context.random_source,
                        )
                    )
                )
                inserted_stops: list[RouteStop] = []
                base_stop_index = 0
                chain_stop_index = 0
                insertion_set = set(insertion_positions)
                for final_position in range(len(stops) + len(selected)):
                    if final_position in insertion_set:
                        group_index, rank, stop = selected[chain_stop_index]
                        inserted_stops.append(stop)
                        insertions.append((route_index, rank, final_position))
                        chain_stop_index += 1
                    else:
                        inserted_stops.append(stops[base_stop_index])
                        base_stop_index += 1
                stops = inserted_stops
            stops_by_route[route_index] = tuple(stops)
        insertion_signature = (
            assignment,
            tuple(insertions),
        )
        if insertion_signature in seen_insertions:
            return None
        seen_insertions.add(insertion_signature)
        return stops_by_route


def _package_events_by_package(
    solution: VrpSolution,
) -> dict[str, _PackageGroups]:
    transfer_counts: dict[str, int] = defaultdict(int)
    for step in solution.transfer_steps:
        transfer_counts[step.package_id] += 1
    groups_by_package: dict[str, defaultdict[int, list[_PackageEvent]]] = {}
    for route_index, route in enumerate(solution.routes):
        for stop in route.stops:
            if type(stop) is ActivityStop:
                package_id = stop.activity_ref.activity_id
                if stop.activity_ref.kind not in {'pickup', 'delivery'}:
                    continue
                groups = groups_by_package.setdefault(package_id, defaultdict(list))
                if stop.activity_ref.kind == 'pickup':
                    groups[0].append((route_index, 0, stop))
                else:
                    transfer_count = transfer_counts.get(package_id, 0)
                    groups[transfer_count].append(
                        (route_index, 2 * transfer_count + 1, stop)
                    )
            elif type(stop) is TransferOperationStop:
                package_id = stop.transfer_step_ref.package_id
                groups = groups_by_package.setdefault(package_id, defaultdict(list))
                step_number = stop.transfer_step_ref.step_number
                group_index = step_number - 1 if stop.role == 'out' else step_number
                rank = 2 * step_number - 1 if stop.role == 'out' else 2 * step_number
                groups[group_index].append((route_index, rank, stop))
    return {
        package_id: dict(groups) for package_id, groups in groups_by_package.items()
    }


def _package_events(solution: VrpSolution, package_id: str) -> _PackageGroups:
    return _package_events_by_package(solution).get(package_id, {})


def _chain_placement(
    solution: VrpSolution, package_id: str
) -> tuple[tuple[object, ...], ...]:
    placement: list[tuple[object, ...]] = []
    for route in solution.routes:
        for stop_index, stop in enumerate(route.stops):
            if (
                type(stop) is ActivityStop
                and stop.activity_ref.activity_id == package_id
                and stop.activity_ref.kind in {'pickup', 'delivery'}
            ):
                placement.append(
                    (
                        route.vehicle_id,
                        route.day_index,
                        stop_index,
                        stop.activity_ref.kind,
                        0,
                    )
                )
            elif (
                type(stop) is TransferOperationStop
                and stop.transfer_step_ref.package_id == package_id
            ):
                placement.append(
                    (
                        route.vehicle_id,
                        route.day_index,
                        stop_index,
                        stop.role,
                        stop.transfer_step_ref.step_number,
                    )
                )
    return tuple(placement)


def _sample_uniform_carrier_sequence(
    group_indices: tuple[int, ...],
    vehicle_options: dict[int, tuple[str, ...]],
    route_options: dict[int, dict[str, tuple[int, ...]]],
    route_stop_counts: tuple[int, ...],
    random_source: RandomSource,
) -> tuple[str, ...] | None:
    """Sample carrier assignments weighted by their legal stop placements."""
    if not group_indices:
        return None
    zero_counts = (0,) * len(route_stop_counts)

    def add_route_counts(
        counts: tuple[int, ...], route_indices: tuple[int, ...]
    ) -> tuple[int, ...]:
        updated = list(counts)
        for route_index in route_indices:
            updated[route_index] += 1
        return tuple(updated)

    @cache
    def completion_count(
        position: int,
        previous_vehicle: str | None,
        assigned_counts: tuple[int, ...],
    ) -> int:
        if position == len(group_indices):
            result = 1
            for route_index, event_count in enumerate(assigned_counts):
                result *= comb(
                    route_stop_counts[route_index] + event_count, event_count
                )
            return result
        group_index = group_indices[position]
        next_is_adjacent = (
            position + 1 < len(group_indices)
            and group_index + 1 == group_indices[position + 1]
        )
        total = 0
        for vehicle_id in vehicle_options.get(group_index, ()):
            if previous_vehicle is not None and vehicle_id == previous_vehicle:
                continue
            route_indices = route_options[group_index][vehicle_id]
            total += completion_count(
                position + 1,
                vehicle_id if next_is_adjacent else None,
                add_route_counts(assigned_counts, route_indices),
            )
        return total

    total_assignments = completion_count(0, None, zero_counts)
    if total_assignments == 0:
        return None
    assignment: list[str] = []
    assigned_counts = zero_counts
    previous_vehicle: str | None = None
    for position, group_index in enumerate(group_indices):
        next_is_adjacent = (
            position + 1 < len(group_indices)
            and group_index + 1 == group_indices[position + 1]
        )
        branches: list[tuple[str, tuple[int, ...], int]] = []
        for vehicle_id in vehicle_options.get(group_index, ()):
            if previous_vehicle is not None and vehicle_id == previous_vehicle:
                continue
            route_indices = route_options[group_index][vehicle_id]
            next_counts = add_route_counts(assigned_counts, route_indices)
            branch_count = completion_count(
                position + 1,
                vehicle_id if next_is_adjacent else None,
                next_counts,
            )
            if branch_count:
                branches.append((vehicle_id, next_counts, branch_count))
        branch_total = sum(item[2] for item in branches)
        rank = _random_below(branch_total, random_source)
        for vehicle_id, next_counts, branch_count in branches:
            if rank < branch_count:
                assignment.append(vehicle_id)
                assigned_counts = next_counts
                previous_vehicle = vehicle_id if next_is_adjacent else None
                break
            rank -= branch_count
        else:
            raise RuntimeError('weighted carrier sampling did not select a branch')
    return tuple(assignment)


def _random_below(population_size: int, random_source: RandomSource) -> int:
    """Draw an unbiased integer rank from a positive population size."""
    if population_size <= 0:
        raise ValueError('population_size must be positive')
    if population_size == 1:
        return 0
    bit_count = (population_size - 1).bit_length()
    word_bits = 53
    word_range = 1 << word_bits
    word_count = (bit_count + word_bits - 1) // word_bits
    mask = (1 << bit_count) - 1
    while True:
        candidate = 0
        for _ in range(word_count):
            draw = random_source.random()
            if type(draw) is not float:
                raise TypeError('RandomSource.random() must return a Python float')
            if not 0.0 <= draw < 1.0:
                raise ValueError('RandomSource.random() must return a float in [0, 1)')
            candidate = (candidate << word_bits) | int(draw * word_range)
        candidate &= mask
        if candidate < population_size:
            return candidate


def _sample_carrier_sequences(
    group_indices: tuple[int, ...],
    vehicle_options: dict[int, tuple[str, ...]],
    count: int,
    random_source: RandomSource,
) -> tuple[tuple[str, ...], ...]:
    @cache
    def can_complete(position: int, previous_vehicle: str | None) -> bool:
        if position == len(group_indices):
            return True
        group_index = group_indices[position]
        next_is_adjacent = (
            position + 1 < len(group_indices)
            and group_index + 1 == group_indices[position + 1]
        )
        for vehicle_id in vehicle_options[group_index]:
            if previous_vehicle is not None and vehicle_id == previous_vehicle:
                continue
            following_vehicle = vehicle_id if next_is_adjacent else None
            if can_complete(position + 1, following_vehicle):
                return True
        return False

    if not group_indices or not can_complete(0, None):
        return ()
    sequences: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for _ in range(count * 8):
        assignment: list[str] = []
        for position, group_index in enumerate(group_indices):
            previous_vehicle = None
            if position and group_indices[position - 1] + 1 == group_index:
                previous_vehicle = assignment[-1]
            next_is_adjacent = (
                position + 1 < len(group_indices)
                and group_index + 1 == group_indices[position + 1]
            )
            options = tuple(
                vehicle_id
                for vehicle_id in vehicle_options[group_index]
                if vehicle_id != previous_vehicle
                and can_complete(position + 1, vehicle_id if next_is_adjacent else None)
            )
            if not options:
                break
            assignment.append(choose_uniformly(options, random_source))
        if len(assignment) != len(group_indices):
            continue
        sequence = tuple(assignment)
        if sequence in seen:
            continue
        seen.add(sequence)
        sequences.append(sequence)
        if len(sequences) >= count:
            break
    return tuple(sequences)


def _order_candidates(
    candidates: tuple[_PackageCandidate, ...],
    objectives: ObjectiveDeclaration,
    objective_index: int | None,
    random_source: RandomSource,
) -> tuple[_PackageCandidate, ...]:
    if objective_index is None:
        return randomized_order(candidates, random_source)
    objective = objectives.objectives[objective_index]

    def compare(left: _PackageCandidate, right: _PackageCandidate) -> int:
        comparison = objective.compare(
            left[1][objective_index], right[1][objective_index]
        )
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    ranked = sorted(candidates, key=cmp_to_key(compare))
    ordered: list[_PackageCandidate] = []
    group_start = 0
    while group_start < len(ranked):
        group_end = group_start + 1
        while (
            group_end < len(ranked)
            and compare(ranked[group_start], ranked[group_end]) == 0
        ):
            group_end += 1
        ordered.extend(randomized_order(ranked[group_start:group_end], random_source))
        group_start = group_end
    return tuple(ordered)


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
