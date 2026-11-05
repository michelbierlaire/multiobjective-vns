"""Feature-specific driver and depot-assignment Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace

from multiobjective_vns.identifiers import ObjectiveId
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
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    route_node_sequence,
    sample_loopless_paths,
)
from multiobjective_vns.multiday_vrp.solution import (
    RouteLeg,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = [
    'DriverSwapCycleOperator',
    'MultiDayDepotReassignmentOperator',
    'OvernightDepotChangeOperator',
]


class OvernightDepotChangeOperator:
    """Change overnight depots and rebuild affected vehicle-route paths."""

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
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
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
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

    def call(self, context: CandidateContext) -> OperatorResult:
        """Change up to ``k`` route overnight-depot assignments."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        changed_routes: set[int] = set()
        considered: set[int] = set()
        for _ in range(size):
            targets = tuple(
                index
                for index, route in enumerate(working.routes)
                if index not in considered
                and _alternative_depots(
                    self._instance, route.vehicle_id, route.overnight_depot_id
                )
            )
            if not targets:
                break
            route_index = choose_uniformly(targets, context.random_source)
            considered.add(route_index)
            route = working.routes[route_index]
            alternatives = randomized_sample(
                _alternative_depots(
                    self._instance, route.vehicle_id, route.overnight_depot_id
                ),
                self._candidate_limit,
                context.random_source,
            )
            candidates: list[tuple[VrpSolution, tuple[object, ...]]] = []
            for depot_id in alternatives:
                routes = list(working.routes)
                routes[route_index] = replace(route, overnight_depot_id=depot_id)
                provisional = replace(working, routes=tuple(routes))
                affected = {
                    index
                    for index, item in enumerate(provisional.routes)
                    if item.vehicle_id == route.vehicle_id
                    and item.day_index >= route.day_index
                }
                rebuilt = _rebuild_vehicle_routes(
                    self._instance,
                    working,
                    provisional,
                    affected,
                    random_source=context.random_source,
                    max_paths_per_leg=self._path_limit,
                    max_arc_expansion_steps=self._expansion_limit,
                    max_candidate_alternatives=self._candidate_limit,
                )
                for candidate in rebuilt:
                    evaluation = self._assess_plan(
                        self._instance, self._objectives, candidate
                    )
                    if not isinstance(evaluation, ApplicationEvaluation):
                        raise TypeError(
                            'VrpPlanAssessor must return ApplicationEvaluation'
                        )
                    if (
                        not evaluation.representation_valid
                        or evaluation.objective_values is None
                    ):
                        continue
                    if len(evaluation.objective_values) != len(
                        self._objectives.objectives
                    ):
                        raise ValueError(
                            'objective vector length must match declaration'
                        )
                    if context.ensure_feasible and evaluation.feasible is not True:
                        continue
                    candidates.append((candidate, evaluation.objective_values))
            if not candidates:
                continue
            candidate = _select_candidate(
                candidates,
                self._objectives,
                self._objective_position,
                context,
            )
            for index, item in enumerate(candidate.routes):
                if (
                    item.vehicle_id == route.vehicle_id
                    and item.overnight_depot_id
                    != working.routes[index].overnight_depot_id
                ):
                    changed_routes.add(index)
            working = candidate
        modifications = sum(
            working.routes[index].overnight_depot_id
            != base.routes[index].overnight_depot_id
            for index in changed_routes
        )
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )


class DriverSwapCycleOperator:
    """Cycle driver assignments among distinct active vehicle-day routes."""

    __slots__ = ('_instance', '_objectives', '_assess_plan', '_maximum_size')

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        max_neighborhood_size: int,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._maximum_size = require_integer(
            max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Try bounded random cycles containing between two and ``k`` routes."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        route_count = len(base.routes)
        if size < 2 or route_count < 2:
            return operator_result(None, modifications=0, requested_size=size)
        maximum_cycle = min(size, route_count)
        for _ in range(32):
            cycle_size = choose_uniformly(
                tuple(range(2, maximum_cycle + 1)), context.random_source
            )
            route_indices = randomized_sample(
                tuple(range(route_count)), cycle_size, context.random_source
            )
            routes = list(base.routes)
            for position, route_index in enumerate(route_indices):
                following = route_indices[(position + 1) % cycle_size]
                routes[route_index] = replace(
                    routes[route_index], driver_id=base.routes[following].driver_id
                )
            candidate = replace(base, routes=tuple(routes))
            modifications = sum(
                candidate.routes[index].driver_id != base.routes[index].driver_id
                for index in route_indices
            )
            if modifications and candidate_is_eligible(
                self._instance,
                self._objectives,
                candidate,
                ensure_feasible=context.ensure_feasible,
                assess_plan=self._assess_plan,
            ):
                return operator_result(
                    candidate, modifications=modifications, requested_size=size
                )
        return operator_result(None, modifications=0, requested_size=size)


class MultiDayDepotReassignmentOperator:
    """Reassign overnight depots across a contiguous active-route block."""

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
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
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
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

    def call(self, context: CandidateContext) -> OperatorResult:
        """Change a bounded contiguous block of vehicle-day depot choices."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        if size < 2:
            return operator_result(None, modifications=0, requested_size=size)
        blocks = _active_route_blocks(base, size)
        if not blocks:
            return operator_result(None, modifications=0, requested_size=size)
        selected = choose_uniformly(blocks, context.random_source)
        vehicle_id = base.routes[selected[0]].vehicle_id
        affected = {
            index
            for index, route in enumerate(base.routes)
            if route.vehicle_id == vehicle_id
            and route.day_index >= base.routes[selected[0]].day_index
        }
        candidates: list[tuple[VrpSolution, tuple[object, ...], int]] = []
        seen: set[tuple[str, ...]] = set()
        for _ in range(self._candidate_limit * 8):
            routes = list(base.routes)
            for route_index in selected:
                route = base.routes[route_index]
                eligible = tuple(
                    sorted(
                        record.depot_id
                        for record in self._instance.fleet_data.vehicle_overnight_depots
                        if record.vehicle_id == vehicle_id
                    )
                )
                if not eligible:
                    return operator_result(None, modifications=0, requested_size=size)
                depot_id = choose_uniformly(eligible, context.random_source)
                routes[route_index] = replace(route, overnight_depot_id=depot_id)
            signature = tuple(routes[index].overnight_depot_id for index in selected)
            if signature in seen:
                continue
            seen.add(signature)
            modifications = sum(
                routes[index].overnight_depot_id
                != base.routes[index].overnight_depot_id
                for index in selected
            )
            if modifications == 0:
                continue
            provisional = replace(base, routes=tuple(routes))
            rebuilt = _rebuild_vehicle_routes(
                self._instance,
                base,
                provisional,
                affected,
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=self._candidate_limit,
            )
            for candidate in rebuilt:
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
                candidates.append(
                    (candidate, evaluation.objective_values, modifications)
                )
            if len(candidates) >= self._candidate_limit:
                break
        if not candidates:
            return operator_result(None, modifications=0, requested_size=size)
        candidate, _values, modifications = _select_scored_candidate(
            candidates,
            self._objectives,
            self._objective_position,
            context,
        )
        return operator_result(
            candidate, modifications=modifications, requested_size=size
        )


def _active_route_blocks(
    solution: VrpSolution, maximum_length: int
) -> tuple[tuple[int, ...], ...]:
    by_vehicle: dict[str, list[int]] = {}
    for route_index, route in enumerate(solution.routes):
        by_vehicle.setdefault(route.vehicle_id, []).append(route_index)
    blocks: list[tuple[int, ...]] = []
    for indices in by_vehicle.values():
        indices.sort(key=lambda index: solution.routes[index].day_index)
        maximum = min(maximum_length, len(indices))
        for length in range(2, maximum + 1):
            blocks.extend(
                tuple(indices[start : start + length])
                for start in range(len(indices) - length + 1)
            )
    return tuple(blocks)


def _select_scored_candidate(
    candidates: list[tuple[VrpSolution, tuple[object, ...], int]],
    objectives: ObjectiveDeclaration,
    objective_index: int | None,
    context: CandidateContext,
) -> tuple[VrpSolution, tuple[object, ...], int]:
    if objective_index is None:
        return choose_uniformly(candidates, context.random_source)
    objective = objectives.objectives[objective_index]
    best = candidates[0]
    tied = [best]
    for candidate in candidates[1:]:
        comparison = objective.compare(
            candidate[1][objective_index], best[1][objective_index]
        )
        if comparison is ObjectiveComparison.LESS:
            best = candidate
            tied = [candidate]
        elif comparison is ObjectiveComparison.EQUAL:
            tied.append(candidate)
    return choose_uniformly(tied, context.random_source)


def _alternative_depots(
    instance: LoadedVrpInstance, vehicle_id: str, current_depot_id: str
) -> tuple[str, ...]:
    return tuple(
        sorted(
            record.depot_id
            for record in instance.fleet_data.vehicle_overnight_depots
            if record.vehicle_id == vehicle_id and record.depot_id != current_depot_id
        )
    )


def _rebuild_vehicle_routes(
    instance: LoadedVrpInstance,
    original: VrpSolution,
    provisional: VrpSolution,
    route_indices: set[int],
    *,
    random_source: RandomSource,
    max_paths_per_leg: int,
    max_arc_expansion_steps: int,
    max_candidate_alternatives: int,
) -> tuple[VrpSolution, ...]:
    """Rebuild paths whose endpoint pairs change after a depot reassignment."""
    dimensions: list[tuple[int, int, tuple[tuple[str, ...], ...]]] = []
    fixed_legs: dict[int, list[RouteLeg | None]] = {}
    for route_index in sorted(route_indices):
        old_route = original.routes[route_index]
        new_route = provisional.routes[route_index]
        old_nodes = route_node_sequence(instance, original, old_route)
        new_nodes = route_node_sequence(instance, provisional, new_route)
        if len(old_route.legs) != len(old_nodes) - 1:
            raise ValueError('base route legs do not align with route endpoints')
        legs: list[RouteLeg | None] = []
        for leg_index, (old_pair, new_pair) in enumerate(
            zip(
                zip(old_nodes[:-1], old_nodes[1:], strict=True),
                zip(new_nodes[:-1], new_nodes[1:], strict=True),
                strict=True,
            )
        ):
            if old_pair == new_pair:
                legs.append(old_route.legs[leg_index])
                continue
            paths = sample_loopless_paths(
                instance,
                new_pair[0],
                new_pair[1],
                random_source=random_source,
                max_paths=max_paths_per_leg,
                max_arc_expansion_steps=max_arc_expansion_steps,
            )
            if not paths:
                return ()
            legs.append(None)
            dimensions.append((route_index, leg_index, paths))
        fixed_legs[route_index] = legs
    if not dimensions:
        return (provisional,) if provisional != original else ()
    candidates: list[VrpSolution] = []
    seen: set[tuple[tuple[str, ...], ...]] = set()
    for _ in range(max_candidate_alternatives * 4):
        routes = list(provisional.routes)
        for route_index, legs in fixed_legs.items():
            completed = list(legs)
            for dimension_route, leg_index, paths in dimensions:
                if dimension_route == route_index:
                    completed[leg_index] = RouteLeg(
                        choose_uniformly(paths, random_source)
                    )
            if any(leg is None for leg in completed):
                raise RuntimeError('depot path rebuilding left a route leg unset')
            routes[route_index] = replace(
                routes[route_index],
                legs=tuple(leg for leg in completed if leg is not None),
            )
        candidate = replace(provisional, routes=tuple(routes))
        signature = tuple(
            tuple(arc_id for leg in route.legs for arc_id in leg.arc_ids)
            for route in candidate.routes
        )
        if signature not in seen:
            seen.add(signature)
            candidates.append(candidate)
        if len(candidates) == max_candidate_alternatives:
            break
    return tuple(candidates)


def _select_candidate(
    candidates: list[tuple[VrpSolution, tuple[object, ...]]],
    objectives: ObjectiveDeclaration,
    objective_index: int | None,
    context: CandidateContext,
) -> VrpSolution:
    if objective_index is None:
        return choose_uniformly(candidates, context.random_source)[0]
    objective = objectives.objectives[objective_index]
    best_value = candidates[0][1][objective_index]
    tied = [candidates[0]]
    for candidate in candidates[1:]:
        comparison = objective.compare(candidate[1][objective_index], best_value)
        if comparison is ObjectiveComparison.LESS:
            best_value = candidate[1][objective_index]
            tied = [candidate]
        elif comparison is ObjectiveComparison.EQUAL:
            tied.append(candidate)
    return choose_uniformly(tied, context.random_source)[0]


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
