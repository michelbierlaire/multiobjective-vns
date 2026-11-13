"""Pareto trade-off route moves for multi-day VRP plans.

Created: 2026-11-04T09:12:38+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Literal

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    candidate_is_eligible,
    choose_uniformly,
    operator_result,
    randomized_order,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    assess_complete_candidate,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    PathSearchIndex,
    build_path_search_index,
    rebuild_changed_routes,
    route_node_sequence,
    sample_loopless_paths,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
    RouteStop,
    TransferOperationStop,
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
from multiobjective_vns.values import finite_real, require_integer

__all__ = ['ParetoTradeoffOperator']

type ParetoMoveFamily = Literal['path', 'timing', 'activity']
type _Alternative = tuple[VrpSolution, tuple[object, ...]]
type _TimedAlternative = tuple[VrpSolution, tuple[object, ...], int]
type _TimingTarget = tuple[int, int | None, str]
type _ActivityPosition = tuple[int, int]

_STOP_TYPES = (ActivityStop, TransferOperationStop, DepotOnlyStop)
_TIMING_FIELDS = ('pre_service_wait', 'wait', 'break_duration')


class ParetoTradeoffOperator:
    """Select a sampled route move from the nondominated alternatives.

    :param instance: Immutable VRP data used to rebuild and assess plans.
    :param objective_declaration: Complete ordered objective vector.
    :param assess_plan: Shared pure application plan assessor.
    :param move_family: Route path, timing, or activity relocation move.
    :param max_neighborhood_size: Maximum number of sequential move units.
    :param time_step: Positive step used by timing moves.
    :param max_candidate_alternatives: Maximum complete alternatives assessed.
    :param max_paths_per_leg: Maximum sampled paths for one route leg.
    :param max_arc_expansion_steps: Per-path-search expansion bound.
    :raises TypeError: If a setting has an unsupported Python type.
    :raises ValueError: If a setting is outside its documented range.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_move_family',
        '_time_step',
        '_maximum_size',
        '_candidate_limit',
        '_path_limit',
        '_expansion_limit',
        '_path_search_index',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        move_family: ParetoMoveFamily,
        max_neighborhood_size: int,
        time_step: float = 1.0,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        if move_family not in {'path', 'timing', 'activity'}:
            raise ValueError(f'unsupported Pareto move family: {move_family!r}')
        if len(objective_declaration.objectives) < 2:
            raise ValueError('Pareto trade-off moves require at least two objectives')
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._move_family = move_family
        self._time_step = finite_real(time_step, name='time_step')
        if self._time_step <= 0.0:
            raise ValueError('time_step must be positive')
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
        self._path_search_index: PathSearchIndex | None = None

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` Pareto trade-off route moves.

        Each move ranks at most the configured number of complete alternatives
        with the standard componentwise minimization relation. Candidate
        assessment and random selection use the registered shared boundaries.

        :param context: VRP base, requested size, feasibility request, and RNG.
        :returns: Complete candidate proposals with move counts, or an empty result.
        :raises TypeError: If the base or assessor result violates its contract.
        :raises ValueError: If the requested size or objective vector is invalid.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        if context.ensure_feasible and not candidate_is_eligible(
            self._instance,
            self._objectives,
            base,
            ensure_feasible=True,
            assess_plan=self._assess_plan,
        ):
            return operator_result(None, modifications=0, requested_size=size)
        if self._move_family == 'path':
            return self._path_result(base, size, context)
        if self._move_family == 'timing':
            return self._timing_result(base, size, context)

        return self._activity_result(base, size, context)

    def _activity_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        working = base
        moved_units: set[tuple[object, ...]] = set()
        modifications = 0
        last_frontier: tuple[_Alternative, ...] = ()

        for move_position in range(size):
            target = self._select_target(working, moved_units, context)
            if target is None:
                break
            candidates = self._activity_alternatives(working, target, context)
            unit = _unit_key(target)
            if not candidates:
                moved_units.add(unit)
                continue
            frontier = _nondominated(candidates, self._objectives)
            if not frontier:
                moved_units.add(unit)
                continue
            last_frontier = frontier
            moved_units.add(unit)
            modifications += 1

            if move_position + 1 < size:
                working = choose_uniformly(frontier, context.random_source)[0]

        if not last_frontier:
            return OperatorResult(candidates=())
        proposals = tuple(
            CandidateProposal(Candidate(solution), modifications)
            for solution, _values in randomized_order(
                last_frontier, context.random_source
            )[: context.max_candidates]
        )
        return OperatorResult(candidates=proposals)

    def _path_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        working = base
        moved_units: set[tuple[object, ...]] = set()
        modifications = 0
        last_frontier: tuple[_Alternative, ...] = ()

        for move_position in range(size):
            target = self._select_target(working, moved_units, context)
            if target is None:
                break
            candidates = self._path_alternatives(working, target, context)
            unit = _unit_key(target)
            if not candidates:
                moved_units.add(unit)
                continue

            frontier = _nondominated(candidates, self._objectives)
            if not frontier:
                moved_units.add(unit)
                continue
            last_frontier = frontier
            moved_units.add(unit)
            modifications += 1

            if move_position + 1 < size:
                working = choose_uniformly(frontier, context.random_source)[0]

        if not last_frontier:
            return OperatorResult(candidates=())
        proposals = tuple(
            CandidateProposal(Candidate(solution), modifications)
            for solution, _values in randomized_order(
                last_frontier, context.random_source
            )[: context.max_candidates]
        )
        return OperatorResult(candidates=proposals)

    def _timing_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        active_routes = tuple(
            route_index
            for route_index in range(len(base.routes))
            if _timing_targets(base, route_index, self._time_step)
        )
        if not active_routes:
            return OperatorResult(candidates=())
        route_index = choose_uniformly(active_routes, context.random_source)
        base_route = base.routes[route_index]
        alternatives: list[_TimedAlternative] = []
        seen_route_states: set[VehicleDayRoute] = set()

        for _ in range(self._candidate_limit):
            trial = base
            modifications = 0
            for _ in range(size):
                targets = _timing_targets(trial, route_index, self._time_step)
                if not targets:
                    break
                target = choose_uniformly(targets, context.random_source)
                delta = choose_uniformly(
                    (-self._time_step, self._time_step), context.random_source
                )
                changed = _adjust_timing(trial, target, delta)
                if (
                    changed is None
                    or changed.routes[route_index] == trial.routes[route_index]
                ):
                    continue
                trial = changed
                modifications += 1

            route_state = trial.routes[route_index]
            if modifications == 0 or route_state == base_route:
                continue
            if route_state in seen_route_states:
                continue
            seen_route_states.add(route_state)
            values = self._assess(trial, context)
            if values is not None:
                alternatives.append((trial, values, modifications))

        frontier = _nondominated_timed(tuple(alternatives), self._objectives)
        ordered = randomized_order(frontier, context.random_source)
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), modifications)
                for solution, _values, modifications in ordered[
                    : context.max_candidates
                ]
            )
        )

    def _select_target(
        self,
        solution: VrpSolution,
        moved_units: set[tuple[object, ...]],
        context: CandidateContext,
    ) -> tuple[object, ...] | None:
        if self._move_family == 'path':
            active_routes = tuple(
                index
                for index, route in enumerate(solution.routes)
                if any(
                    ('path', index, leg) not in moved_units
                    for leg in range(len(route.legs))
                )
            )
            if not active_routes:
                return None
            route_index = choose_uniformly(active_routes, context.random_source)
            leg_indices = tuple(
                leg_index
                for leg_index in range(len(solution.routes[route_index].legs))
                if ('path', route_index, leg_index) not in moved_units
            )
            leg_index = choose_uniformly(leg_indices, context.random_source)
            return ('path', route_index, leg_index)

        route_indices = tuple(
            route_index
            for route_index, route in enumerate(solution.routes)
            if any(
                type(stop) is ActivityStop
                and (
                    'activity',
                    stop.activity_ref.kind,
                    stop.activity_ref.activity_id,
                )
                not in moved_units
                for stop in route.stops
            )
        )
        if not route_indices:
            return None
        route_index = choose_uniformly(route_indices, context.random_source)
        activity_targets = tuple(
            (
                stop_index,
                stop.activity_ref.kind,
                stop.activity_ref.activity_id,
            )
            for stop_index, stop in enumerate(solution.routes[route_index].stops)
            if type(stop) is ActivityStop
            and (
                'activity',
                stop.activity_ref.kind,
                stop.activity_ref.activity_id,
            )
            not in moved_units
        )
        selected = choose_uniformly(activity_targets, context.random_source)
        return ('activity', route_index, *selected)

    def _path_alternatives(
        self,
        working: VrpSolution,
        target: tuple[object, ...],
        context: CandidateContext,
    ) -> tuple[_Alternative, ...]:
        _family, route_index, leg_index = target
        route_position = cast_int(route_index, 'route index')
        leg_position = cast_int(leg_index, 'leg index')
        route = working.routes[route_position]
        nodes = route_node_sequence(self._instance, working, route)
        path_search_index = self._path_search_index
        if path_search_index is None:
            path_search_index = build_path_search_index(self._instance)
            self._path_search_index = path_search_index
        paths = sample_loopless_paths(
            self._instance,
            nodes[leg_position],
            nodes[leg_position + 1],
            random_source=context.random_source,
            max_paths=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            path_search_index=path_search_index,
        )
        alternatives: list[_Alternative] = []
        sampled_paths = randomized_sample(
            tuple(path for path in paths if path != route.legs[leg_position].arc_ids),
            self._candidate_limit,
            context.random_source,
        )
        for path in sampled_paths:
            routes = list(working.routes)
            legs = list(route.legs)
            legs[leg_position] = RouteLeg(path)
            routes[route_position] = replace(route, legs=tuple(legs))
            candidate = replace(working, routes=tuple(routes))
            assessed = self._assess(candidate, context)
            if assessed is not None:
                alternatives.append((candidate, assessed))
        return tuple(alternatives)

    def _activity_alternatives(
        self,
        working: VrpSolution,
        target: tuple[object, ...],
        context: CandidateContext,
    ) -> tuple[_Alternative, ...]:
        _family, route_index, stop_index, _kind, _activity_id = target
        source = (
            cast_int(route_index, 'route index'),
            cast_int(stop_index, 'stop index'),
        )
        positions = _insertion_positions(working, source)
        sampled_positions = randomized_sample(
            positions, self._candidate_limit, context.random_source
        )
        if not sampled_positions:
            return ()
        path_search_index = self._path_search_index
        if path_search_index is None:
            path_search_index = build_path_search_index(self._instance)
            self._path_search_index = path_search_index

        alternatives: list[_Alternative] = []
        for destination_route, insertion_index in sampled_positions:
            stops_by_route = _relocate_stops(
                working, source, destination_route, insertion_index
            )
            rebuilt = rebuild_changed_routes(
                self._instance,
                working,
                stops_by_route,
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=self._candidate_limit - len(alternatives),
                path_search_index=path_search_index,
            )
            for candidate in rebuilt:
                if candidate == working:
                    continue
                assessed = self._assess(candidate, context)
                if assessed is not None:
                    alternatives.append((candidate, assessed))
                if len(alternatives) == self._candidate_limit:
                    return tuple(alternatives)
        return tuple(alternatives)

    def _assess(
        self, candidate: VrpSolution, context: CandidateContext
    ) -> tuple[object, ...] | None:
        evaluation = assess_complete_candidate(
            self._instance,
            self._objectives,
            candidate,
            self._assess_plan,
            ensure_feasible=context.ensure_feasible,
        )
        return None if evaluation is None else evaluation.objective_values


def _nondominated(
    candidates: tuple[_Alternative, ...], objectives: ObjectiveDeclaration
) -> tuple[_Alternative, ...]:
    return tuple(
        candidate
        for index, candidate in enumerate(candidates)
        if not any(
            other_index != index and _dominates(other[1], candidate[1], objectives)
            for other_index, other in enumerate(candidates)
        )
    )


def _nondominated_timed(
    candidates: tuple[_TimedAlternative, ...], objectives: ObjectiveDeclaration
) -> tuple[_TimedAlternative, ...]:
    return tuple(
        candidate
        for index, candidate in enumerate(candidates)
        if not any(
            other_index != index and _dominates(other[1], candidate[1], objectives)
            for other_index, other in enumerate(candidates)
        )
    )


def _dominates(
    left: tuple[object, ...],
    right: tuple[object, ...],
    objectives: ObjectiveDeclaration,
) -> bool:
    strictly_better = False
    for position, objective in enumerate(objectives.objectives):
        comparison = objective.compare(left[position], right[position])
        if comparison is ObjectiveComparison.GREATER:
            return False
        if comparison is ObjectiveComparison.LESS:
            strictly_better = True
    return strictly_better


def _timing_targets(
    solution: VrpSolution, route_index: int, time_step: float
) -> tuple[_TimingTarget, ...]:
    targets: list[_TimingTarget] = []
    start_target = (route_index, None, 'start_time')
    if any(
        _timing_delta_is_valid(solution, start_target, delta)
        for delta in (-time_step, time_step)
    ):
        targets.append(start_target)
    route = solution.routes[route_index]
    for stop_index, stop in enumerate(route.stops):
        if type(stop) not in _STOP_TYPES:
            continue
        fields = (
            ('wait', 'break_duration')
            if type(stop) is DepotOnlyStop
            else _TIMING_FIELDS
        )
        for field in fields:
            if any(
                _timing_delta_is_valid(
                    solution, (route_index, stop_index, field), delta
                )
                for delta in (-time_step, time_step)
            ):
                targets.append((route_index, stop_index, field))
    return tuple(targets)


def _timing_delta_is_valid(
    solution: VrpSolution, target: _TimingTarget, delta: float
) -> bool:
    route_index, stop_index, field = target
    route = solution.routes[route_index]
    if stop_index is None:
        value = route.start_time + delta
        return isfinite(value) and value >= 0.0
    stop = route.stops[stop_index]
    value = getattr(stop, field) + delta
    if not isfinite(value) or value < 0.0:
        return False
    break_duration = value if field == 'break_duration' else stop.break_duration
    wait = value if field == 'wait' else stop.wait
    return break_duration <= wait


def _adjust_timing(
    solution: VrpSolution, target: _TimingTarget, delta: float
) -> VrpSolution | None:
    route_index, stop_index, field = target
    routes = list(solution.routes)
    route = routes[route_index]
    if stop_index is None:
        value = route.start_time + delta
        if not isfinite(value) or value < 0.0:
            return None
        routes[route_index] = replace(route, start_time=value)
        return replace(solution, routes=tuple(routes))
    stops = list(route.stops)
    stop = stops[stop_index]
    value = getattr(stop, field) + delta
    if not isfinite(value) or value < 0.0:
        return None
    updated = replace(stop, **{field: value})
    if updated.break_duration > updated.wait:
        return None
    stops[stop_index] = updated
    routes[route_index] = replace(route, stops=tuple(stops))
    return replace(solution, routes=tuple(routes))


def _insertion_positions(
    solution: VrpSolution, source: _ActivityPosition
) -> tuple[_ActivityPosition, ...]:
    source_route, source_stop = source
    return tuple(
        (route_index, insertion_index)
        for route_index, route in enumerate(solution.routes)
        for insertion_index in range(len(route.stops) + 1)
        if not (
            route_index == source_route
            and insertion_index in (source_stop, source_stop + 1)
        )
    )


def _relocate_stops(
    solution: VrpSolution,
    source: _ActivityPosition,
    destination_route: int,
    insertion_index: int,
) -> dict[int, tuple[RouteStop, ...]]:
    source_route, source_stop = source
    selected = solution.routes[source_route].stops[source_stop]
    source_stops = list(solution.routes[source_route].stops)
    del source_stops[source_stop]
    if destination_route == source_route:
        if insertion_index > source_stop:
            insertion_index -= 1
        source_stops.insert(insertion_index, selected)
        return {source_route: tuple(source_stops)}
    destination_stops = list(solution.routes[destination_route].stops)
    destination_stops.insert(insertion_index, selected)
    return {
        source_route: tuple(source_stops),
        destination_route: tuple(destination_stops),
    }


def _bounded_limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result


def cast_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f'{name} must be an integer')
    return value


def cast_str(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f'{name} must be a string')
    return value


def _unit_key(target: tuple[object, ...]) -> tuple[object, ...]:
    if target[0] == 'activity':
        return ('activity', target[3], target[4])
    return target
