"""Regret-based reinsertion of service units with time windows.

Created: 2026-11-05T00:59:12+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import cmp_to_key
from itertools import product
from math import inf, isfinite
from typing import cast

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    assess_complete_candidate,
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_assignment_helpers import (
    _bounded_limit,
    _rebuild_paths,
    _ScoredPlan,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteStop,
    TransferOperationStop,
    VrpSolution,
)
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.values import require_integer

__all__ = ['TimeWindowRegretBasedReinsertionOperator']

type _UnitKey = tuple[str, ...]
type _RouteStops = tuple[int, tuple[RouteStop, ...]]


@dataclass(frozen=True, slots=True)
class _ServiceUnit:
    """A windowed activity or package chain and its current route positions."""

    key: _UnitKey
    route_stops: tuple[_RouteStops, ...]


@dataclass(frozen=True, slots=True)
class _UnitAlternatives:
    """Objective-ranked complete placements for one service unit."""

    unit_key: _UnitKey
    candidates: tuple[_ScoredPlan, ...]
    objective_position: int

    @property
    def best_value(self) -> float:
        return cast(float, self.candidates[0][1][self.objective_position])

    @property
    def regret(self) -> float:
        if len(self.candidates) == 1:
            return inf
        first = cast(float, self.candidates[0][1][self.objective_position])
        second = cast(float, self.candidates[1][1][self.objective_position])
        if first == inf and second == inf:
            return 0.0
        return second - first


class TimeWindowRegretBasedReinsertionOperator:
    """Relocate windowed services using bounded objective-based regret.

    :param instance: Validated VRP instance.
    :param objective_declaration: Ordered objectives for complete-plan ranking.
    :param assess_plan: Shared complete-plan assessment callback.
    :param objective_id: Objective used to rank alternatives and calculate regret.
    :param max_neighborhood_size: Largest supported value of ``k``.
    :param max_candidate_alternatives: Placements assessed per service unit.
    :param max_paths_per_leg: Maximum sampled paths for a route leg.
    :param max_arc_expansion_steps: Search bound for each sampled path.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_id',
        '_objective_position',
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
        objective_id: ObjectiveId,
        max_neighborhood_size: int,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._objective_id = objective_id
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

    def call(self, context: CandidateContext) -> OperatorResult:
        """Relocate at most ``k`` distinct windowed service units.

        :param context: Base plan, requested size, feasibility request, and RNG.
        :returns: Changed plan and number of units relocated, or ``m=0``.
        :raises TypeError: If the base or a complete assessment is malformed.
        :raises ValueError: If ``k`` exceeds the registered maximum.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        working = require_vrp_base(context)
        moved_units: set[_UnitKey] = set()
        modifications = 0

        for _ in range(size):
            alternatives_list: list[_UnitAlternatives] = []
            for unit in _windowed_units(self._instance, working):
                if unit.key in moved_units:
                    continue
                result = self._unit_alternatives(working, unit, context)
                if result is not None:
                    alternatives_list.append(result)
            alternatives = tuple(alternatives_list)
            if not alternatives:
                break
            selected = self._select_unit(alternatives, context)
            working = selected.candidates[
                self._choose_best_candidate(selected.candidates, context)
            ][0]
            moved_units.add(selected.unit_key)
            modifications += 1

        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _unit_alternatives(
        self,
        base: VrpSolution,
        unit: _ServiceUnit,
        context: CandidateContext,
    ) -> _UnitAlternatives | None:
        candidates = self._placement_candidates(base, unit, context)
        assessed: list[_ScoredPlan] = []
        for candidate in candidates:
            evaluation = assess_complete_candidate(
                self._instance,
                self._objectives,
                candidate,
                self._assess_plan,
                ensure_feasible=context.ensure_feasible,
            )
            if evaluation is None or evaluation.objective_values is None:
                continue
            values = evaluation.objective_values
            if not _has_valid_vrp_values(values, len(self._objectives.objectives)):
                continue
            if not any(previous[0] == candidate for previous in assessed):
                assessed.append((candidate, values))
        if not assessed:
            return None
        objective = self._objectives.objectives[self._objective_position]

        def compare(left: _ScoredPlan, right: _ScoredPlan) -> int:
            result = objective.compare(
                left[1][self._objective_position],
                right[1][self._objective_position],
            )
            if result is ObjectiveComparison.LESS:
                return -1
            if result is ObjectiveComparison.GREATER:
                return 1
            return 0

        assessed.sort(key=cmp_to_key(compare))
        return _UnitAlternatives(
            unit.key,
            tuple(assessed),
            self._objective_position,
        )

    def _placement_candidates(
        self,
        base: VrpSolution,
        unit: _ServiceUnit,
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        base_stops = [list(route.stops) for route in base.routes]
        for route_index, stops in unit.route_stops:
            removed = set(stops)
            base_stops[route_index] = [
                stop for stop in base_stops[route_index] if stop not in removed
            ]

        positions = tuple(
            tuple(range(len(base_stops[route_index]) + 1))
            for route_index, _stops in unit.route_stops
        )
        has_transfers = any(
            isinstance(stop, TransferOperationStop)
            for _route_index, stops in unit.route_stops
            for stop in stops
        )
        if unit.key[0] == 'activity' or not has_transfers:
            service_stops = tuple(
                stop for _route_index, stops in unit.route_stops for stop in stops
            )
            sampled = _sample_service_block_placements(
                base,
                base_stops,
                service_stops,
                self._candidate_limit,
                context,
            )
        else:
            sampled = _sample_distinct_placements(
                base,
                base_stops,
                unit.route_stops,
                positions,
                self._candidate_limit,
                context,
            )
        completed: list[VrpSolution] = []
        for candidate in sampled:
            with_paths = _rebuild_paths(
                self._instance,
                base,
                candidate,
                context,
                max_paths=self._path_limit,
                max_expansions=self._expansion_limit,
            )
            if with_paths is not None and with_paths not in completed:
                completed.append(with_paths)
        return tuple(completed)

    def _select_unit(
        self,
        alternatives: Sequence[_UnitAlternatives],
        context: CandidateContext,
    ) -> _UnitAlternatives:
        singletons = tuple(item for item in alternatives if len(item.candidates) == 1)
        if singletons:
            best_value = singletons[0].best_value
            best = [singletons[0]]
            for item in singletons[1:]:
                comparison = self._compare_values(item.best_value, best_value)
                if comparison is ObjectiveComparison.LESS:
                    best_value = item.best_value
                    best = [item]
                elif comparison is ObjectiveComparison.EQUAL:
                    best.append(item)
            return choose_uniformly(best, context.random_source)

        best_regret = max(item.regret for item in alternatives)
        regret_ties = tuple(item for item in alternatives if item.regret == best_regret)
        best_value = regret_ties[0].best_value
        best = [regret_ties[0]]
        for item in regret_ties[1:]:
            comparison = self._compare_values(item.best_value, best_value)
            if comparison is ObjectiveComparison.LESS:
                best_value = item.best_value
                best = [item]
            elif comparison is ObjectiveComparison.EQUAL:
                best.append(item)
        return choose_uniformly(best, context.random_source)

    def _choose_best_candidate(
        self,
        candidates: Sequence[_ScoredPlan],
        context: CandidateContext,
    ) -> int:
        best_value = candidates[0][1][self._objective_position]
        best_positions = [0]
        for index, candidate in enumerate(candidates[1:], start=1):
            comparison = self._compare_values(
                candidate[1][self._objective_position], best_value
            )
            if comparison is ObjectiveComparison.LESS:
                best_value = candidate[1][self._objective_position]
                best_positions = [index]
            elif comparison is ObjectiveComparison.EQUAL:
                best_positions.append(index)
        return choose_uniformly(best_positions, context.random_source)

    def _compare_values(self, left: object, right: object) -> ObjectiveComparison:
        return self._objectives.objectives[self._objective_position].compare(
            left, right
        )


def _windowed_units(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
) -> tuple[_ServiceUnit, ...]:
    by_key: dict[_UnitKey, dict[int, list[tuple[int, RouteStop]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    windowed_keys: set[_UnitKey] = set()
    for route_index, route in enumerate(solution.routes):
        for stop_index, stop in enumerate(route.stops):
            if isinstance(stop, ActivityStop):
                reference = stop.activity_ref
                if reference.kind in {'pickup', 'delivery'}:
                    unit_key: _UnitKey = ('package', reference.activity_id)
                else:
                    unit_key = ('activity', reference.kind, reference.activity_id)
                by_key[unit_key][route_index].append((stop_index, stop))
                if _activity_has_window(
                    instance,
                    reference.kind,
                    reference.activity_id,
                    route.day_index,
                ):
                    windowed_keys.add(unit_key)
            elif isinstance(stop, TransferOperationStop):
                unit_key = ('package', stop.transfer_step_ref.package_id)
                by_key[unit_key][route_index].append((stop_index, stop))

    units: list[_ServiceUnit] = []
    for key in sorted(windowed_keys):
        route_stops = tuple(
            (
                route_index,
                tuple(stop for _stop_index, stop in sorted(occurrences)),
            )
            for route_index, occurrences in sorted(by_key[key].items())
        )
        if route_stops:
            units.append(_ServiceUnit(key, route_stops))
    return tuple(units)


def _activity_has_window(
    instance: LoadedVrpInstance,
    kind: str,
    activity_id: str,
    day_index: int,
) -> bool:
    if kind in {'pickup', 'delivery'}:
        return any(
            record.package_id == activity_id and record.activity == kind
            for record in instance.package_data.package_time_windows
        )
    if kind == 'intervention':
        return any(
            record.intervention_id == activity_id and record.day_index == day_index
            for record in instance.intervention_data.intervention_time_windows
        )
    return False


def _sample_distinct_placements(
    base: VrpSolution,
    route_stops_without_unit: list[list[RouteStop]],
    route_stops: tuple[_RouteStops, ...],
    insertion_positions: tuple[tuple[int, ...], ...],
    limit: int,
    context: CandidateContext,
) -> tuple[VrpSolution, ...]:
    generated: list[VrpSolution] = []

    def add(selected_positions: tuple[int, ...]) -> None:
        updated_routes = list(base.routes)
        for (route_index, unit_stops), insertion_position in zip(
            route_stops, selected_positions, strict=True
        ):
            current = route_stops_without_unit[route_index]
            updated_routes[route_index] = replace(
                updated_routes[route_index],
                stops=(
                    *current[:insertion_position],
                    *unit_stops,
                    *current[insertion_position:],
                ),
            )
        candidate = replace(base, routes=tuple(updated_routes))
        if candidate != base and candidate not in generated:
            generated.append(candidate)

    for _ in range(max(20, limit * 20)):
        selected_positions = tuple(
            choose_uniformly(positions, context.random_source)
            for positions in insertion_positions
        )
        add(selected_positions)
        if len(generated) >= limit:
            return tuple(generated[:limit])

    for selected_positions in product(*insertion_positions):
        add(tuple(selected_positions))
        if len(generated) >= limit:
            break
    return tuple(generated[:limit])


def _sample_service_block_placements(
    base: VrpSolution,
    route_stops_without_unit: list[list[RouteStop]],
    service_stops: tuple[RouteStop, ...],
    limit: int,
    context: CandidateContext,
) -> tuple[VrpSolution, ...]:
    positions = tuple(
        (route_index, insertion_position)
        for route_index, stops in enumerate(route_stops_without_unit)
        for insertion_position in range(len(stops) + 1)
    )
    sampled_positions = randomized_sample(
        positions,
        min(limit, len(positions)),
        context.random_source,
    )
    generated: list[VrpSolution] = []
    for route_index, insertion_position in sampled_positions:
        updated_routes = [
            replace(route, stops=tuple(route_stops_without_unit[index]))
            for index, route in enumerate(base.routes)
        ]
        stops = route_stops_without_unit[route_index]
        updated_routes[route_index] = replace(
            updated_routes[route_index],
            stops=(
                *stops[:insertion_position],
                *service_stops,
                *stops[insertion_position:],
            ),
        )
        candidate = replace(base, routes=tuple(updated_routes))
        if candidate != base and candidate not in generated:
            generated.append(candidate)
    return tuple(generated)


def _has_valid_vrp_values(values: tuple[object, ...], expected_count: int) -> bool:
    return len(values) == expected_count and all(
        type(value) is float and (isfinite(value) or value == inf) for value in values
    )
