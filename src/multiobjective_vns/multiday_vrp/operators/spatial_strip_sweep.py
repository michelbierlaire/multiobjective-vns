"""Daganzo-inspired coordinate strip-sweep route rebuilding.

Created: 2026-11-12T16:00:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cmp_to_key

from multiobjective_vns.callbacks import LookupKey
from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.identity import VrpSolutionIdentity
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
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
    build_path_search_index,
    rebuild_changed_routes,
    route_node_sequence,
    stop_node_id,
)
from multiobjective_vns.multiday_vrp.operators.spatial_strip_geometry import (
    SpatialDeliveryPoint,
    sample_sweep_partitions,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
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

__all__ = ['DaganzoStripSweepRouteRebuildOperator']

type _ScoredCandidate = tuple[VrpSolution, tuple[object, ...], int]
type _GroupKey = tuple[int, str]


@dataclass(frozen=True, slots=True)
class _EligibleRoute:
    route_index: int
    vehicle_order: int
    start_node_id: str
    package_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PackageOccurrence:
    package_id: str
    source_route_index: int
    source_vehicle_order: int
    pickup_position: int
    pickup_stop: ActivityStop
    delivery_stop: ActivityStop
    delivery_node_id: str
    x: float
    y: float


class DaganzoStripSweepRouteRebuildOperator:
    """Rebuild same-day package routes from sampled planar strip sweeps.

    The Operator partitions a geographic delivery order across up to ``k``
    eligible vehicle routes, reconstructs each selected route's directed legs,
    and assesses complete plans with the application's assessor.

    :param instance: Validated VRP instance with complete spatial coverage.
    :param objective_declaration: Ordered objectives for assessment and ranking.
    :param assess_plan: Shared complete-plan assessment callback.
    :param max_neighborhood_size: Maximum number of jointly rebuilt routes.
    :param objective_id: Optional single-objective ranking target.
    :param max_candidate_alternatives: Maximum complete alternatives assessed.
    :param max_paths_per_leg: Maximum sampled paths for a changed route leg.
    :param max_arc_expansion_steps: Bound for each route path search.
    :raises TypeError: If a dependency or setting has an invalid type.
    :raises ValueError: If spatial data, objective target, or limits are invalid.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_target_position',
        '_maximum_size',
        '_alternative_limit',
        '_path_limit',
        '_expansion_limit',
        '_coordinates',
        '_packages',
        '_identity',
        '_path_search_index',
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
        if instance.spatial_data.coordinate_system_id is None:
            raise ValueError('spatial coordinates are required by this Operator')
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._target_position = (
            None
            if objective_id is None
            else objective_position(objective_declaration, objective_id)
        )
        self._maximum_size = require_integer(
            max_neighborhood_size,
            name='max_neighborhood_size',
            minimum=1,
        )
        self._alternative_limit = _positive_bounded_integer(
            max_candidate_alternatives,
            name='max_candidate_alternatives',
            maximum=32,
        )
        self._path_limit = _positive_bounded_integer(
            max_paths_per_leg,
            name='max_paths_per_leg',
            maximum=32,
        )
        self._expansion_limit = _positive_bounded_integer(
            max_arc_expansion_steps,
            name='max_arc_expansion_steps',
            maximum=10000,
        )
        self._coordinates = {
            item.node_id: (item.x, item.y)
            for item in instance.spatial_data.node_coordinates
        }
        if not self._coordinates:
            raise ValueError('spatial coordinates are required by this Operator')
        self._packages = {
            item.package_id: item for item in instance.package_data.packages
        }
        self._identity = VrpSolutionIdentity(instance)
        self._path_search_index = build_path_search_index(instance)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return distinct complete route-rebuild candidates in defined order.

        :param context: Base, neighborhood size, shared random source, and limit.
        :returns: Assessed distinct candidates with route-count modification m.
        :raises TypeError: If the base is not a VRP solution.
        :raises ValueError: If ``k`` exceeds this Operator's maximum.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        groups = self._eligible_groups(base)
        if not groups:
            return OperatorResult(candidates=())

        maximum_group_size = max(min(size, len(routes)) for routes in groups.values())
        eligible_keys = tuple(
            key
            for key, routes in sorted(groups.items())
            if min(size, len(routes)) == maximum_group_size
        )
        selected_key = choose_uniformly(eligible_keys, context.random_source)
        group_routes = groups[selected_key]
        selected_count = min(size, len(group_routes))
        selected_routes = tuple(
            sorted(
                randomized_sample(group_routes, selected_count, context.random_source),
                key=lambda item: (item.vehicle_order, item.route_index),
            )
        )
        occurrences = self._package_occurrences(base, selected_routes)
        if occurrences is None:
            return OperatorResult(candidates=())

        points = tuple(
            SpatialDeliveryPoint(item.package_id, item.delivery_node_id, item.x, item.y)
            for item in occurrences
        )
        route_count = len(selected_routes)
        partitions = sample_sweep_partitions(
            points,
            route_count=route_count,
            max_alternatives=self._alternative_limit,
            random_source=context.random_source,
        )

        scored: list[_ScoredCandidate] = []
        equivalent_candidates: dict[LookupKey, list[VrpSolution]] = {
            self._identity.lookup_key(base): [base]
        }
        path_options_cache: dict[tuple[str, str], tuple[tuple[str, ...], ...]] = {}
        examined = 0
        for partition in partitions:
            if examined >= self._alternative_limit:
                break
            blocks = _split_blocks(
                partition.package_order,
                partition.cut_positions,
            )
            replacement_stops = self._replacement_stops(
                occurrences,
                selected_routes,
                blocks,
                partition.route_assignment,
            )
            rebuilt = rebuild_changed_routes(
                self._instance,
                base,
                replacement_stops,
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=1,
                path_search_index=self._path_search_index,
                path_options_cache=path_options_cache,
            )
            if not rebuilt:
                examined += 1
                continue

            candidate = rebuilt[0]
            examined += 1
            candidate_key = self._identity.lookup_key(candidate)
            same_key_candidates = equivalent_candidates.setdefault(
                candidate_key,
                [],
            )
            if any(
                self._identity.equivalent(candidate, prior)
                for prior in same_key_candidates
            ):
                continue
            same_key_candidates.append(candidate)
            evaluation = assess_complete_candidate(
                self._instance,
                self._objectives,
                candidate,
                self._assess_plan,
                ensure_feasible=context.ensure_feasible,
            )
            if evaluation is None or evaluation.objective_values is None:
                continue
            modification_count = sum(
                _route_move_changed(
                    base.routes[item.route_index],
                    candidate.routes[item.route_index],
                )
                for item in selected_routes
            )
            if not 1 <= modification_count <= size:
                continue
            scored.append((candidate, evaluation.objective_values, modification_count))

        ordered = self._rank(scored, context)
        proposals = tuple(
            CandidateProposal(Candidate(solution=solution), modification_count)
            for solution, _values, modification_count in ordered[
                : context.max_candidates
            ]
        )
        return OperatorResult(candidates=proposals)

    def _eligible_groups(
        self, solution: VrpSolution
    ) -> dict[_GroupKey, tuple[_EligibleRoute, ...]]:
        vehicle_order = {
            vehicle.vehicle_id: index
            for index, vehicle in enumerate(self._instance.fleet_data.vehicles)
        }
        per_vehicle_day: dict[tuple[str, int], int] = defaultdict(int)
        for route in solution.routes:
            per_vehicle_day[(route.vehicle_id, route.day_index)] += 1
        groups: dict[_GroupKey, list[_EligibleRoute]] = defaultdict(list)
        for route_index, route in enumerate(solution.routes):
            if per_vehicle_day[(route.vehicle_id, route.day_index)] != 1:
                continue
            if route.vehicle_id not in vehicle_order or not route.stops:
                continue
            start_node = route_node_sequence(self._instance, solution, route)[0]
            package_ids = self._eligible_route_packages(
                solution,
                route,
                start_node,
            )
            if not package_ids:
                continue
            groups[(route.day_index, start_node)].append(
                _EligibleRoute(
                    route_index,
                    vehicle_order[route.vehicle_id],
                    start_node,
                    package_ids,
                )
            )

        valid_groups: dict[_GroupKey, tuple[_EligibleRoute, ...]] = {}
        transfer_packages = {step.package_id for step in solution.transfer_steps}
        for key, routes in groups.items():
            group_package_ids = [
                package_id for route in routes for package_id in route.package_ids
            ]
            if len(group_package_ids) != len(set(group_package_ids)):
                continue
            if transfer_packages.intersection(group_package_ids):
                continue
            valid_groups[key] = tuple(
                sorted(routes, key=lambda item: (item.vehicle_order, item.route_index))
            )
        return valid_groups

    def _eligible_route_packages(
        self,
        solution: VrpSolution,
        route: VehicleDayRoute,
        start_node_id: str,
    ) -> tuple[str, ...]:
        pickups: dict[str, int] = {}
        deliveries: dict[str, int] = {}
        for stop_index, stop in enumerate(route.stops):
            if type(stop) is not ActivityStop:
                return ()
            if stop.team_member_ids or stop.break_duration != 0.0 or stop.tour_end:
                return ()
            kind = stop.activity_ref.kind
            package_id = stop.activity_ref.activity_id
            if kind not in {'pickup', 'delivery'} or package_id not in self._packages:
                return ()
            target = pickups if kind == 'pickup' else deliveries
            if package_id in target:
                return ()
            target[package_id] = stop_index
            if (
                kind == 'pickup'
                and stop_node_id(self._instance, solution, stop) != start_node_id
            ):
                return ()
        if not pickups or pickups.keys() != deliveries.keys():
            return ()
        return tuple(sorted(pickups))

    def _package_occurrences(
        self,
        solution: VrpSolution,
        selected_routes: tuple[_EligibleRoute, ...],
    ) -> tuple[_PackageOccurrence, ...] | None:
        occurrences: list[_PackageOccurrence] = []
        for selected in selected_routes:
            route = solution.routes[selected.route_index]
            pickup_stops: dict[str, tuple[int, ActivityStop]] = {}
            delivery_stops: dict[str, ActivityStop] = {}
            for index, stop in enumerate(route.stops):
                if type(stop) is not ActivityStop:
                    return None
                if stop.activity_ref.kind == 'pickup':
                    pickup_stops[stop.activity_ref.activity_id] = (index, stop)
                else:
                    delivery_stops[stop.activity_ref.activity_id] = stop
            for package_id in selected.package_ids:
                pickup_position, pickup_stop = pickup_stops[package_id]
                delivery_stop = delivery_stops[package_id]
                delivery_node_id = self._packages[package_id].delivery_node_id
                x, y = self._coordinates[delivery_node_id]
                occurrences.append(
                    _PackageOccurrence(
                        package_id,
                        selected.route_index,
                        selected.vehicle_order,
                        pickup_position,
                        pickup_stop,
                        delivery_stop,
                        delivery_node_id,
                        x,
                        y,
                    )
                )
        package_ids = [item.package_id for item in occurrences]
        if len(package_ids) != len(set(package_ids)):
            return None
        return tuple(sorted(occurrences, key=lambda item: item.package_id))

    def _replacement_stops(
        self,
        occurrences: tuple[_PackageOccurrence, ...],
        selected_routes: tuple[_EligibleRoute, ...],
        blocks: tuple[tuple[str, ...], ...],
        assignment: tuple[int, ...],
    ) -> Mapping[int, tuple[ActivityStop, ...]]:
        occurrence_by_id = {item.package_id: item for item in occurrences}
        pickup_order = tuple(
            sorted(
                occurrences,
                key=lambda item: (
                    item.source_vehicle_order,
                    item.pickup_position,
                    item.package_id,
                ),
            )
        )
        stops_by_route: dict[int, tuple[ActivityStop, ...]] = {}
        for block_index, package_block in enumerate(blocks):
            selected_index = assignment[block_index]
            selected_route = selected_routes[selected_index]
            assigned_ids = set(package_block)
            pickups = tuple(
                item.pickup_stop
                for item in pickup_order
                if item.package_id in assigned_ids
            )
            deliveries = tuple(
                occurrence_by_id[package_id].delivery_stop
                for package_id in package_block
            )
            stops_by_route[selected_route.route_index] = (*pickups, *deliveries)
        return stops_by_route

    def _rank(
        self,
        candidates: Sequence[_ScoredCandidate],
        context: CandidateContext,
    ) -> tuple[_ScoredCandidate, ...]:
        if self._target_position is None or len(candidates) < 2:
            return tuple(candidates)
        position = self._target_position
        objective = self._objectives.objectives[position]

        def compare(left: _ScoredCandidate, right: _ScoredCandidate) -> int:
            result = objective.compare(left[1][position], right[1][position])
            if result is ObjectiveComparison.LESS:
                return -1
            if result is ObjectiveComparison.GREATER:
                return 1
            return 0

        ranked = sorted(candidates, key=cmp_to_key(compare))
        groups: list[list[_ScoredCandidate]] = []
        for item in ranked:
            if not groups or compare(groups[-1][0], item) != 0:
                groups.append([item])
            else:
                groups[-1].append(item)
        return tuple(
            item
            for group in groups
            for item in randomized_order(group, context.random_source)
        )


def _positive_bounded_integer(value: int, *, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result


def _route_move_changed(
    base: VehicleDayRoute,
    candidate: VehicleDayRoute,
) -> bool:
    """Compare the route units named by the Operator's modification scale."""
    base_delivery_order = tuple(
        stop.activity_ref.activity_id
        for stop in base.stops
        if type(stop) is ActivityStop and stop.activity_ref.kind == 'delivery'
    )
    candidate_delivery_order = tuple(
        stop.activity_ref.activity_id
        for stop in candidate.stops
        if type(stop) is ActivityStop and stop.activity_ref.kind == 'delivery'
    )
    return (
        set(base_delivery_order) != set(candidate_delivery_order)
        or base_delivery_order != candidate_delivery_order
        or tuple(leg.arc_ids for leg in base.legs)
        != tuple(leg.arc_ids for leg in candidate.legs)
    )


def _split_blocks(
    package_order: tuple[str, ...], cuts: tuple[int, ...]
) -> tuple[tuple[str, ...], ...]:
    boundaries = (0, *cuts, len(package_order))
    return tuple(
        package_order[start:end]
        for start, end in zip(boundaries[:-1], boundaries[1:], strict=True)
    )
