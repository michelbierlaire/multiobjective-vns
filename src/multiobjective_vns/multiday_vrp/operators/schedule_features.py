"""Feature-specific driver and depot-assignment Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from functools import cmp_to_key
from itertools import permutations

from multiobjective_vns.identifiers import ObjectiveId
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
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
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
        '_overnight_depot_ids_by_vehicle',
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
        self._overnight_depot_ids_by_vehicle = _overnight_depot_ids_by_vehicle(instance)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return up to ``context.max_candidates`` depot-change alternatives."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        route_domains = tuple(
            (
                index,
                tuple(
                    depot_id
                    for depot_id in self._overnight_depot_ids_by_vehicle.get(
                        route.vehicle_id, ()
                    )
                    if depot_id != route.overnight_depot_id
                ),
            )
            for index, route in enumerate(base.routes)
        )
        route_domains = tuple(item for item in route_domains if item[1])
        if not route_domains:
            return operator_result(None, modifications=0, requested_size=size)

        domain_by_route = dict(route_domains)
        target_changes = min(size, len(route_domains))
        vector_limit = _depot_assignment_vector_count(
            tuple(domain for _, domain in route_domains),
            target_changes,
            self._candidate_limit,
        )
        attempt_limit = vector_limit * 32
        attempts = 0
        complete_trials = 0
        seen_vectors: set[tuple[tuple[int, str], ...]] = set()
        seen_candidates: set[VrpSolution] = set()
        candidates: list[tuple[VrpSolution, tuple[object, ...], int]] = []
        route_indices = tuple(index for index, _ in route_domains)

        while (
            len(seen_vectors) < vector_limit
            and attempts < attempt_limit
            and complete_trials < self._candidate_limit
        ):
            attempts += 1
            selected_indices = randomized_sample(
                route_indices, target_changes, context.random_source
            )
            selected_depots = tuple(
                (
                    route_index,
                    choose_uniformly(
                        domain_by_route[route_index], context.random_source
                    ),
                )
                for route_index in selected_indices
            )
            vector = tuple(sorted(selected_depots))
            if vector in seen_vectors:
                continue
            seen_vectors.add(vector)

            routes = list(base.routes)
            affected: set[int] = set()
            for route_index, depot_id in vector:
                route = base.routes[route_index]
                routes[route_index] = replace(route, overnight_depot_id=depot_id)
                affected.update(
                    index
                    for index, item in enumerate(base.routes)
                    if item.vehicle_id == route.vehicle_id
                    and item.day_index >= route.day_index
                )
            provisional = replace(base, routes=tuple(routes))
            remaining = self._candidate_limit - complete_trials
            rebuilt = _rebuild_vehicle_routes(
                self._instance,
                base,
                provisional,
                affected,
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=remaining,
            )
            complete_trials += len(rebuilt)
            for candidate in rebuilt:
                if candidate in seen_candidates:
                    continue
                seen_candidates.add(candidate)
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
                    raise ValueError('objective vector length must match declaration')
                if context.ensure_feasible and evaluation.feasible is not True:
                    continue
                modifications = sum(
                    item.overnight_depot_id != base.routes[index].overnight_depot_id
                    for index, item in enumerate(candidate.routes)
                )
                if modifications:
                    candidates.append(
                        (candidate, evaluation.objective_values, modifications)
                    )

        if not candidates:
            return operator_result(None, modifications=0, requested_size=size)
        ordered = _order_depot_candidates(
            candidates,
            self._objectives,
            self._objective_position,
            context,
        )
        proposals = tuple(
            CandidateProposal(Candidate(solution), modifications)
            for solution, _values, modifications in ordered[: context.max_candidates]
        )
        return OperatorResult(candidates=proposals)


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
        """Return distinct assignment trials built from successive driver cycles."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        route_count = len(base.routes)
        if size < 2 or route_count < 2:
            return OperatorResult(candidates=())

        target_changes = min(size, route_count)
        base_driver_ids = tuple(route.driver_id for route in base.routes)
        driver_counts: dict[str, int] = {}
        for driver_id in base_driver_ids:
            driver_counts[driver_id] = driver_counts.get(driver_id, 0) + 1
        largest_driver_group = max(driver_counts.values())
        maximum_changes = route_count - max(0, 2 * largest_driver_group - route_count)
        if maximum_changes < target_changes:
            return OperatorResult(candidates=())

        maximum_cycle = min(size, route_count)
        trial_limit = _distinct_driver_trial_limit(
            base_driver_ids, target_changes, maximum=32
        )
        if not trial_limit:
            return OperatorResult(candidates=())
        attempt_limit = 32 * 8
        cycle_attempt_limit = max(32, target_changes * 16)
        seen_trials: set[tuple[str, ...]] = set()
        proposals: list[CandidateProposal] = []
        for _ in range(attempt_limit):
            if (
                len(seen_trials) >= trial_limit
                or len(proposals) >= context.max_candidates
            ):
                break
            working_driver_ids = base_driver_ids
            seen_intermediate = {base_driver_ids}
            changed_count = 0
            for _ in range(cycle_attempt_limit):
                if changed_count >= target_changes:
                    break
                cycle_size = choose_uniformly(
                    tuple(range(2, maximum_cycle + 1)), context.random_source
                )
                route_indices = randomized_sample(
                    tuple(range(route_count)), cycle_size, context.random_source
                )
                next_driver_ids = list(working_driver_ids)
                next_changed_count = changed_count
                for position, route_index in enumerate(route_indices):
                    following = route_indices[(position + 1) % cycle_size]
                    new_driver_id = working_driver_ids[following]
                    was_changed = (
                        working_driver_ids[route_index] != base_driver_ids[route_index]
                    )
                    will_be_changed = new_driver_id != base_driver_ids[route_index]
                    next_changed_count += int(will_be_changed) - int(was_changed)
                    next_driver_ids[route_index] = new_driver_id
                assignment = tuple(next_driver_ids)
                if (
                    next_changed_count <= 0
                    or next_changed_count > target_changes
                    or assignment in seen_intermediate
                ):
                    continue
                working_driver_ids = assignment
                changed_count = next_changed_count
                seen_intermediate.add(assignment)

            if changed_count != target_changes or working_driver_ids in seen_trials:
                continue
            seen_trials.add(working_driver_ids)
            candidate = replace(
                base,
                routes=tuple(
                    route
                    if route.driver_id == driver_id
                    else replace(route, driver_id=driver_id)
                    for route, driver_id in zip(
                        base.routes, working_driver_ids, strict=True
                    )
                ),
            )
            if candidate_is_eligible(
                self._instance,
                self._objectives,
                candidate,
                ensure_feasible=context.ensure_feasible,
                assess_plan=self._assess_plan,
            ):
                proposals.append(CandidateProposal(Candidate(candidate), changed_count))
        return OperatorResult(candidates=tuple(proposals))


def _distinct_driver_trial_limit(
    base_driver_ids: tuple[str, ...], target_changes: int, *, maximum: int
) -> int:
    """Bound trials by the exact small-instance assignment-vector count."""
    if len(base_driver_ids) > 7:
        return maximum
    assignments = set(permutations(base_driver_ids))
    return min(
        maximum,
        sum(
            sum(
                candidate_id != base_id
                for base_id, candidate_id in zip(
                    base_driver_ids, assignment, strict=True
                )
            )
            == target_changes
            for assignment in assignments
        ),
    )


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
        '_overnight_depot_ids_by_vehicle',
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
        self._overnight_depot_ids_by_vehicle = _overnight_depot_ids_by_vehicle(instance)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return candidates that reassign a contiguous active-route block."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        if size < 2:
            return operator_result(None, modifications=0, requested_size=size)
        blocks_by_vehicle: dict[str, tuple[tuple[int, ...], ...]] = {}
        for vehicle_id, blocks in _active_route_blocks_by_vehicle(base, size).items():
            depot_domain = self._overnight_depot_ids_by_vehicle.get(vehicle_id, ())
            if not depot_domain:
                continue
            eligible_blocks = tuple(
                block
                for block in blocks
                if any(
                    depot_id != base.routes[index].overnight_depot_id
                    for index in block
                    for depot_id in depot_domain
                )
            )
            if eligible_blocks:
                blocks_by_vehicle[vehicle_id] = eligible_blocks
        if not blocks_by_vehicle:
            return operator_result(None, modifications=0, requested_size=size)
        vehicle_ids = tuple(sorted(blocks_by_vehicle))
        vehicle_id = choose_uniformly(vehicle_ids, context.random_source)
        selected = choose_uniformly(
            blocks_by_vehicle[vehicle_id], context.random_source
        )
        depot_domain = self._overnight_depot_ids_by_vehicle.get(vehicle_id, ())
        if not depot_domain:
            return operator_result(None, modifications=0, requested_size=size)

        vector_limit = _depot_vector_count(
            len(selected), len(depot_domain), self._candidate_limit
        )
        attempt_limit = vector_limit * 32
        attempts = 0
        seen_vectors: set[tuple[str, ...]] = set()
        seen_candidates: set[VrpSolution] = set()
        candidates: list[tuple[VrpSolution, tuple[object, ...], int]] = []
        affected = {
            index
            for index, route in enumerate(base.routes)
            if route.vehicle_id == vehicle_id
            and route.day_index >= base.routes[selected[0]].day_index
        }

        while len(seen_vectors) < vector_limit and attempts < attempt_limit:
            attempts += 1
            depot_vector = tuple(
                choose_uniformly(depot_domain, context.random_source) for _ in selected
            )
            if depot_vector in seen_vectors:
                continue
            seen_vectors.add(depot_vector)

            routes = list(base.routes)
            for route_index, depot_id in zip(selected, depot_vector, strict=True):
                route = base.routes[route_index]
                routes[route_index] = replace(route, overnight_depot_id=depot_id)
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
                max_candidate_alternatives=1,
            )
            for candidate in rebuilt:
                if candidate in seen_candidates:
                    continue
                seen_candidates.add(candidate)
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
        if not candidates:
            return operator_result(None, modifications=0, requested_size=size)
        ordered = _order_depot_candidates(
            candidates,
            self._objectives,
            self._objective_position,
            context,
        )
        proposals = tuple(
            CandidateProposal(Candidate(solution), modifications)
            for solution, _values, modifications in ordered[: context.max_candidates]
        )
        return OperatorResult(candidates=proposals)


def _active_route_blocks_by_vehicle(
    solution: VrpSolution, maximum_length: int
) -> dict[str, tuple[tuple[int, ...], ...]]:
    by_vehicle: dict[str, list[int]] = {}
    for route_index, route in enumerate(solution.routes):
        by_vehicle.setdefault(route.vehicle_id, []).append(route_index)
    blocks_by_vehicle: dict[str, tuple[tuple[int, ...], ...]] = {}
    for vehicle_id, indices in by_vehicle.items():
        indices.sort(key=lambda index: solution.routes[index].day_index)
        maximum = min(maximum_length, len(indices))
        blocks: list[tuple[int, ...]] = []
        for length in range(2, maximum + 1):
            blocks.extend(
                tuple(indices[start : start + length])
                for start in range(len(indices) - length + 1)
            )
        if blocks:
            blocks_by_vehicle[vehicle_id] = tuple(blocks)
    return blocks_by_vehicle


def _depot_vector_count(route_count: int, domain_size: int, limit: int) -> int:
    """Return the Cartesian depot-vector count, capped at the sample limit."""
    count = 1
    for _ in range(route_count):
        count = min(limit, count * domain_size)
    return count


def _overnight_depot_ids_by_vehicle(
    instance: LoadedVrpInstance,
) -> dict[str, tuple[str, ...]]:
    depots: dict[str, set[str]] = {}
    for record in instance.fleet_data.vehicle_overnight_depots:
        depots.setdefault(record.vehicle_id, set()).add(record.depot_id)
    return {
        vehicle_id: tuple(sorted(depot_ids)) for vehicle_id, depot_ids in depots.items()
    }


def _depot_assignment_vector_count(
    domains: tuple[tuple[str, ...], ...], target_changes: int, limit: int
) -> int:
    """Count fixed-size depot vectors, capped at the sampling limit."""
    counts = [0] * (target_changes + 1)
    counts[0] = 1
    for domain in domains:
        for selected_count in range(target_changes, 0, -1):
            counts[selected_count] = min(
                limit,
                counts[selected_count] + counts[selected_count - 1] * len(domain),
            )
    return counts[target_changes]


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
    dimensions_by_route: dict[int, list[tuple[int, tuple[tuple[str, ...], ...]]]] = {}
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
            dimensions_by_route.setdefault(route_index, []).append((leg_index, paths))
        fixed_legs[route_index] = legs
    if not dimensions:
        return (provisional,) if provisional != original else ()
    candidates: list[VrpSolution] = []
    seen: set[tuple[tuple[tuple[str, ...], ...], ...]] = set()
    for _ in range(max_candidate_alternatives * 4):
        routes = list(provisional.routes)
        for route_index, legs in fixed_legs.items():
            completed = list(legs)
            for leg_index, paths in dimensions_by_route.get(route_index, ()):
                completed[leg_index] = RouteLeg(choose_uniformly(paths, random_source))
            if any(leg is None for leg in completed):
                raise RuntimeError('depot path rebuilding left a route leg unset')
            routes[route_index] = replace(
                routes[route_index],
                legs=tuple(leg for leg in completed if leg is not None),
            )
        candidate = replace(provisional, routes=tuple(routes))
        signature = tuple(
            tuple(leg.arc_ids for leg in route.legs) for route in candidate.routes
        )
        if signature not in seen:
            seen.add(signature)
            candidates.append(candidate)
        if len(candidates) == max_candidate_alternatives:
            break
    return tuple(candidates)


def _order_depot_candidates(
    candidates: list[tuple[VrpSolution, tuple[object, ...], int]],
    objectives: ObjectiveDeclaration,
    objective_index: int | None,
    context: CandidateContext,
) -> tuple[tuple[VrpSolution, tuple[object, ...], int], ...]:
    if objective_index is None:
        return randomized_order(candidates, context.random_source)
    objective = objectives.objectives[objective_index]

    def compare(
        left: tuple[VrpSolution, tuple[object, ...], int],
        right: tuple[VrpSolution, tuple[object, ...], int],
    ) -> int:
        comparison = objective.compare(
            left[1][objective_index], right[1][objective_index]
        )
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    ranked = sorted(candidates, key=cmp_to_key(compare))
    ordered: list[tuple[VrpSolution, tuple[object, ...], int]] = []
    group_start = 0
    while group_start < len(ranked):
        group_end = group_start + 1
        while (
            group_end < len(ranked)
            and compare(ranked[group_start], ranked[group_end]) == 0
        ):
            group_end += 1
        ordered.extend(
            randomized_order(ranked[group_start:group_end], context.random_source)
        )
        group_start = group_end
    return tuple(ordered)


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
