"""Relocate package custody chains to repair cargo and tool capacity.

Created: 2026-11-04T12:09:06+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.capacity_profile import (
    capacity_excess_by_stop,
    capacity_states_by_stop,
)
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import objective_position
from multiobjective_vns.multiday_vrp.operators.path_search import rebuild_changed_routes
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteStop,
    TransferOperationStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = ['CapacityAwarePackageChainRelocationOperator']

type _PackageEvent = tuple[int, int, RouteStop]
type _EventGroups = dict[int, list[_PackageEvent]]


class CapacityAwarePackageChainRelocationOperator:
    """Reassign and relocate complete package custody chains.

    A custody leg keeps its pickup/transfer operations on one vehicle. Each
    transfer moves custody to a distinct vehicle, and the final leg keeps the
    delivery with its receiving vehicle. The Operator samples bounded complete
    plans and ranks them by their largest assessed cargo/tool capacity excess.
    """

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
        """Apply up to ``k`` distinct package-chain relocations.

        :param context: Base plan, requested size, feasibility request, and the
            package's shared random source.
        :returns: A complete candidate when at least one chain changed.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        working = require_vrp_base(context)
        moved: set[str] = set()
        modifications = 0
        for _ in range(size):
            targets = tuple(
                package_id
                for package_id in self._target_packages(working, context)
                if package_id not in moved
            )
            if not targets:
                break
            package_id = choose_uniformly(targets, context.random_source)
            moved.add(package_id)
            candidates = self._candidates(working, package_id, context)
            if not candidates:
                continue
            selected = self._choose(candidates, context)
            working = selected[0]
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _target_packages(
        self, solution: VrpSolution, context: CandidateContext
    ) -> tuple[str, ...]:
        groups_by_package = {
            package.package_id: _event_groups(solution, package.package_id)
            for package in self._instance.package_data.packages
        }
        eligible = tuple(
            package_id for package_id, groups in groups_by_package.items() if groups
        )
        if not eligible:
            return ()
        states = capacity_states_by_stop(self._instance, solution)
        maximum_excess = max(
            (excess for excess, _loaded in states.values()), default=0.0
        )
        if maximum_excess > 0.0:
            worst_states = tuple(
                key
                for key, (excess, _loaded) in states.items()
                if excess == maximum_excess
            )
            state = choose_uniformly(worst_states, context.random_source)
            loaded = states[state][1]
            loads = {
                item.package_id: item.load
                for item in self._instance.package_data.packages
            }
            scored = tuple(
                package_id for package_id in eligible if package_id in loaded
            )
            if scored:
                maximum = max(loads[package_id] for package_id in scored)
                return tuple(
                    package_id for package_id in scored if loads[package_id] == maximum
                )
            return ()
        loads = {
            item.package_id: item.load for item in self._instance.package_data.packages
        }
        maximum = max(loads[package_id] for package_id in eligible)
        return tuple(
            package_id for package_id in eligible if loads[package_id] == maximum
        )

    def _candidates(
        self,
        solution: VrpSolution,
        package_id: str,
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...], float], ...]:
        groups = _event_groups(solution, package_id)
        if not groups:
            return ()
        route_by_vehicle_day = {
            (route.vehicle_id, route.day_index): index
            for index, route in enumerate(solution.routes)
        }
        package_compatibility = {
            (item.vehicle_id, item.package_id): item.compatible
            for item in self._instance.package_data.vehicle_package_compatibility
        }
        vehicle_options: dict[int, tuple[str, ...]] = {}
        for group_index, events in groups.items():
            days = {
                solution.routes[route_index].day_index for route_index, _, _ in events
            }
            vehicle_options[group_index] = tuple(
                vehicle_id
                for vehicle_id in sorted(
                    {route.vehicle_id for route in solution.routes}
                )
                if all((vehicle_id, day) in route_by_vehicle_day for day in days)
                and package_compatibility.get((vehicle_id, package_id), True)
            )
            if not vehicle_options[group_index]:
                return ()

        group_indices = tuple(sorted(groups))
        current_assignment = tuple(
            solution.routes[groups[group_index][0][0]].vehicle_id
            for group_index in group_indices
        )
        assignments: list[tuple[str, ...]] = []
        seen: set[tuple[str, ...]] = set()
        for position, group_index in enumerate(group_indices):
            for vehicle_id in vehicle_options[group_index]:
                if vehicle_id == current_assignment[position]:
                    continue
                candidate_assignment = list(current_assignment)
                candidate_assignment[position] = vehicle_id
                if any(
                    left == right
                    for left, right in zip(
                        candidate_assignment[:-1],
                        candidate_assignment[1:],
                        strict=True,
                    )
                ):
                    continue
                key = tuple(candidate_assignment)
                if key not in seen:
                    seen.add(key)
                    assignments.append(key)
        if current_assignment not in seen:
            assignments.append(current_assignment)
        assignment_choices = randomized_sample(
            assignments, self._candidate_limit * 8, context.random_source
        )

        generated: list[tuple[VrpSolution, tuple[object, ...], float]] = []
        for assignment_key in assignment_choices:
            moved = self._insert_chain(
                solution,
                package_id,
                groups,
                group_indices,
                assignment_key,
                route_by_vehicle_day,
                context,
            )
            for candidate in moved:
                if candidate == solution:
                    continue
                evaluation = self._assess_plan(
                    self._instance, self._objectives, candidate
                )
                if not isinstance(evaluation, ApplicationEvaluation):
                    raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
                if not _usable(evaluation, self._objectives):
                    continue
                if context.ensure_feasible and evaluation.feasible is not True:
                    continue
                generated.append(
                    (
                        candidate,
                        evaluation.objective_values or (),
                        _maximum_capacity_excess(self._instance, candidate),
                    )
                )
                if len(generated) >= self._candidate_limit:
                    return tuple(generated)
        return tuple(generated)

    def _insert_chain(
        self,
        solution: VrpSolution,
        package_id: str,
        groups: _EventGroups,
        group_indices: tuple[int, ...],
        assignment: tuple[str, ...],
        route_by_vehicle_day: Mapping[tuple[str, int], int],
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        chain_stops = {
            (route_index, id(stop))
            for events in groups.values()
            for route_index, _rank, stop in events
        }
        remaining_by_route: dict[int, tuple[RouteStop, ...]] = {}
        additions: dict[int, list[tuple[int, RouteStop]]] = defaultdict(list)
        for route_index, route in enumerate(solution.routes):
            remaining_by_route[route_index] = tuple(
                stop
                for stop in route.stops
                if (route_index, id(stop)) not in chain_stops
            )
        for group_index, vehicle_id in zip(group_indices, assignment, strict=True):
            for source_route, rank, stop in groups[group_index]:
                day = solution.routes[source_route].day_index
                destination = route_by_vehicle_day[(vehicle_id, day)]
                additions[destination].append((rank, stop))

        changed_route_indices = {route_index for route_index, _stop_id in chain_stops}
        changed_route_indices.update(additions)
        insertion_order = tuple(sorted(changed_route_indices))
        insertions: dict[int, int] = {}
        for route_index in insertion_order:
            if route_index not in additions:
                continue
            count = len(remaining_by_route[route_index])
            insertions[route_index] = choose_uniformly(
                tuple(range(count + 1)), context.random_source
            )
        if not insertion_order:
            return ()
        stops_by_route: dict[int, tuple[RouteStop, ...]] = {}
        for route_index in insertion_order:
            stops = list(remaining_by_route[route_index])
            selected = sorted(additions.get(route_index, []), key=lambda item: item[0])
            if selected:
                insertion = insertions[route_index]
                stops[insertion:insertion] = [stop for _rank, stop in selected]
            stops_by_route[route_index] = tuple(stops)
        return rebuild_changed_routes(
            self._instance,
            solution,
            stops_by_route,
            random_source=context.random_source,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_candidate_alternatives=self._candidate_limit,
        )

    def _choose(
        self,
        candidates: tuple[tuple[VrpSolution, tuple[object, ...], float], ...],
        context: CandidateContext,
    ) -> tuple[VrpSolution, tuple[object, ...], float]:
        best_excess = min(item[2] for item in candidates)
        tied = [item for item in candidates if item[2] == best_excess]
        if self._objective_position is not None:
            objective = self._objectives.objectives[self._objective_position]
            best = tied[0]
            objective_ties = [best]
            for candidate in tied[1:]:
                comparison = objective.compare(
                    candidate[1][self._objective_position],
                    best[1][self._objective_position],
                )
                if comparison is ObjectiveComparison.LESS:
                    best = candidate
                    objective_ties = [candidate]
                elif comparison is ObjectiveComparison.EQUAL:
                    objective_ties.append(candidate)
            tied = objective_ties
        return choose_uniformly(tied, context.random_source)


def _event_groups(solution: VrpSolution, package_id: str) -> _EventGroups:
    transfer_count = sum(
        step.package_id == package_id for step in solution.transfer_steps
    )
    groups: _EventGroups = defaultdict(list)
    for route_index, route in enumerate(solution.routes):
        for stop_index, stop in enumerate(route.stops):
            if (
                type(stop) is ActivityStop
                and stop.activity_ref.activity_id == package_id
            ):
                if stop.activity_ref.kind == 'pickup':
                    groups[0].append((route_index, 0, stop))
                elif stop.activity_ref.kind == 'delivery':
                    groups[transfer_count].append(
                        (route_index, 2 * transfer_count + 1, stop)
                    )
            elif (
                type(stop) is TransferOperationStop
                and stop.transfer_step_ref.package_id == package_id
            ):
                step = stop.transfer_step_ref.step_number
                group = step - 1 if stop.role == 'out' else step
                rank = 2 * step - 1 if stop.role == 'out' else 2 * step
                groups[group].append((route_index, rank, stop))
    return dict(groups)


def _maximum_capacity_excess(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> float:
    return max(capacity_excess_by_stop(instance, solution).values(), default=0.0)


def _usable(
    evaluation: ApplicationEvaluation, objectives: ObjectiveDeclaration
) -> bool:
    return (
        evaluation.representation_valid
        and evaluation.feasible is not None
        and evaluation.objective_values is not None
        and type(evaluation.objective_values) is tuple
        and len(evaluation.objective_values) == len(objectives.objectives)
    )


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
