"""Window-guided Or-opt and SWAP* route-order Operators.

Created: 2026-11-06T13:24:40+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    operator_result,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    _sample_integer_indices,
    route_node_sequence,
    stop_node_id,
)
from multiobjective_vns.multiday_vrp.operators.service_window_common import (
    _append_scored,
    _Bundle,
    _bundle_windowed,
    _first_key_position,
    _is_windowed_activity,
    _select_critical_pair,
    _select_critical_target,
    _WindowStructuralOperator,
    _without_keys,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_elimination import (
    _relationship_groups,
    _service_units,
    _stop_unit_key,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_assignment_helpers import (
    _ScoredPlan,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteStop,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult

__all__ = [
    'SlackGuidedOrOptRelocationOperator',
    'WindowFeasibleSwapStarOperator',
]


class SlackGuidedOrOptRelocationOperator(_WindowStructuralOperator):
    """Relocate a windowed stop block and favor the least-penalized plans.

    :param window_objective_id: The instance's dedicated window-penalty objective.
    :param objective_id: Optional objective used after window-first filtering.
    :param max_neighborhood_size: Maximum stop-block size ``k``.
    :param max_candidate_alternatives: Maximum insertion proposals sampled.
    """

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        window_objective_id: ObjectiveId,
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        super().__init__(
            instance,
            objective_declaration,
            assess_plan,
            window_objective_id=window_objective_id,
            objective_id=objective_id,
            max_neighborhood_size=max_neighborhood_size,
            max_candidate_alternatives=max_candidate_alternatives,
            max_paths_per_leg=max_paths_per_leg,
            max_arc_expansion_steps=max_arc_expansion_steps,
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Move one block of exactly ``k`` stops containing a windowed activity."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        target_list: list[tuple[int, int]] = []
        for route_index, route in enumerate(base.routes):
            windowed = tuple(
                _is_windowed_activity(self._instance, route.day_index, stop)
                for stop in route.stops
            )
            if len(route.stops) < size:
                continue
            windowed_count = sum(windowed[:size])
            for start in range(len(route.stops) - size + 1):
                if windowed_count:
                    target_list.append((route_index, start))
                if start + size < len(route.stops):
                    windowed_count += int(windowed[start + size]) - int(windowed[start])
        targets = tuple(target_list)
        if not targets:
            return operator_result(None, modifications=0, requested_size=size)
        target = _select_critical_target(
            self._instance,
            base,
            targets,
            context,
        )
        route_index, start = target
        route = base.routes[route_index]
        block = route.stops[start : start + size]
        reduced = (*route.stops[:start], *route.stops[start + size :])
        placements: list[tuple[int, int]] = []
        seen_insertions: set[tuple[int, tuple[object, ...]]] = set()
        for destination_index, destination in enumerate(base.routes):
            if destination.day_index != route.day_index:
                continue
            target_stops = (
                reduced if destination_index == route_index else destination.stops
            )
            for insertion in range(len(target_stops) + 1):
                inserted = (
                    *target_stops[:insertion],
                    *block,
                    *target_stops[insertion:],
                )
                if destination_index == route_index:
                    if inserted == route.stops:
                        continue
                insertion_key = (destination_index, tuple(inserted))
                if insertion_key in seen_insertions:
                    continue
                seen_insertions.add(insertion_key)
                placements.append((destination_index, insertion))
        scored: list[_ScoredPlan] = []
        for destination_index, insertion in randomized_sample(
            placements, self._candidate_limit, context.random_source
        ):
            destination = base.routes[destination_index]
            target_stops = (
                reduced if destination_index == route_index else destination.stops
            )
            inserted = (
                *target_stops[:insertion],
                *block,
                *target_stops[insertion:],
            )
            routes = list(base.routes)
            routes[route_index] = replace(route, stops=tuple(reduced))
            routes[destination_index] = replace(destination, stops=tuple(inserted))
            proposal = replace(base, routes=tuple(routes))
            changed_indices = (route_index, destination_index)
            for candidate in self._rebuild(base, proposal, changed_indices, context):
                values = self._assess(
                    candidate, ensure_feasible=context.ensure_feasible
                )
                if values is not None and candidate != base:
                    _append_scored(scored, candidate, values)
        if not scored:
            return operator_result(None, modifications=0, requested_size=size)
        selected, _values = self._choose(scored, context)
        return operator_result(selected, modifications=size, requested_size=size)


class WindowFeasibleSwapStarOperator(_WindowStructuralOperator):
    """Exchange complete service repair units across same-day routes.

    At least one exchanged unit has a time-windowed activity. Independent
    insertion positions allow the two units to occupy useful positions in the
    receiving routes.
    """

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        window_objective_id: ObjectiveId,
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        super().__init__(
            instance,
            objective_declaration,
            assess_plan,
            window_objective_id=window_objective_id,
            objective_id=objective_id,
            max_neighborhood_size=max_neighborhood_size,
            max_candidate_alternatives=max_candidate_alternatives,
            max_paths_per_leg=max_paths_per_leg,
            max_arc_expansion_steps=max_arc_expansion_steps,
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` distinct repair-unit pair exchanges."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        working = require_vrp_base(context)
        used_pairs: set[frozenset[frozenset[tuple[str, ...]]]] = set()
        modifications = 0
        for _ in range(size):
            step_context = (
                context
                if modifications == 0
                else replace(context, base=working, base_evaluation=None)
            )
            result = self._exchange_candidates(working, step_context, used_pairs)
            if result is None:
                break
            pair_identity, candidates = result
            working, _values = self._choose(candidates, step_context)
            used_pairs.add(pair_identity)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _exchange_candidates(
        self,
        base: VrpSolution,
        context: CandidateContext,
        used_pairs: set[frozenset[frozenset[tuple[str, ...]]]],
    ) -> tuple[frozenset[frozenset[tuple[str, ...]]], tuple[_ScoredPlan, ...]] | None:
        bundles_by_route = _contained_exchange_bundles_by_route(self._instance, base)
        pairs: list[tuple[int, _Bundle, int, _Bundle]] = []
        for left_index, left_route in enumerate(base.routes):
            for right_index in range(left_index + 1, len(base.routes)):
                right_route = base.routes[right_index]
                if (
                    left_route.day_index != right_route.day_index
                    or left_route.vehicle_id == right_route.vehicle_id
                ):
                    continue
                for left_bundle in bundles_by_route[left_index]:
                    for right_bundle in bundles_by_route[right_index]:
                        if _bundle_windowed(
                            self._instance, left_route.day_index, left_bundle
                        ) or _bundle_windowed(
                            self._instance, right_route.day_index, right_bundle
                        ):
                            pair = (left_index, left_bundle, right_index, right_bundle)
                            if _pair_identity(pair) not in used_pairs:
                                pairs.append(pair)
        if not pairs:
            return None
        selected_pair = _select_critical_pair(self._instance, base, pairs, context)
        left_index, left_bundle, right_index, right_bundle = selected_pair
        left_route = base.routes[left_index]
        right_route = base.routes[right_index]
        left_remaining = _without_keys(left_route.stops, left_bundle.keys)
        right_remaining = _without_keys(right_route.stops, right_bundle.keys)
        left_depot_pickups, left_remaining = _split_start_depot_pickups(
            self._instance, base, left_route, left_remaining
        )
        right_depot_pickups, right_remaining = _split_start_depot_pickups(
            self._instance, base, right_route, right_remaining
        )
        incoming_left_pickups, incoming_left_stops = _split_start_depot_pickups(
            self._instance, base, left_route, right_bundle.stops
        )
        incoming_right_pickups, incoming_right_stops = _split_start_depot_pickups(
            self._instance, base, right_route, left_bundle.stops
        )
        # A multi-stop repair unit uses its first route occurrence as its
        # free-insertion anchor; the unit's remaining stops stay ordered.
        left_anchor = _first_key_position(left_route.stops, left_bundle.keys)
        right_anchor = _first_key_position(right_route.stops, right_bundle.keys)
        right_width = len(right_remaining) + 1
        placement_count = (len(left_remaining) + 1) * right_width
        placement_indices = _sample_integer_indices(
            placement_count,
            min(self._candidate_limit, placement_count),
            context.random_source,
        )
        scored: list[_ScoredPlan] = []
        seen_candidates: set[VrpSolution] = set()
        for rank in placement_indices:
            left_position, right_position = divmod(rank, right_width)
            if left_position == right_anchor or right_position == left_anchor:
                continue
            new_left = (
                *left_depot_pickups,
                *incoming_left_pickups,
                *left_remaining[:left_position],
                *incoming_left_stops,
                *left_remaining[left_position:],
            )
            new_right = (
                *right_depot_pickups,
                *incoming_right_pickups,
                *right_remaining[:right_position],
                *incoming_right_stops,
                *right_remaining[right_position:],
            )
            if new_left == left_route.stops and new_right == right_route.stops:
                continue
            routes = list(base.routes)
            routes[left_index] = replace(left_route, stops=tuple(new_left))
            routes[right_index] = replace(right_route, stops=tuple(new_right))
            proposal = replace(base, routes=tuple(routes))
            rebuilt = self._rebuild(
                base,
                proposal,
                (left_index, right_index),
                context,
                max_candidate_alternatives=self._candidate_limit - len(seen_candidates),
            )
            for candidate in rebuilt:
                if candidate in seen_candidates:
                    continue
                seen_candidates.add(candidate)
                values = self._assess(
                    candidate, ensure_feasible=context.ensure_feasible
                )
                if values is not None:
                    _append_scored(scored, candidate, values)
                if len(seen_candidates) >= self._candidate_limit:
                    break
            if len(seen_candidates) >= self._candidate_limit:
                break
        if not scored:
            return None
        selected_pair_identity = _pair_identity(selected_pair)
        return selected_pair_identity, tuple(scored)


def _contained_exchange_bundles_by_route(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
) -> tuple[tuple[_Bundle, ...], ...]:
    """Return route-contained repair units, preserving markers and other units."""
    bundles: list[list[_Bundle]] = [[] for _route in solution.routes]
    for group in _relationship_groups(instance, _service_units(solution)):
        occurrences = tuple(
            occurrence for unit in group for occurrence in unit.occurrences
        )
        route_indices = {index for index, _position, _stop in occurrences}
        if len(route_indices) != 1:
            continue
        route_index = next(iter(route_indices))
        route = solution.routes[route_index]
        keys = frozenset(unit.key for unit in group)
        stops = tuple(stop for stop in route.stops if _stop_unit_key(stop) in keys)
        if stops:
            bundles[route_index].append(_Bundle(keys, stops))
    return tuple(tuple(route_bundles) for route_bundles in bundles)


def _split_start_depot_pickups(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route: VehicleDayRoute,
    stops: tuple[RouteStop, ...],
) -> tuple[tuple[RouteStop, ...], tuple[RouteStop, ...]]:
    """Separate pickups at a route's starting depot for its initial stop block."""
    start_node_id = route_node_sequence(instance, solution, route)[0]
    initial_pickups: list[RouteStop] = []
    remaining: list[RouteStop] = []
    for stop in stops:
        if (
            isinstance(stop, ActivityStop)
            and stop.activity_ref.kind == 'pickup'
            and stop_node_id(instance, solution, stop) == start_node_id
        ):
            initial_pickups.append(stop)
        else:
            remaining.append(stop)
    return tuple(initial_pickups), tuple(remaining)


def _pair_identity(
    pair: tuple[int, _Bundle, int, _Bundle],
) -> frozenset[frozenset[tuple[str, ...]]]:
    """Return a route-independent key for one exchanged repair-unit pair."""
    return frozenset((pair[1].keys, pair[3].keys))
