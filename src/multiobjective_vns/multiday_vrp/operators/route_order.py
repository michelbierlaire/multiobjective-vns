"""Generic stop-order Operators for multi-day VRP plans.

Created: 2026-11-03T20:25:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from itertools import combinations
from math import isfinite
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
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
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


def _sample_two_opt_targets(
    solution: VrpSolution, count: int, random_source: RandomSource
) -> tuple[RouteTarget, ...]:
    """Sample 2-opt targets without materializing the quadratic target list."""
    available = sum(
        len(route.stops) * (len(route.stops) - 1) // 2 for route in solution.routes
    )
    sample_count = require_integer(count, name='count', minimum=0)
    selected_count = min(sample_count, available)
    swaps: dict[int, int] = {}
    selected: list[RouteTarget] = []
    for position in range(selected_count):
        remaining = available - position
        if remaining == 1:
            selected_index = swaps.get(position, position)
        else:
            draw = random_source.random()
            if type(draw) is not float:
                raise TypeError('RandomSource.random() must return a Python float')
            if not isfinite(draw) or not 0.0 <= draw < 1.0:
                raise ValueError('RandomSource.random() must return a float in [0, 1)')
            offset = min(int(draw * remaining), remaining - 1)
            selected_position = position + offset
            selected_index = swaps.get(selected_position, selected_position)
            swaps[selected_position] = swaps.get(position, position)
            swaps[position] = selected_index
        selected.append(_two_opt_target_at(solution, selected_index))
    return tuple(selected)


def _two_opt_target_at(solution: VrpSolution, target_index: int) -> RouteTarget:
    """Return the target at an index in `_two_opt_targets` order."""
    index = require_integer(target_index, name='target_index', minimum=0)
    for route_index, route in enumerate(solution.routes):
        stop_count = len(route.stops)
        route_target_count = stop_count * (stop_count - 1) // 2
        if index >= route_target_count:
            index -= route_target_count
            continue

        lower = 0
        upper = stop_count - 1
        while lower < upper:
            middle = (lower + upper + 1) // 2
            targets_before_start = middle * (2 * stop_count - middle - 1) // 2
            if targets_before_start <= index:
                lower = middle
            else:
                upper = middle - 1
        start = lower
        targets_before_start = start * (2 * stop_count - start - 1) // 2
        end = start + 1 + index - targets_before_start
        return route_index, start, end
    raise ValueError('target_index is outside the 2-opt target range')


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


def _relocate_alternative_targets(
    solution: VrpSolution, target: RouteTarget
) -> tuple[RouteTarget, ...]:
    """Return the first insertion gap for every distinct relocation plan."""
    source_index, stop_index = target
    source_route = solution.routes[source_index]
    stop = source_route.stops[stop_index]
    reduced_source = (
        *source_route.stops[:stop_index],
        *source_route.stops[stop_index + 1 :],
    )
    insertion_targets: list[RouteTarget] = []
    for destination_index, destination_route in enumerate(solution.routes):
        destination_stops = (
            reduced_source
            if destination_index == source_index
            else destination_route.stops
        )
        for insertion_index in range(len(destination_stops) + 1):
            if insertion_index and destination_stops[insertion_index - 1] == stop:
                continue
            insertion_targets.append((destination_index, insertion_index))
    return tuple(insertion_targets)


def _build_relocate_candidate(
    solution: VrpSolution,
    target: RouteTarget,
    insertion_target: RouteTarget,
) -> VrpSolution:
    """Build one relocation plan from its source and insertion positions."""
    source_index, stop_index = target
    destination_index, insertion_index = insertion_target
    source_route = solution.routes[source_index]
    stop = source_route.stops[stop_index]
    reduced_source = (
        *source_route.stops[:stop_index],
        *source_route.stops[stop_index + 1 :],
    )
    if destination_index == source_index:
        new_stops = (
            *reduced_source[:insertion_index],
            stop,
            *reduced_source[insertion_index:],
        )
        return _replace_stops(solution, {source_index: new_stops})
    destination_stops = solution.routes[destination_index].stops
    new_destination = (
        *destination_stops[:insertion_index],
        stop,
        *destination_stops[insertion_index:],
    )
    return _replace_stops(
        solution,
        {
            source_index: reduced_source,
            destination_index: new_destination,
        },
    )


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


def _or_opt_alternative_targets(
    solution: VrpSolution, target: RouteTarget, size: int
) -> tuple[RouteTarget, ...]:
    """Return first insertion gaps for distinct Or-opt plan alternatives."""
    source_index, start = target
    source_route = solution.routes[source_index]
    block = source_route.stops[start : start + size]
    reduced_source = (*source_route.stops[:start], *source_route.stops[start + size :])
    insertion_targets: list[RouteTarget] = []
    for destination_index, destination_route in enumerate(solution.routes):
        if source_route.day_index != destination_route.day_index:
            continue
        destination_base = (
            reduced_source
            if destination_index == source_index
            else destination_route.stops
        )
        seen_sequences: set[tuple[RouteStop, ...]] = set()
        for insertion_index in range(len(destination_base) + 1):
            candidate_stops = (
                *destination_base[:insertion_index],
                *block,
                *destination_base[insertion_index:],
            )
            if candidate_stops in seen_sequences:
                continue
            seen_sequences.add(candidate_stops)
            insertion_targets.append((destination_index, insertion_index))
    return tuple(insertion_targets)


def _build_or_opt_candidate(
    solution: VrpSolution,
    target: RouteTarget,
    size: int,
    insertion_target: RouteTarget,
) -> VrpSolution:
    """Build one Or-opt plan from its source block and insertion gap."""
    source_index, start = target
    destination_index, insertion_index = insertion_target
    source_route = solution.routes[source_index]
    block = source_route.stops[start : start + size]
    reduced_source = (*source_route.stops[:start], *source_route.stops[start + size :])
    destination_base = (
        reduced_source
        if destination_index == source_index
        else solution.routes[destination_index].stops
    )
    destination_stops = (
        *destination_base[:insertion_index],
        *block,
        *destination_base[insertion_index:],
    )
    replacements = {destination_index: destination_stops}
    if destination_index != source_index:
        replacements[source_index] = reduced_source
    return _replace_stops(solution, replacements)


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


def _exchange_target_count(solution: VrpSolution) -> int:
    """Count same-day stop pairs without materializing the pair list."""
    counts_by_day: dict[int, int] = {}
    for route in solution.routes:
        counts_by_day[route.day_index] = counts_by_day.get(route.day_index, 0) + len(
            route.stops
        )
    return sum(count * (count - 1) // 2 for count in counts_by_day.values())


def _exchange_target_at(solution: VrpSolution, target_index: int) -> RouteTarget:
    """Return a same-day stop pair at its filtered combinations-order index."""
    index = require_integer(target_index, name='target_index', minimum=0)
    positions_by_day: dict[int, list[RouteTarget]] = {}
    ordered_positions: list[tuple[RouteTarget, int, int]] = []
    for route_index, route in enumerate(solution.routes):
        day_positions = positions_by_day.setdefault(route.day_index, [])
        for stop_index in range(len(route.stops)):
            target = (route_index, stop_index)
            day_rank = len(day_positions)
            day_positions.append(target)
            ordered_positions.append((target, route.day_index, day_rank))

    for left, day_index, day_rank in ordered_positions:
        day_positions = positions_by_day[day_index]
        following_count = len(day_positions) - day_rank - 1
        if index < following_count:
            right = day_positions[day_rank + 1 + index]
            return (*left, *right)
        index -= following_count
    raise ValueError('target_index is outside the Exchange target range')


def _sample_exchange_target(
    solution: VrpSolution, random_source: RandomSource
) -> RouteTarget | None:
    """Select one same-day stop pair with the shared random stream."""
    target_count = _exchange_target_count(solution)
    if target_count == 0:
        return None
    if target_count == 1:
        return _exchange_target_at(solution, 0)
    draw = random_source.random()
    if type(draw) is not float:
        raise TypeError('RandomSource.random() must return a Python float')
    if not isfinite(draw) or not 0.0 <= draw < 1.0:
        raise ValueError('RandomSource.random() must return a float in [0, 1)')
    target_index = min(int(draw * target_count), target_count - 1)
    return _exchange_target_at(solution, target_index)


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


def _two_opt_star_target_count(solution: VrpSolution) -> int:
    """Count same-day route-cut pairs without materializing every target."""
    return sum(
        (len(solution.routes[left].stops) + 1) * (len(solution.routes[right].stops) + 1)
        for left, right in _same_day_route_pairs(solution)
    )


def _two_opt_star_target_at(solution: VrpSolution, target_index: int) -> RouteTarget:
    """Return a 2-opt* target at its materialized pair-and-cut order index."""
    index = require_integer(target_index, name='target_index', minimum=0)
    for left, right in _same_day_route_pairs(solution):
        left_cut_count = len(solution.routes[left].stops) + 1
        right_cut_count = len(solution.routes[right].stops) + 1
        pair_target_count = left_cut_count * right_cut_count
        if index >= pair_target_count:
            index -= pair_target_count
            continue
        left_cut, right_cut = divmod(index, right_cut_count)
        return left, right, left_cut, right_cut
    raise ValueError('target_index is outside the 2-opt* target range')


def _sample_two_opt_star_target(
    solution: VrpSolution, random_source: RandomSource
) -> RouteTarget | None:
    """Select one same-day route-cut pair with the shared random stream."""
    target_count = _two_opt_star_target_count(solution)
    if target_count == 0:
        return None
    if target_count == 1:
        return _two_opt_star_target_at(solution, 0)
    draw = random_source.random()
    if type(draw) is not float:
        raise TypeError('RandomSource.random() must return a Python float')
    if not isfinite(draw) or not 0.0 <= draw < 1.0:
        raise ValueError('RandomSource.random() must return a float in [0, 1)')
    target_index = min(int(draw * target_count), target_count - 1)
    return _two_opt_star_target_at(solution, target_index)


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
                left_index: (*left[:left_cut], *right[right_cut:]),
                right_index: (*right[:right_cut], *left[left_cut:]),
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


def _swap_star_target_count(solution: VrpSolution) -> int:
    """Count same-day cross-route stop pairs without materializing them."""
    return sum(
        len(solution.routes[left].stops) * len(solution.routes[right].stops)
        for left, right in _same_day_route_pairs(solution)
    )


def _swap_star_target_at(solution: VrpSolution, target_index: int) -> RouteTarget:
    """Return a SWAP* stop pair at its materialized target-order index."""
    index = require_integer(target_index, name='target_index', minimum=0)
    for left, right in _same_day_route_pairs(solution):
        right_stop_count = len(solution.routes[right].stops)
        pair_target_count = len(solution.routes[left].stops) * right_stop_count
        if index >= pair_target_count:
            index -= pair_target_count
            continue
        left_stop, right_stop = divmod(index, right_stop_count)
        return left, left_stop, right, right_stop
    raise ValueError('target_index is outside the SWAP* target range')


def _sample_index_positions(
    population_size: int, count: int, random_source: RandomSource
) -> tuple[int, ...]:
    """Sample integer positions without allocating the full index range."""
    available = require_integer(population_size, name='population_size', minimum=0)
    sample_count = require_integer(count, name='count', minimum=0)
    selected_count = min(sample_count, available)
    swaps: dict[int, int] = {}
    selected: list[int] = []
    for position in range(selected_count):
        remaining = available - position
        if remaining == 1:
            selected_index = swaps.get(position, position)
        else:
            draw = random_source.random()
            if type(draw) is not float:
                raise TypeError('RandomSource.random() must return a Python float')
            if not isfinite(draw) or not 0.0 <= draw < 1.0:
                raise ValueError('RandomSource.random() must return a float in [0, 1)')
            selected_position = position + min(int(draw * remaining), remaining - 1)
            selected_index = swaps.get(selected_position, selected_position)
            swaps[selected_position] = swaps.get(position, position)
            swaps[position] = selected_index
        selected.append(selected_index)
    return tuple(selected)


def _sample_swap_star_target(
    solution: VrpSolution, random_source: RandomSource
) -> RouteTarget | None:
    """Select one same-day cross-route stop pair with the shared RNG."""
    target_count = _swap_star_target_count(solution)
    if target_count == 0:
        return None
    target_index = _sample_index_positions(target_count, 1, random_source)[0]
    return _swap_star_target_at(solution, target_index)


def _single_stop_insertion_indices(
    stops: tuple[RouteStop, ...],
    inserted_stop: RouteStop,
    excluded_index: int,
) -> tuple[int, ...]:
    """Return first allowed gaps for distinct single-stop insertion results."""
    insertion_indices: list[int] = []
    index = 0
    while index <= len(stops):
        if index < len(stops) and stops[index] == inserted_stop:
            run_end = index + 1
            while run_end < len(stops) and stops[run_end] == inserted_stop:
                run_end += 1
            first_allowed = index + 1 if index == excluded_index else index
            if first_allowed <= run_end:
                insertion_indices.append(first_allowed)
            index = run_end + 1
            continue
        if index != excluded_index:
            insertion_indices.append(index)
        index += 1
    return tuple(insertion_indices)


def _restoring_insertion_option(
    original_stops: tuple[RouteStop, ...],
    removed_index: int,
    inserted_stop: RouteStop,
    insertion_indices: tuple[int, ...],
) -> int | None:
    """Return the insertion-option index that restores the original route."""
    if original_stops[removed_index] != inserted_stop:
        return None
    run_start = removed_index
    while run_start and original_stops[run_start - 1] == inserted_stop:
        run_start -= 1
    run_end = removed_index + 1
    while run_end < len(original_stops) and original_stops[run_end] == inserted_stop:
        run_end += 1
    return next(
        (
            option_index
            for option_index, insertion_index in enumerate(insertion_indices)
            if run_start <= insertion_index < run_end
        ),
        None,
    )


@dataclass(frozen=True, slots=True)
class _SwapStarInsertionPool:
    left_indices: tuple[int, ...]
    right_indices: tuple[int, ...]
    excluded_pair_index: int | None

    @property
    def alternative_count(self) -> int:
        count = len(self.left_indices) * len(self.right_indices)
        return count - int(self.excluded_pair_index is not None)

    def target_at(self, target_index: int) -> tuple[int, int]:
        index = require_integer(target_index, name='target_index', minimum=0)
        if index >= self.alternative_count:
            raise ValueError('target_index is outside the SWAP* insertion range')
        if self.excluded_pair_index is not None and index >= self.excluded_pair_index:
            index += 1
        right_count = len(self.right_indices)
        left_option, right_option = divmod(index, right_count)
        return self.left_indices[left_option], self.right_indices[right_option]


def _swap_star_insertion_pool(
    solution: VrpSolution, target: RouteTarget
) -> _SwapStarInsertionPool:
    """Describe unique insertion pairs while excluding the unchanged plan."""
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
    left_indices = _single_stop_insertion_indices(
        left_reduced, right_stop, right_stop_index
    )
    right_indices = _single_stop_insertion_indices(
        right_reduced, left_stop, left_stop_index
    )
    left_restore = _restoring_insertion_option(
        left_route.stops, left_stop_index, right_stop, left_indices
    )
    right_restore = _restoring_insertion_option(
        right_route.stops, right_stop_index, left_stop, right_indices
    )
    excluded_pair_index = (
        left_restore * len(right_indices) + right_restore
        if left_restore is not None and right_restore is not None
        else None
    )
    return _SwapStarInsertionPool(left_indices, right_indices, excluded_pair_index)


def _build_swap_star_candidate(
    solution: VrpSolution,
    target: RouteTarget,
    insertion_target: tuple[int, int],
) -> VrpSolution:
    """Build one SWAP* plan from its source pair and insertion positions."""
    left_index, left_stop_index, right_index, right_stop_index = target
    left_insert, right_insert = insertion_target
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
    new_left = (*left_reduced[:left_insert], right_stop, *left_reduced[left_insert:])
    new_right = (
        *right_reduced[:right_insert],
        left_stop,
        *right_reduced[right_insert:],
    )
    return _replace_stops(
        solution,
        {left_index: new_left, right_index: new_right},
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
        if self._definition in (_EXCHANGE, _TWO_OPT_STAR, _SWAP_STAR) and all(
            current.stops == original.stops
            for current, original in zip(working.routes, base.routes, strict=True)
        ):
            return operator_result(None, modifications=0, requested_size=size)
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _apply_one(
        self,
        working: VrpSolution,
        context: CandidateContext,
        size: int,
        *,
        target: RouteTarget | None = None,
    ) -> VrpSolution | None:
        if target is None:
            if self._definition is _EXCHANGE:
                selected_target = _sample_exchange_target(
                    working, context.random_source
                )
                if selected_target is None:
                    return None
            elif self._definition is _TWO_OPT_STAR:
                selected_target = _sample_two_opt_star_target(
                    working, context.random_source
                )
                if selected_target is None:
                    return None
            elif self._definition is _SWAP_STAR:
                selected_target = _sample_swap_star_target(
                    working, context.random_source
                )
                if selected_target is None:
                    return None
            else:
                targets = self._definition.targets(working, size)
                if not targets:
                    return None
                selected_target = choose_uniformly(targets, context.random_source)
        else:
            selected_target = target
        if self._definition is _RELOCATE:
            insertion_targets = _relocate_alternative_targets(working, selected_target)
            proposals = tuple(
                _build_relocate_candidate(working, selected_target, insertion_target)
                for insertion_target in randomized_sample(
                    insertion_targets,
                    self._candidate_limit,
                    context.random_source,
                )
            )
        elif self._definition is _OR_OPT:
            insertion_targets = _or_opt_alternative_targets(
                working, selected_target, size
            )
            proposals = tuple(
                _build_or_opt_candidate(
                    working, selected_target, size, insertion_target
                )
                for insertion_target in randomized_sample(
                    insertion_targets,
                    self._candidate_limit,
                    context.random_source,
                )
            )
        elif self._definition is _SWAP_STAR:
            insertion_pool = _swap_star_insertion_pool(working, selected_target)
            proposals = tuple(
                _build_swap_star_candidate(
                    working, selected_target, insertion_pool.target_at(index)
                )
                for index in _sample_index_positions(
                    insertion_pool.alternative_count,
                    self._candidate_limit,
                    context.random_source,
                )
            )
        else:
            built_plans = self._definition.build(working, selected_target, size)
            proposal_plans = (
                built_plans
                if self._definition is _TWO_OPT
                else _unique_solutions(built_plans)
            )
            proposals = randomized_sample(
                proposal_plans,
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

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return a randomized, duplicate-free batch of 2-opt candidates.

        Each candidate starts from the supplied base and applies up to ``k``
        sequential reversals. The first reversal target is distinct across
        trials; the shared random source determines their order. Candidate
        generation stops when the requested batch is full or the bounded
        target sample is exhausted.

        :param context: Base solution, size, feasibility request, RNG, and
            maximum candidate count.
        :returns: Up to ``context.max_candidates`` distinct proposals in
            randomized target order.
        :raises TypeError: If the base, context, or an assessment is malformed.
        :raises ValueError: If ``k`` exceeds this Operator's registered limit.
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
            return OperatorResult(candidates=())

        target_count = sum(
            len(route.stops) * (len(route.stops) - 1) // 2 for route in base.routes
        )
        if not target_count:
            return OperatorResult(candidates=())
        sampled_target_count = min(
            target_count, max(context.max_candidates, self._candidate_limit)
        )
        trial_targets = _sample_two_opt_targets(
            base, sampled_target_count, context.random_source
        )

        proposals: list[CandidateProposal] = []
        seen_solutions = [base]
        for first_target in trial_targets:
            working = base
            modifications = 0
            for move_index in range(size):
                target = (
                    first_target
                    if move_index == 0
                    else _sample_two_opt_targets(working, 1, context.random_source)[0]
                )
                candidate = self._apply_one(
                    working,
                    context,
                    size,
                    target=target,
                )
                if candidate is None:
                    break
                working = candidate
                modifications += 1

            if modifications == 0 or working in seen_solutions:
                continue
            seen_solutions.append(working)
            proposals.append(
                CandidateProposal(
                    candidate=Candidate(solution=working), m=modifications
                )
            )
            if len(proposals) == context.max_candidates:
                break

        return OperatorResult(candidates=tuple(proposals))


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
