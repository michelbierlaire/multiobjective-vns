"""Package pickup-delivery chain repair Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from typing import Literal

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import objective_position
from multiobjective_vns.multiday_vrp.operators.path_search import rebuild_changed_routes
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

type PackageChainMode = Literal['requests', 'compatibility']
type _Occurrence = tuple[int, int, ActivityStop]

__all__ = ['PackageChainMode', 'PackageChainRelocationOperator']


class PackageChainRelocationOperator:
    """Destroy and rebuild complete served package pickup-delivery chains.

    This first implementation handles transfer-free chains. A chain containing
    transfer operations is left for transfer-specific Operators, which preserve
    the paired custody records and carrier sequence.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_mode',
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
        mode: PackageChainMode,
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        if mode not in {'requests', 'compatibility'}:
            raise ValueError(f'unsupported package chain mode: {mode!r}')
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._mode = mode
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
        """Reassign up to ``k`` transfer-free package chains."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        changed_packages: set[str] = set()
        modifications = 0
        for _ in range(size):
            package_ids = tuple(
                package_id
                for package_id in self._eligible_packages(working)
                if package_id not in changed_packages
            )
            if not package_ids:
                break
            package_id = choose_uniformly(package_ids, context.random_source)
            candidates = self._candidates(working, package_id, context)
            changed_packages.add(package_id)
            if not candidates:
                continue
            working = self._choose(candidates, context)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _eligible_packages(self, solution: VrpSolution) -> tuple[str, ...]:
        packages_with_transfer = {step.package_id for step in solution.transfer_steps}
        served: dict[str, list[_Occurrence]] = {}
        for route_index, route in enumerate(solution.routes):
            for stop_index, stop in enumerate(route.stops):
                if type(stop) is ActivityStop and stop.activity_ref.kind in {
                    'pickup',
                    'delivery',
                }:
                    served.setdefault(stop.activity_ref.activity_id, []).append(
                        (route_index, stop_index, stop)
                    )
        candidates = tuple(
            package_id
            for package_id, occurrences in served.items()
            if occurrences and package_id not in packages_with_transfer
        )
        if self._mode == 'requests' or not candidates:
            return candidates
        compatibility = _compatibility(self._instance)
        current_incompatible = tuple(
            package_id
            for package_id in candidates
            if any(
                not compatibility.get(
                    (solution.routes[route_index].vehicle_id, package_id), True
                )
                for route_index, _stop_index, _stop in served[package_id]
            )
        )
        if current_incompatible:
            return current_incompatible
        compatible_route_counts = {
            package_id: sum(
                compatibility.get((route.vehicle_id, package_id), True)
                for route in solution.routes
            )
            for package_id in candidates
        }
        minimum = min(compatible_route_counts.values())
        return tuple(
            package_id
            for package_id in candidates
            if compatible_route_counts[package_id] == minimum
        )

    def _candidates(
        self,
        solution: VrpSolution,
        package_id: str,
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
        occurrences = _package_occurrences(solution, package_id)
        if not occurrences:
            return ()
        pickup = next(
            (item for item in occurrences if item[2].activity_ref.kind == 'pickup'),
            None,
        )
        delivery = next(
            (item for item in occurrences if item[2].activity_ref.kind == 'delivery'),
            None,
        )
        compatibility = _compatibility(self._instance)
        compatible_routes = tuple(
            index
            for index, route in enumerate(solution.routes)
            if self._mode == 'requests'
            or compatibility.get((route.vehicle_id, package_id), True)
        )
        if not compatible_routes:
            return ()
        stops_by_route: dict[int, list[RouteStop]] = {
            index: [
                stop
                for stop in route.stops
                if not (
                    type(stop) is ActivityStop
                    and stop.activity_ref.activity_id == package_id
                    and stop.activity_ref.kind in {'pickup', 'delivery'}
                )
            ]
            for index, route in enumerate(solution.routes)
        }
        trials: list[dict[int, tuple[RouteStop, ...]]] = []
        seen: set[tuple[tuple[int, int, int], ...]] = set()
        for _ in range(self._candidate_limit * 8):
            updated = {index: list(stops) for index, stops in stops_by_route.items()}
            signature: list[tuple[int, int, int]] = []
            pickup_key: tuple[int, int] | None = None
            if pickup is not None:
                route_index = choose_uniformly(compatible_routes, context.random_source)
                insertion = choose_uniformly(
                    tuple(range(len(updated[route_index]) + 1)), context.random_source
                )
                updated[route_index].insert(insertion, pickup[2])
                pickup_key = (route_index, insertion)
                signature.append((0, route_index, insertion))
            if delivery is not None:
                possible_routes = compatible_routes
                if pickup_key is not None:
                    possible_routes = (
                        tuple(
                            route_index
                            for route_index in compatible_routes
                            if route_index >= pickup_key[0]
                        )
                        or compatible_routes
                    )
                route_index = choose_uniformly(possible_routes, context.random_source)
                minimum_position = (
                    pickup_key[1] + 1
                    if pickup_key is not None and route_index == pickup_key[0]
                    else 0
                )
                insertion = choose_uniformly(
                    tuple(range(minimum_position, len(updated[route_index]) + 1)),
                    context.random_source,
                )
                updated[route_index].insert(insertion, delivery[2])
                signature.append((1, route_index, insertion))
            signature_tuple = tuple(signature)
            if signature_tuple in seen:
                continue
            seen.add(signature_tuple)
            changed = {
                index: tuple(stops)
                for index, stops in updated.items()
                if tuple(stops) != solution.routes[index].stops
            }
            if changed:
                trials.append(changed)
            if len(trials) >= self._candidate_limit:
                break
        candidates: list[tuple[VrpSolution, tuple[object, ...]]] = []
        for replacement_stops in trials:
            rebuilt = rebuild_changed_routes(
                self._instance,
                solution,
                replacement_stops,
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
                candidates.append((candidate, evaluation.objective_values))
        return tuple(candidates)

    def _choose(
        self,
        candidates: tuple[tuple[VrpSolution, tuple[object, ...]], ...],
        context: CandidateContext,
    ) -> VrpSolution:
        if self._objective_position is None:
            return choose_uniformly(candidates, context.random_source)[0]
        objective = self._objectives.objectives[self._objective_position]
        best = candidates[0]
        tied = [best]
        for candidate in candidates[1:]:
            comparison = objective.compare(
                candidate[1][self._objective_position],
                best[1][self._objective_position],
            )
            if comparison is ObjectiveComparison.LESS:
                best = candidate
                tied = [candidate]
            elif comparison is ObjectiveComparison.EQUAL:
                tied.append(candidate)
        return choose_uniformly(tied, context.random_source)[0]


def _package_occurrences(
    solution: VrpSolution, package_id: str
) -> tuple[_Occurrence, ...]:
    return tuple(
        (route_index, stop_index, stop)
        for route_index, route in enumerate(solution.routes)
        for stop_index, stop in enumerate(route.stops)
        if type(stop) is ActivityStop
        and stop.activity_ref.activity_id == package_id
        and stop.activity_ref.kind in {'pickup', 'delivery'}
    )


def _compatibility(instance: LoadedVrpInstance) -> dict[tuple[str, str], bool]:
    return {
        (item.vehicle_id, item.package_id): item.compatible
        for item in instance.package_data.vehicle_package_compatibility
    }


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
