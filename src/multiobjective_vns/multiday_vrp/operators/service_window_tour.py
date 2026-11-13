"""Objective-guided construction and improvement of window-feasible tours.

Created: 2026-11-12T18:24:52+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cmp_to_key
from heapq import heappop, heappush
from math import inf, isfinite
from typing import cast

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.helpers import (
    _lower_violation,
    _upper_violation,
)
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
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
)
from multiobjective_vns.multiday_vrp.operators.vehicle_insertion_search import (
    target_first_nondominated_scored_plans,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = [
    'ObjectiveGuidedBacktrackingTourConstructionOperator',
    'ObjectiveGuidedFeasibleDeliveryEdgeExchangeOperator',
    'ObjectiveGuidedTourRegretInsertionOperator',
    'TimeWindowFeasibleTourInsertionOperator',
]

type _Window = tuple[float, float]
type _ScoredTour = tuple[VrpSolution, tuple[object, ...], int]


@dataclass(frozen=True, slots=True)
class _TourRoute:
    route_index: int
    route: VehicleDayRoute
    start_node_id: str
    end_node_id: str
    pickups: tuple[ActivityStop, ...]
    deliveries: tuple[ActivityStop, ...]


@dataclass(frozen=True, slots=True)
class _ScheduleProfile:
    stops: tuple[ActivityStop, ...]
    earliest_departures: tuple[float, ...]
    latest_service_starts: tuple[float, ...]


class _ObjectiveGuidedTourSearch:
    """Shared eligibility, schedule, assessment, and ranking for tour moves."""

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_position',
        '_maximum_size',
        '_candidate_limit',
        '_path_limit',
        '_expansion_limit',
        '_packages',
        '_windows',
        '_shortest_times',
        '_path_search_index',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        objective_id: ObjectiveId,
        max_neighborhood_size: int,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        """Initialize objective-guided tour search.

        :param instance: Validated VRP data used to build route schedules.
        :param objective_declaration: Ordered objective comparison functions.
        :param assess_plan: Shared complete-plan assessor.
        :param objective_id: Selected non-window objective to improve.
        :param max_neighborhood_size: Maximum number of delivery positions moved.
        :param max_candidate_alternatives: Bound on assessed tour alternatives.
        :param max_paths_per_leg: Maximum rebuilt paths considered per route leg.
        :param max_arc_expansion_steps: Maximum network expansions per path search.
        :raises TypeError: If the instance, declaration, or assessor is invalid.
        :raises ValueError: If limits are invalid or the objective is the window
            penalty objective.
        """
        if not isinstance(instance, LoadedVrpInstance):
            raise TypeError('instance must be a LoadedVrpInstance')
        if not isinstance(objective_declaration, ObjectiveDeclaration):
            raise TypeError('objective_declaration must be an ObjectiveDeclaration')
        if not callable(assess_plan):
            raise TypeError('assess_plan must be callable')
        if objective_id == instance.time_window_violation_objective_id:
            raise ValueError(
                'tour search requires an objective other than the dedicated '
                'time-window-violation objective'
            )
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._objective_position = objective_position(
            objective_declaration, objective_id
        )
        self._maximum_size = require_integer(
            max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )
        self._candidate_limit = _bounded_limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _bounded_limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _bounded_limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )
        self._packages = {
            item.package_id: item for item in instance.package_data.packages
        }
        window_records: dict[tuple[str, str], list[_Window]] = defaultdict(list)
        for item in instance.package_data.package_time_windows:
            window_records[(item.package_id, item.activity)].append(
                (item.start_time, item.end_time)
            )
        self._windows = {
            key: tuple(sorted(values)) for key, values in window_records.items()
        }
        self._shortest_times = _shortest_travel_times(instance)
        self._path_search_index = build_path_search_index(instance)

    def _eligible_routes(self, solution: VrpSolution) -> tuple[_TourRoute, ...]:
        if solution.transfer_steps:
            return ()
        eligible: list[_TourRoute] = []
        for route_index, route in enumerate(solution.routes):
            if len(route.stops) < 4:
                continue
            if any(
                type(stop) is not ActivityStop
                or stop.activity_ref.kind not in {'pickup', 'delivery'}
                or stop.team_member_ids
                or stop.wait != 0.0
                or stop.break_duration != 0.0
                or stop.tour_end
                for stop in route.stops
            ):
                continue
            stops = tuple(
                stop for stop in route.stops if isinstance(stop, ActivityStop)
            )
            pickups = tuple(
                stop for stop in stops if stop.activity_ref.kind == 'pickup'
            )
            deliveries = tuple(
                stop for stop in stops if stop.activity_ref.kind == 'delivery'
            )
            pickup_ids = tuple(stop.activity_ref.activity_id for stop in pickups)
            delivery_ids = tuple(stop.activity_ref.activity_id for stop in deliveries)
            if (
                len(pickups) < 2
                or len(deliveries) < 2
                or set(pickup_ids) != set(delivery_ids)
                or len(set(pickup_ids)) != len(pickup_ids)
                or len(set(delivery_ids)) != len(delivery_ids)
                or stops != (*pickups, *deliveries)
            ):
                continue
            try:
                nodes = route_node_sequence(self._instance, solution, route)
            except (KeyError, ValueError):
                continue
            if not nodes or any(
                package_id not in self._packages for package_id in pickup_ids
            ):
                continue
            if any(
                not self._windows.get(
                    (stop.activity_ref.activity_id, stop.activity_ref.kind)
                )
                for stop in stops
            ):
                continue
            if any(
                self._packages[package_id].pickup_node_id != nodes[0]
                for package_id in pickup_ids
            ):
                continue
            if any(
                (
                    nodes[0],
                    self._packages[stop.activity_ref.activity_id].delivery_node_id,
                )
                not in self._shortest_times
                for stop in deliveries
            ):
                continue
            eligible.append(
                _TourRoute(route_index, route, nodes[0], nodes[-1], pickups, deliveries)
            )
        return tuple(eligible)

    def _route_at(self, solution: VrpSolution, route_index: int) -> _TourRoute | None:
        return next(
            (
                route
                for route in self._eligible_routes(solution)
                if route.route_index == route_index
            ),
            None,
        )

    def _schedule_order(
        self, route: _TourRoute, delivery_order: Sequence[ActivityStop]
    ) -> tuple[ActivityStop, ...] | None:
        current_time = route.route.start_time
        current_node = route.start_node_id
        scheduled: list[ActivityStop] = []
        for stop in (*route.pickups, *delivery_order):
            package_id = stop.activity_ref.activity_id
            package = self._packages[package_id]
            destination = (
                package.pickup_node_id
                if stop.activity_ref.kind == 'pickup'
                else package.delivery_node_id
            )
            travel = self._shortest_times.get((current_node, destination), inf)
            if not isfinite(travel):
                return None
            arrival = current_time + travel
            duration = (
                package.pickup_service_duration
                if stop.activity_ref.kind == 'pickup'
                else package.delivery_service_duration
            )
            service_start = _earliest_service_start(
                arrival,
                duration,
                self._windows.get((package_id, stop.activity_ref.kind), ()),
                stop.pre_service_wait,
            )
            if service_start is None:
                return None
            wait = service_start - arrival
            scheduled.append(replace(stop, pre_service_wait=float(wait)))
            current_time = service_start + duration
            current_node = destination
        return_travel = self._shortest_times.get((current_node, route.end_node_id), inf)
        if not isfinite(return_travel):
            return None
        return tuple(scheduled)

    def _schedule_profile(
        self, route: _TourRoute, delivery_order: Sequence[ActivityStop]
    ) -> _ScheduleProfile | None:
        stops = (*route.pickups, *delivery_order)
        scheduled = self._schedule_order(route, delivery_order)
        if scheduled is None:
            return None
        departures: list[float] = []
        current_time = route.route.start_time
        current_node = route.start_node_id
        for stop in scheduled:
            destination = self._destination(stop)
            travel = self._shortest_times.get((current_node, destination), inf)
            if not isfinite(travel):
                return None
            duration = self._duration(stop)
            arrival = current_time + travel
            service_start = arrival + stop.pre_service_wait
            current_time = service_start + duration
            current_node = destination
            departures.append(current_time)

        latest_starts = [inf] * len(stops)
        latest_next = inf
        for index in range(len(stops) - 1, -1, -1):
            stop = stops[index]
            duration = self._duration(stop)
            if index + 1 < len(stops):
                next_stop = stops[index + 1]
                travel = self._shortest_times.get(
                    (self._destination(stop), self._destination(next_stop)), inf
                )
                if not isfinite(travel):
                    return None
                latest_bound = latest_next - duration - travel
            else:
                latest_bound = inf
            latest_start = _latest_service_start(
                latest_bound,
                duration,
                self._windows.get(
                    (stop.activity_ref.activity_id, stop.activity_ref.kind), ()
                ),
            )
            if latest_start is None:
                return None
            latest_starts[index] = latest_start
            latest_next = latest_start
        return _ScheduleProfile(
            tuple(scheduled), tuple(departures), tuple(latest_starts)
        )

    def _slack_screen(
        self,
        route: _TourRoute,
        current_order: Sequence[ActivityStop],
        candidate_order: Sequence[ActivityStop],
    ) -> bool:
        """Screen a changed segment against its forward schedule and suffix slack."""
        profile = self._schedule_profile(route, current_order)
        if profile is None:
            return False
        prefix = 0
        while (
            prefix < min(len(current_order), len(candidate_order))
            and current_order[prefix] == candidate_order[prefix]
        ):
            prefix += 1
        suffix = 0
        while (
            suffix < min(len(current_order) - prefix, len(candidate_order) - prefix)
            and current_order[len(current_order) - suffix - 1]
            == candidate_order[len(candidate_order) - suffix - 1]
        ):
            suffix += 1

        prefix_count = len(route.pickups) + prefix
        if prefix_count:
            prefix_stop = profile.stops[prefix_count - 1]
            current_node = self._destination(prefix_stop)
            current_time = profile.earliest_departures[prefix_count - 1]
        else:
            current_node = route.start_node_id
            current_time = route.route.start_time

        changed_end = len(candidate_order) - suffix
        for stop in candidate_order[prefix:changed_end]:
            next_time = self._earliest_departure(stop, current_node, current_time)
            if next_time is None:
                return False
            current_time, current_node = next_time

        if suffix == 0:
            return True
        suffix_delivery_index = len(candidate_order) - suffix
        suffix_profile_index = len(route.pickups) + suffix_delivery_index
        next_stop = candidate_order[suffix_delivery_index]
        arrival = current_time + self._travel_time(
            current_node, self._destination(next_stop)
        )
        if not isfinite(arrival):
            return False
        service_start = _earliest_service_start(
            arrival,
            self._duration(next_stop),
            self._windows.get(
                (next_stop.activity_ref.activity_id, next_stop.activity_ref.kind), ()
            ),
            next_stop.pre_service_wait,
        )
        return (
            service_start is not None
            and service_start
            <= profile.latest_service_starts[suffix_profile_index] + 1e-9
        )

    def _earliest_departure(
        self, stop: ActivityStop, current_node: str, current_time: float
    ) -> tuple[float, str] | None:
        destination = self._destination(stop)
        travel = self._travel_time(current_node, destination)
        if not isfinite(travel):
            return None
        arrival = current_time + travel
        service_start = _earliest_service_start(
            arrival,
            self._duration(stop),
            self._windows.get(
                (stop.activity_ref.activity_id, stop.activity_ref.kind), ()
            ),
            stop.pre_service_wait,
        )
        if service_start is None:
            return None
        return service_start + self._duration(stop), destination

    def _destination(self, stop: ActivityStop) -> str:
        package = self._packages[stop.activity_ref.activity_id]
        return (
            package.pickup_node_id
            if stop.activity_ref.kind == 'pickup'
            else package.delivery_node_id
        )

    def _duration(self, stop: ActivityStop) -> float:
        package = self._packages[stop.activity_ref.activity_id]
        return (
            package.pickup_service_duration
            if stop.activity_ref.kind == 'pickup'
            else package.delivery_service_duration
        )

    def _travel_time(self, start_node: str, end_node: str) -> float:
        return self._shortest_times.get((start_node, end_node), inf)

    def _assess_order(
        self,
        base: VrpSolution,
        route: _TourRoute,
        delivery_order: Sequence[ActivityStop],
        context: CandidateContext,
    ) -> tuple[_ScoredTour, ...]:
        current_order = route.deliveries
        if tuple(delivery_order) == current_order:
            return ()
        scheduled = self._schedule_order(route, delivery_order)
        if scheduled is None:
            return ()
        scored: list[_ScoredTour] = []
        seen: set[VrpSolution] = set()
        for candidate in rebuild_changed_routes(
            self._instance,
            base,
            {route.route_index: tuple(scheduled)},
            random_source=context.random_source,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_candidate_alternatives=self._path_limit,
            path_search_index=self._path_search_index,
        ):
            if candidate == base or candidate in seen:
                continue
            evaluation = assess_complete_candidate(
                self._instance,
                self._objectives,
                candidate,
                self._assess_plan,
                ensure_feasible=context.ensure_feasible,
            )
            if (
                evaluation is None
                or evaluation.objective_values is None
                or not _route_windows_compliant(
                    candidate,
                    evaluation,
                    route.route_index,
                    self._windows,
                )
            ):
                continue
            candidate_route = candidate.routes[route.route_index]
            modifications = _delivery_position_changes(
                route.route.stops, candidate_route.stops
            )
            if modifications == 0:
                continue
            seen.add(candidate)
            scored.append((candidate, evaluation.objective_values, modifications))
            if len(scored) >= self._candidate_limit:
                break
        return tuple(scored)

    def _base_objective_values(self, base: VrpSolution) -> tuple[object, ...] | None:
        evaluation = assess_complete_candidate(
            self._instance,
            self._objectives,
            base,
            self._assess_plan,
            ensure_feasible=False,
        )
        return None if evaluation is None else evaluation.objective_values

    def _objective_compare(self, left: object, right: object) -> ObjectiveComparison:
        return self._objectives.objectives[self._objective_position].compare(
            left, right
        )

    def _sort_by_target(
        self, candidates: Sequence[_ScoredTour]
    ) -> tuple[_ScoredTour, ...]:
        def compare(left: _ScoredTour, right: _ScoredTour) -> int:
            result = self._objective_compare(
                left[1][self._objective_position],
                right[1][self._objective_position],
            )
            if result is ObjectiveComparison.LESS:
                return -1
            if result is ObjectiveComparison.GREATER:
                return 1
            return 0

        return tuple(sorted(candidates, key=cmp_to_key(compare)))

    def _best_candidate(
        self, candidates: Sequence[_ScoredTour], context: CandidateContext
    ) -> _ScoredTour:
        ordered = self._sort_by_target(candidates)
        best_value = ordered[0][1][self._objective_position]
        tied = tuple(
            item
            for item in ordered
            if self._objective_compare(item[1][self._objective_position], best_value)
            is ObjectiveComparison.EQUAL
        )
        return choose_uniformly(tied, context.random_source)

    def _regret_gap(self, candidates: Sequence[_ScoredTour]) -> float:
        ordered = self._sort_by_target(candidates)
        if len(ordered) < 2:
            return inf
        best = cast(float, ordered[0][1][self._objective_position])
        second = cast(float, ordered[1][1][self._objective_position])
        if best == inf and second == inf:
            return 0.0
        return abs(second - best)

    def _rank_completed(
        self,
        candidates: Sequence[_ScoredTour],
        base_values: tuple[object, ...],
        context: CandidateContext,
    ) -> tuple[_ScoredTour, ...]:
        objective = self._objectives.objectives[self._objective_position]
        improving = tuple(
            item
            for item in candidates
            if objective.compare(
                item[1][self._objective_position],
                base_values[self._objective_position],
            )
            is ObjectiveComparison.LESS
        )
        if not improving:
            return ()
        frontier = target_first_nondominated_scored_plans(
            tuple((item[0], item[1]) for item in improving),
            self._objectives,
            objective_position=self._objective_position,
        )
        selected = {solution for solution, _values in frontier}
        frontier_items = tuple(item for item in improving if item[0] in selected)
        return randomized_order(frontier_items, context.random_source)

    def _result(
        self,
        candidates: Sequence[_ScoredTour],
        base_values: tuple[object, ...] | None,
        context: CandidateContext,
    ) -> OperatorResult:
        if base_values is None:
            return OperatorResult(candidates=())
        ranked = self._rank_completed(candidates, base_values, context)
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), modifications)
                for solution, _values, modifications in ranked[: context.max_candidates]
            )
        )


class ObjectiveGuidedTourRegretInsertionOperator(_ObjectiveGuidedTourSearch):
    """Reinsert delivery stops using regret in the selected objective.

    :param objective_id: Declared route-order objective used to score insertions.
    :param max_neighborhood_size: Maximum number of deliveries selected to move.
    :param max_candidate_alternatives: Bound on scored tour alternatives.
    :raises ValueError: If the objective is absent or is the time-window penalty.
    """

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return objective-improving tours that pass strict scenario checks."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        base_values = self._base_objective_values(base)
        if base_values is None:
            return OperatorResult(candidates=())
        routes = self._eligible_routes(base)
        if not routes:
            return OperatorResult(candidates=())
        selected_route = choose_uniformly(routes, context.random_source)
        target_count = min(size, len(selected_route.deliveries))
        completed: list[_ScoredTour] = []
        seen: set[VrpSolution] = {base}

        for _trial in range(self._candidate_limit):
            targets = randomized_sample(
                selected_route.deliveries, target_count, context.random_source
            )
            working = base
            pending = [stop.activity_ref.activity_id for stop in targets]
            last_scored: _ScoredTour | None = None
            while pending:
                alternatives_by_package: list[tuple[str, tuple[_ScoredTour, ...]]] = []
                for package_id in pending:
                    current_route = self._route_at(working, selected_route.route_index)
                    if current_route is None:
                        continue
                    alternatives = self._insertion_alternatives(
                        working, current_route, package_id, context
                    )
                    if alternatives:
                        alternatives_by_package.append((package_id, alternatives))
                if not alternatives_by_package:
                    break
                package_id, alternatives = self._select_regret_package(
                    alternatives_by_package, context
                )
                last_scored = self._best_candidate(alternatives, context)
                working = last_scored[0]
                pending.remove(package_id)

            if last_scored is not None and working != base and working not in seen:
                completed.append(
                    (
                        working,
                        last_scored[1],
                        _delivery_position_changes(
                            base.routes[selected_route.route_index].stops,
                            working.routes[selected_route.route_index].stops,
                        ),
                    )
                )
                seen.add(working)
                if len(completed) >= self._candidate_limit:
                    break
        return self._result(completed, base_values, context)

    def _insertion_alternatives(
        self,
        base: VrpSolution,
        route: _TourRoute,
        package_id: str,
        context: CandidateContext,
    ) -> tuple[_ScoredTour, ...]:
        current_order = route.deliveries
        selected = next(
            (
                stop
                for stop in current_order
                if stop.activity_ref.activity_id == package_id
            ),
            None,
        )
        if selected is None:
            return ()
        reduced = tuple(stop for stop in current_order if stop != selected)
        completed: list[_ScoredTour] = []
        seen: set[VrpSolution] = set()
        insertion_indices: tuple[int, ...] = tuple(range(len(reduced) + 1))
        if len(insertion_indices) > self._candidate_limit:
            insertion_indices = randomized_sample(
                insertion_indices,
                self._candidate_limit,
                context.random_source,
            )
        for index in insertion_indices:
            order = (*reduced[:index], selected, *reduced[index:])
            if order == current_order:
                continue
            position_candidates = self._assess_order(base, route, order, context)
            if position_candidates:
                item = self._best_candidate(position_candidates, context)
                if item[0] not in seen:
                    seen.add(item[0])
                    completed.append(item)
        return tuple(completed)

    def _select_regret_package(
        self,
        alternatives: Sequence[tuple[str, tuple[_ScoredTour, ...]]],
        context: CandidateContext,
    ) -> tuple[str, tuple[_ScoredTour, ...]]:
        singletons = tuple(item for item in alternatives if len(item[1]) == 1)
        if singletons:
            return self._select_best_package(singletons, context)
        regrets = tuple((item, self._regret_gap(item[1])) for item in alternatives)
        largest_regret = max(regret for _item, regret in regrets)
        regret_ties = tuple(
            item for item, regret in regrets if regret == largest_regret
        )
        return self._select_best_package(regret_ties, context)

    def _select_best_package(
        self,
        alternatives: Sequence[tuple[str, tuple[_ScoredTour, ...]]],
        context: CandidateContext,
    ) -> tuple[str, tuple[_ScoredTour, ...]]:
        best = self._best_candidate(alternatives[0][1], context)
        best_value = best[1][self._objective_position]
        choices = [alternatives[0]]
        for item in alternatives[1:]:
            candidate = self._best_candidate(item[1], context)
            comparison = self._objective_compare(
                candidate[1][self._objective_position], best_value
            )
            if comparison is ObjectiveComparison.LESS:
                best_value = candidate[1][self._objective_position]
                choices = [item]
            elif comparison is ObjectiveComparison.EQUAL:
                choices.append(item)
        return choose_uniformly(choices, context.random_source)


class ObjectiveGuidedBacktrackingTourConstructionOperator(_ObjectiveGuidedTourSearch):
    """Construct delivery orders with bounded feasibility lookahead.

    :param objective_id: Declared route-order objective for completed tours.
    :param max_neighborhood_size: Maximum number of deliveries reconstructed.
    :param max_candidate_alternatives: Bound on search depth and tour results.
    """

    def call(self, context: CandidateContext) -> OperatorResult:
        """Backtrack from dead-end insertions and rank feasible complete tours."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        base_values = self._base_objective_values(base)
        if base_values is None:
            return OperatorResult(candidates=())
        routes = self._eligible_routes(base)
        if not routes:
            return OperatorResult(candidates=())
        selected_route = choose_uniformly(routes, context.random_source)
        target_count = min(size, len(selected_route.deliveries))
        targets = randomized_sample(
            selected_route.deliveries, target_count, context.random_source
        )
        target_ids = {stop.activity_ref.activity_id for stop in targets}
        target_stops = {stop.activity_ref.activity_id: stop for stop in targets}
        fixed = tuple(
            stop
            for stop in selected_route.deliveries
            if stop.activity_ref.activity_id not in target_ids
        )
        completed: list[_ScoredTour] = []
        seen_solutions: set[VrpSolution] = {base}
        seen_states: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
        visited = 0
        node_limit = max(self._candidate_limit, self._candidate_limit * target_count)

        def search(
            partial_order: tuple[ActivityStop, ...], pending: tuple[str, ...]
        ) -> None:
            nonlocal visited
            if visited >= node_limit or len(completed) >= self._candidate_limit:
                return
            visited += 1
            state = (
                tuple(stop.activity_ref.activity_id for stop in partial_order),
                pending,
            )
            if state in seen_states:
                return
            seen_states.add(state)
            if not pending:
                for item in self._assess_order(
                    base, selected_route, partial_order, context
                ):
                    if item[0] not in seen_solutions:
                        seen_solutions.add(item[0])
                        completed.append(item)
                        if len(completed) >= self._candidate_limit:
                            return
                return

            positions_by_package: list[
                tuple[str, tuple[tuple[ActivityStop, ...], ...]]
            ] = []
            for package_id in pending:
                stop = target_stops[package_id]
                feasible_orders: list[tuple[ActivityStop, ...]] = []
                for index in range(len(partial_order) + 1):
                    order = (*partial_order[:index], stop, *partial_order[index:])
                    if self._schedule_order(selected_route, order) is not None:
                        feasible_orders.append(order)
                if not feasible_orders:
                    return
                positions_by_package.append((package_id, tuple(feasible_orders)))

            smallest = min(
                len(options) for _package_id, options in positions_by_package
            )
            constrained = tuple(
                item for item in positions_by_package if len(item[1]) == smallest
            )
            for package_id, chosen_orders in randomized_order(
                constrained, context.random_source
            ):
                pending_after = tuple(item for item in pending if item != package_id)
                selected_options = (
                    chosen_orders
                    if len(chosen_orders) <= self._candidate_limit
                    else randomized_sample(
                        chosen_orders, self._candidate_limit, context.random_source
                    )
                )
                for order in randomized_order(selected_options, context.random_source):
                    search(order, pending_after)

        if self._schedule_order(selected_route, fixed) is not None:
            search(fixed, tuple(sorted(target_ids)))
        return self._result(completed, base_values, context)


class ObjectiveGuidedFeasibleDeliveryEdgeExchangeOperator(_ObjectiveGuidedTourSearch):
    """Improve feasible delivery orders with objective-guided edge exchanges.

    :param objective_id: Declared route-order objective for move comparison.
    :param max_neighborhood_size: Maximum consecutive deliveries changed by a move.
    :param max_candidate_alternatives: Maximum complete moves assessed per call.
    """

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return objective-improving reversals and relocations that stay feasible."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        base_values = self._base_objective_values(base)
        if base_values is None:
            return OperatorResult(candidates=())
        routes = self._eligible_routes(base)
        if not routes:
            return OperatorResult(candidates=())
        route = choose_uniformly(routes, context.random_source)
        current_order = route.deliveries
        orders = self._edge_exchange_orders(current_order, size)
        if not orders:
            return OperatorResult(candidates=())
        sampled = (
            tuple(orders)
            if len(orders) <= self._candidate_limit
            else randomized_sample(
                tuple(orders), self._candidate_limit, context.random_source
            )
        )
        completed: list[_ScoredTour] = []
        seen: set[VrpSolution] = {base}
        for order in sampled:
            if not self._slack_screen(route, current_order, order):
                continue
            for item in self._assess_order(base, route, order, context):
                if item[0] not in seen:
                    seen.add(item[0])
                    completed.append(item)
                    if len(completed) >= self._candidate_limit:
                        break
            if len(completed) >= self._candidate_limit:
                break
        return self._result(completed, base_values, context)

    def _edge_exchange_orders(
        self, delivery_order: Sequence[ActivityStop], maximum_block: int
    ) -> tuple[tuple[ActivityStop, ...], ...]:
        orders: list[tuple[ActivityStop, ...]] = []
        seen: set[tuple[ActivityStop, ...]] = {tuple(delivery_order)}
        stop_count = len(delivery_order)
        for start in range(stop_count - 1):
            for end in range(start + 1, min(stop_count, start + maximum_block)):
                order = (
                    *delivery_order[:start],
                    *reversed(delivery_order[start : end + 1]),
                    *delivery_order[end + 1 :],
                )
                if order not in seen:
                    seen.add(order)
                    orders.append(order)
        for block_size in range(1, min(maximum_block, stop_count) + 1):
            for start in range(stop_count - block_size + 1):
                block = tuple(delivery_order[start : start + block_size])
                reduced = (
                    *delivery_order[:start],
                    *delivery_order[start + block_size :],
                )
                for insertion in range(len(reduced) + 1):
                    order = (
                        *reduced[:insertion],
                        *block,
                        *reduced[insertion:],
                    )
                    if order not in seen:
                        seen.add(order)
                        orders.append(order)
        return tuple(orders)


# Keep the name introduced by the initial draft as a source-compatible alias.
TimeWindowFeasibleTourInsertionOperator = ObjectiveGuidedTourRegretInsertionOperator


def _earliest_service_start(
    arrival: float,
    service_duration: float,
    windows: Sequence[_Window],
    existing_wait: float,
) -> float | None:
    if not windows:
        return arrival + existing_wait
    options = tuple(
        (max(arrival, start), end)
        for start, end in windows
        if max(arrival, start) + service_duration <= end + 1e-9
    )
    if not options:
        return None
    service_start, _end = min(options, key=lambda item: (item[0], -item[1]))
    return service_start


def _latest_service_start(
    latest_bound: float,
    service_duration: float,
    windows: Sequence[_Window],
) -> float | None:
    if not windows:
        return latest_bound
    options = tuple(
        min(end - service_duration, latest_bound)
        for start, end in windows
        if min(end - service_duration, latest_bound) >= start - 1e-9
    )
    return None if not options else max(options)


def _delivery_position_changes(
    base_stops: Sequence[object], candidate_stops: Sequence[object]
) -> int:
    base_positions = {
        stop.activity_ref.activity_id: index
        for index, stop in enumerate(base_stops)
        if isinstance(stop, ActivityStop) and stop.activity_ref.kind == 'delivery'
    }
    candidate_positions = {
        stop.activity_ref.activity_id: index
        for index, stop in enumerate(candidate_stops)
        if isinstance(stop, ActivityStop) and stop.activity_ref.kind == 'delivery'
    }
    return sum(
        base_positions[package_id] != candidate_positions[package_id]
        for package_id in base_positions
    )


def _route_windows_compliant(
    solution: VrpSolution,
    evaluation: ApplicationEvaluation,
    route_index: int,
    windows: Mapping[tuple[str, str], tuple[_Window, ...]],
) -> bool:
    analysis = evaluation.analysis
    if not isinstance(analysis, VrpOperatorAnalysis) or not analysis.is_complete:
        return False
    route = solution.routes[route_index]
    if not analysis.scenario_evaluations:
        return False
    for scenario in analysis.scenario_evaluations:
        route_trace = next(
            (
                item
                for item in scenario.vehicle_days
                if (item.vehicle_id, item.day_index)
                == (route.vehicle_id, route.day_index)
            ),
            None,
        )
        if route_trace is None or len(route_trace.stops) != len(route.stops):
            return False
        for stop, trace in zip(route.stops, route_trace.stops, strict=True):
            if not isinstance(stop, ActivityStop):
                return False
            relevant = windows.get(
                (stop.activity_ref.activity_id, stop.activity_ref.kind), ()
            )
            if not relevant:
                continue
            if trace.service_start is None or trace.service_finish is None:
                return False
            if (
                min(
                    _lower_violation(trace.service_start, start)
                    + _upper_violation(trace.service_finish, end)
                    for start, end in relevant
                )
                > 1e-9
            ):
                return False
    return True


def _shortest_travel_times(
    instance: LoadedVrpInstance,
) -> dict[tuple[str, str], float]:
    arc_times = _minimum_arc_times(instance)
    outgoing: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for arc in instance.network_data.arcs:
        weight = arc_times.get(arc.arc_id)
        if weight is not None and isfinite(weight):
            outgoing[arc.tail_node_id].append((arc.head_node_id, weight))
    relevant_nodes = {
        node_id
        for package in instance.package_data.packages
        for node_id in (package.pickup_node_id, package.delivery_node_id)
    }
    relevant_nodes.update(depot.node_id for depot in instance.network_data.depots)
    result: dict[tuple[str, str], float] = {}
    for source in relevant_nodes:
        distances = {source: 0.0}
        queue = [(0.0, source)]
        while queue:
            distance, node = heappop(queue)
            if distance != distances.get(node):
                continue
            for destination, weight in outgoing.get(node, ()):
                candidate = distance + weight
                if candidate < distances.get(destination, inf):
                    distances[destination] = candidate
                    heappush(queue, (candidate, destination))
        result.update(
            {(source, node): distance for node, distance in distances.items()}
        )
    return result


def _minimum_arc_times(instance: LoadedVrpInstance) -> dict[str, float]:
    arc_times: dict[str, float] = {}
    for (
        _scenario,
        arc_id,
        _day,
        _boundary,
    ), value in instance.scenario_bank.travel_times.items():
        arc_times[arc_id] = min(arc_times.get(arc_id, inf), value)
    return arc_times


def _bounded_limit(value: int, name: str, maximum: int) -> int:
    limit = require_integer(value, name=name, minimum=1)
    if limit > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return limit
