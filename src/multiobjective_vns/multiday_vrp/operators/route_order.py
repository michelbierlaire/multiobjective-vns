"""Generic stop-order Operators for multi-day VRP plans.

Created: 2026-11-03T20:25:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from itertools import combinations
from typing import ClassVar

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    candidate_is_eligible,
    choose_uniformly,
    operator_result,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    rebuild_changed_routes,
)
from multiobjective_vns.multiday_vrp.solution import RouteStop, VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.values import require_integer

__all__ = [
    'ExchangeOperator',
    'OrOptChainRelocationOperator',
    'RelocateOperator',
    'SwapStarOperator',
    'TwoOptOperator',
    'TwoOptStarOperator',
]

type RouteTarget = tuple[int, ...]
type MoveBuilder = Callable[[VrpSolution, RouteTarget, int], tuple[VrpSolution, ...]]


@dataclass(frozen=True, slots=True)
class _RouteMoveDefinition:
    name: str
    targets: Callable[[VrpSolution, int], tuple[RouteTarget, ...]]
    build: MoveBuilder
    chain_size: bool = False


def _same_day_route_pairs(solution: VrpSolution) -> Iterator[tuple[int, int]]:
    for left, right in combinations(range(len(solution.routes)), 2):
        if solution.routes[left].day_index == solution.routes[right].day_index:
            yield left, right


def _two_opt_targets(solution: VrpSolution, _size: int) -> tuple[RouteTarget, ...]:
    return tuple(
        (route_index, start, end)
        for route_index, route in enumerate(solution.routes)
        for start in range(len(route.stops))
        for end in range(start + 1, len(route.stops))
    )


def _build_two_opt(
    solution: VrpSolution, target: RouteTarget, _size: int
) -> tuple[VrpSolution, ...]:
    route_index, start, end = target
    route = solution.routes[route_index]
    stops = (
        *route.stops[:start],
        *reversed(route.stops[start : end + 1]),
        *route.stops[end + 1 :],
    )
    return (_replace_stops(solution, {route_index: tuple(stops)}),)


def _relocate_targets(solution: VrpSolution, _size: int) -> tuple[RouteTarget, ...]:
    return tuple(
        (route_index, stop_index)
        for route_index, route in enumerate(solution.routes)
        for stop_index in range(len(route.stops))
    )


def _build_relocate(
    solution: VrpSolution, target: RouteTarget, _size: int
) -> tuple[VrpSolution, ...]:
    source_index, stop_index = target
    source_route = solution.routes[source_index]
    stop = source_route.stops[stop_index]
    reduced_source = list(source_route.stops)
    reduced_source.pop(stop_index)
    candidates: list[VrpSolution] = []
    for destination_index, destination_route in enumerate(solution.routes):
        if destination_index == source_index:
            for insertion_index in range(len(source_route.stops) + 1):
                adjusted = insertion_index - (insertion_index > stop_index)
                stops = list(reduced_source)
                stops.insert(adjusted, stop)
                candidates.append(
                    _replace_stops(solution, {source_index: tuple(stops)})
                )
            continue
        for insertion_index in range(len(destination_route.stops) + 1):
            destination_stops = list(destination_route.stops)
            destination_stops.insert(insertion_index, stop)
            candidates.append(
                _replace_stops(
                    solution,
                    {
                        source_index: tuple(reduced_source),
                        destination_index: tuple(destination_stops),
                    },
                )
            )
    return tuple(candidates)


def _or_opt_targets(solution: VrpSolution, size: int) -> tuple[RouteTarget, ...]:
    return tuple(
        (route_index, start)
        for route_index, route in enumerate(solution.routes)
        for start in range(max(0, len(route.stops) - size + 1))
    )


def _build_or_opt(
    solution: VrpSolution, target: RouteTarget, size: int
) -> tuple[VrpSolution, ...]:
    source_index, start = target
    source_route = solution.routes[source_index]
    block = source_route.stops[start : start + size]
    reduced_source = (*source_route.stops[:start], *source_route.stops[start + size :])
    candidates: list[VrpSolution] = []
    for destination_index, destination_route in enumerate(solution.routes):
        if source_route.day_index != destination_route.day_index:
            continue
        destination_base = (
            reduced_source
            if destination_index == source_index
            else destination_route.stops
        )
        for insertion_index in range(len(destination_base) + 1):
            destination_stops = (
                *destination_base[:insertion_index],
                *block,
                *destination_base[insertion_index:],
            )
            replacements = {destination_index: tuple(destination_stops)}
            if destination_index != source_index:
                replacements[source_index] = tuple(reduced_source)
            candidates.append(_replace_stops(solution, replacements))
    return tuple(candidates)


def _exchange_targets(solution: VrpSolution, _size: int) -> tuple[RouteTarget, ...]:
    positions = tuple(
        (route_index, stop_index)
        for route_index, route in enumerate(solution.routes)
        for stop_index in range(len(route.stops))
    )
    return tuple(
        (left_route, left_stop, right_route, right_stop)
        for (left_route, left_stop), (right_route, right_stop) in combinations(
            positions, 2
        )
        if solution.routes[left_route].day_index
        == solution.routes[right_route].day_index
    )


def _build_exchange(
    solution: VrpSolution, target: RouteTarget, _size: int
) -> tuple[VrpSolution, ...]:
    left_route, left_stop, right_route, right_stop = target
    if left_route == right_route:
        stops = list(solution.routes[left_route].stops)
        stops[left_stop], stops[right_stop] = stops[right_stop], stops[left_stop]
        return (_replace_stops(solution, {left_route: tuple(stops)}),)
    left_stops = list(solution.routes[left_route].stops)
    right_stops = list(solution.routes[right_route].stops)
    left_stops[left_stop], right_stops[right_stop] = (
        right_stops[right_stop],
        left_stops[left_stop],
    )
    return (
        _replace_stops(
            solution,
            {left_route: tuple(left_stops), right_route: tuple(right_stops)},
        ),
    )


def _two_opt_star_targets(solution: VrpSolution, _size: int) -> tuple[RouteTarget, ...]:
    return tuple(
        (left, right, left_cut, right_cut)
        for left, right in _same_day_route_pairs(solution)
        for left_cut in range(len(solution.routes[left].stops) + 1)
        for right_cut in range(len(solution.routes[right].stops) + 1)
    )


def _build_two_opt_star(
    solution: VrpSolution, target: RouteTarget, _size: int
) -> tuple[VrpSolution, ...]:
    left_index, right_index, left_cut, right_cut = target
    left = solution.routes[left_index].stops
    right = solution.routes[right_index].stops
    return (
        _replace_stops(
            solution,
            {
                left_index: (*left[: left_cut + 1], *right[right_cut + 1 :]),
                right_index: (*right[: right_cut + 1], *left[left_cut + 1 :]),
            },
        ),
    )


def _swap_star_targets(solution: VrpSolution, _size: int) -> tuple[RouteTarget, ...]:
    return tuple(
        (left, left_stop, right, right_stop)
        for left, right in _same_day_route_pairs(solution)
        for left_stop in range(len(solution.routes[left].stops))
        for right_stop in range(len(solution.routes[right].stops))
    )


def _build_swap_star(
    solution: VrpSolution, target: RouteTarget, _size: int
) -> tuple[VrpSolution, ...]:
    left_index, left_stop_index, right_index, right_stop_index = target
    left_route = solution.routes[left_index]
    right_route = solution.routes[right_index]
    left_stop = left_route.stops[left_stop_index]
    right_stop = right_route.stops[right_stop_index]
    left_reduced = (
        *left_route.stops[:left_stop_index],
        *left_route.stops[left_stop_index + 1 :],
    )
    right_reduced = (
        *right_route.stops[:right_stop_index],
        *right_route.stops[right_stop_index + 1 :],
    )
    candidates: list[VrpSolution] = []
    for left_insert in range(len(left_reduced) + 1):
        if left_insert == right_stop_index:
            continue
        new_left = (
            *left_reduced[:left_insert],
            right_stop,
            *left_reduced[left_insert:],
        )
        for right_insert in range(len(right_reduced) + 1):
            if right_insert == left_stop_index:
                continue
            new_right = (
                *right_reduced[:right_insert],
                left_stop,
                *right_reduced[right_insert:],
            )
            if new_left == left_route.stops and new_right == right_route.stops:
                continue
            candidates.append(
                _replace_stops(
                    solution,
                    {left_index: tuple(new_left), right_index: tuple(new_right)},
                )
            )
    return tuple(candidates)


def _replace_stops(
    solution: VrpSolution,
    replacements: dict[int, tuple[RouteStop, ...]],
) -> VrpSolution:
    routes = list(solution.routes)
    for route_index, stops in replacements.items():
        routes[route_index] = replace(routes[route_index], stops=stops)
    return replace(solution, routes=tuple(routes))


_TWO_OPT = _RouteMoveDefinition('2-opt', _two_opt_targets, _build_two_opt)
_RELOCATE = _RouteMoveDefinition('relocate', _relocate_targets, _build_relocate)
_OR_OPT = _RouteMoveDefinition(
    'Or-opt', _or_opt_targets, _build_or_opt, chain_size=True
)
_EXCHANGE = _RouteMoveDefinition('exchange', _exchange_targets, _build_exchange)
_TWO_OPT_STAR = _RouteMoveDefinition(
    '2-opt*', _two_opt_star_targets, _build_two_opt_star
)
_SWAP_STAR = _RouteMoveDefinition('SWAP*', _swap_star_targets, _build_swap_star)


class _RouteOrderOperator:
    """Apply one route move definition through the shared VRP assessment."""

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_maximum_size',
        '_candidate_limit',
        '_path_limit',
        '_expansion_limit',
    )
    _definition: ClassVar[_RouteMoveDefinition]

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        max_neighborhood_size: int,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._maximum_size = require_integer(
            max_neighborhood_size,
            name='max_neighborhood_size',
            minimum=1,
        )
        self._candidate_limit = _bounded_limit(
            max_candidate_alternatives,
            name='max_candidate_alternatives',
            minimum=1,
            maximum=32,
        )
        self._path_limit = _bounded_limit(
            max_paths_per_leg,
            name='max_paths_per_leg',
            minimum=1,
            maximum=32,
        )
        self._expansion_limit = _bounded_limit(
            max_arc_expansion_steps,
            name='max_arc_expansion_steps',
            minimum=1,
            maximum=10000,
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` move units to a working plan.

        :param context: Base solution, size, feasibility requirement, and RNG.
        :returns: One changed plan and its move count, or an empty result.
        :raises TypeError: If the base or a complete assessment is malformed.
        :raises ValueError: If the requested size exceeds this Operator's limit.
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
        if self._definition.chain_size:
            candidate = self._apply_one(base, context, size)
            return operator_result(
                candidate,
                modifications=size if candidate is not None else 0,
                requested_size=size,
            )
        working = base
        modifications = 0
        for _ in range(size):
            candidate = self._apply_one(working, context, size)
            if candidate is None:
                break
            working = candidate
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _apply_one(
        self, working: VrpSolution, context: CandidateContext, size: int
    ) -> VrpSolution | None:
        targets = self._definition.targets(working, size)
        if not targets:
            return None
        target = choose_uniformly(targets, context.random_source)
        proposals = randomized_sample(
            _unique_solutions(self._definition.build(working, target, size)),
            self._candidate_limit,
            context.random_source,
        )
        alternatives: list[VrpSolution] = []
        for proposal in proposals:
            changed_stops = {
                route_index: route.stops
                for route_index, route in enumerate(proposal.routes)
                if route.stops != working.routes[route_index].stops
            }
            if not changed_stops:
                continue
            alternatives.extend(
                rebuild_changed_routes(
                    self._instance,
                    working,
                    changed_stops,
                    random_source=context.random_source,
                    max_paths_per_leg=self._path_limit,
                    max_arc_expansion_steps=self._expansion_limit,
                    max_candidate_alternatives=self._candidate_limit,
                )
            )
            if len(alternatives) >= self._candidate_limit:
                break
        sampled = randomized_sample(
            _unique_solutions(tuple(alternatives[: self._candidate_limit])),
            self._candidate_limit,
            context.random_source,
        )
        eligible = tuple(
            candidate
            for candidate in sampled
            if candidate != working
            and candidate_is_eligible(
                self._instance,
                self._objectives,
                candidate,
                ensure_feasible=context.ensure_feasible,
                assess_plan=self._assess_plan,
            )
        )
        return choose_uniformly(eligible, context.random_source) if eligible else None


class TwoOptOperator(_RouteOrderOperator):
    """Reverse one contiguous stop subsequence at a time."""

    _definition = _TWO_OPT


class RelocateOperator(_RouteOrderOperator):
    """Relocate one stop occurrence within an active day-level route set."""

    _definition = _RELOCATE


class OrOptChainRelocationOperator(_RouteOrderOperator):
    """Move one contiguous stop block whose length is the requested ``k``."""

    _definition = _OR_OPT


class ExchangeOperator(_RouteOrderOperator):
    """Exchange two stop occurrences on the same day."""

    _definition = _EXCHANGE


class TwoOptStarOperator(_RouteOrderOperator):
    """Exchange route suffixes after selected cuts on the same day."""

    _definition = _TWO_OPT_STAR


class SwapStarOperator(_RouteOrderOperator):
    """Exchange one stop pair with independently selected insertion positions."""

    _definition = _SWAP_STAR


def _bounded_limit(value: int, *, name: str, minimum: int, maximum: int) -> int:
    normalized = require_integer(value, name=name, minimum=minimum)
    if normalized > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return normalized


def _unique_solutions(
    solutions: tuple[VrpSolution, ...],
) -> tuple[VrpSolution, ...]:
    unique: list[VrpSolution] = []
    seen: set[VrpSolution] = set()
    for solution in solutions:
        if solution not in seen:
            seen.add(solution)
            unique.append(solution)
    return tuple(unique)
