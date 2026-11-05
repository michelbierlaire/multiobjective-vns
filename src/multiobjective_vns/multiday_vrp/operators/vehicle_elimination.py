"""Vehicle-elimination destroy-and-repair Operator.

Created: 2026-11-05T00:59:12+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    candidate_is_eligible,
    choose_uniformly,
    operator_result,
    randomized_order,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    assess_complete_candidate,
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_assignment_helpers import (
    _bounded_limit,
    _choose_scored,
    _rebuild_paths,
    _ScoredPlan,
    _sort_routes,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    DepotOnlyStop,
    RouteStop,
    TransferOperationStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.values import require_integer

__all__ = ['VehicleEliminationDestroyRepairOperator']

type _UnitKey = tuple[str, ...]
type _Occurrence = tuple[int, int, RouteStop]


@dataclass(frozen=True, slots=True)
class _ServiceUnit:
    """One package chain or independent activity with its route occurrences."""

    key: _UnitKey
    occurrences: tuple[_Occurrence, ...]


class VehicleEliminationDestroyRepairOperator:
    """Remove one vehicle's routes and reinsert all their service units.

    :param instance: Validated VRP instance.
    :param objective_declaration: Ordered objectives for complete-plan ranking.
    :param assess_plan: Shared complete-plan assessment callback.
    :param max_neighborhood_size: Largest supported value of ``k``.
    :param objective_id: Optional objective used to rank alternatives.
    :param max_candidate_alternatives: Maximum repair trials per elimination.
    :param max_paths_per_leg: Maximum sampled paths for a route leg.
    :param max_arc_expansion_steps: Search bound for each sampled path.
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
        self._candidate_limit = _bounded_limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _bounded_limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _bounded_limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` vehicle eliminations sequentially.

        :param context: Base plan, requested size, feasibility request, and RNG.
        :returns: Complete plan and number of eliminated vehicles, or ``m=0``.
        :raises TypeError: If the base or an assessment is malformed.
        :raises ValueError: If ``k`` exceeds the registered maximum.
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

        working = base
        modifications = 0
        for _ in range(size):
            units = _service_units(working)
            groups = _relationship_groups(self._instance, units)
            eligible_vehicles = _eligible_source_vehicles(working, groups)
            if not eligible_vehicles:
                break
            source_vehicle_id = choose_uniformly(
                eligible_vehicles, context.random_source
            )
            selected_groups = tuple(
                group
                for group in groups
                if any(
                    working.routes[route_index].vehicle_id == source_vehicle_id
                    for unit in group
                    for route_index, _stop_index, _stop in unit.occurrences
                )
            )
            candidates = self._repair_candidates(
                working,
                source_vehicle_id,
                selected_groups,
                context,
            )
            assessed = self._assess_candidates(candidates, context)
            if not assessed:
                break
            working = _choose_scored(
                assessed,
                self._objectives,
                self._objective_position,
                context,
            )[0]
            modifications += 1

        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _repair_candidates(
        self,
        base: VrpSolution,
        source_vehicle_id: str,
        groups: tuple[tuple[_ServiceUnit, ...], ...],
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        selected_keys = {unit.key for group in groups for unit in group}
        partial_routes = []
        for route in base.routes:
            if route.vehicle_id == source_vehicle_id:
                continue
            kept_stops = tuple(
                stop
                for stop in route.stops
                if _stop_unit_key(stop) not in selected_keys
            )
            partial_routes.append(replace(route, stops=kept_stops))
        partial = _sort_routes(
            replace(base, routes=tuple(partial_routes)), self._instance
        )
        if not partial.routes or not groups:
            return ()

        completed: list[VrpSolution] = []
        for _ in range(self._candidate_limit):
            trial = partial
            complete = True
            for group in randomized_order(groups, context.random_source):
                for unit in randomized_order(group, context.random_source):
                    updated = self._insert_repair_unit(trial, unit, context)
                    if updated is None:
                        complete = False
                        break
                    trial = updated
                if not complete:
                    break
            if not complete:
                continue
            with_paths = _rebuild_paths(
                self._instance,
                base,
                trial,
                context,
                max_paths=self._path_limit,
                max_expansions=self._expansion_limit,
            )
            if with_paths is None or with_paths == base:
                continue
            if with_paths not in completed:
                completed.append(with_paths)
        return tuple(completed)

    def _insert_repair_unit(
        self,
        partial: VrpSolution,
        unit: _ServiceUnit,
        context: CandidateContext,
    ) -> VrpSolution | None:
        if unit.key[0] == 'package':
            return _insert_package_chain(
                self._instance,
                partial,
                unit,
                context,
            )
        stops = tuple(
            _without_tour_boundary(occurrence[2]) for occurrence in unit.occurrences
        )
        if not stops or not partial.routes:
            return None
        route_index = choose_uniformly(
            tuple(range(len(partial.routes))), context.random_source
        )
        route = partial.routes[route_index]
        insertion_index = choose_uniformly(
            tuple(range(len(route.stops) + 1)), context.random_source
        )
        updated_routes = list(partial.routes)
        updated_routes[route_index] = replace(
            route,
            stops=(
                *route.stops[:insertion_index],
                *stops,
                *route.stops[insertion_index:],
            ),
        )
        return replace(partial, routes=tuple(updated_routes))

    def _assess_candidates(
        self,
        candidates: Sequence[VrpSolution],
        context: CandidateContext,
    ) -> tuple[_ScoredPlan, ...]:
        assessed: list[_ScoredPlan] = []
        for candidate in candidates:
            evaluation = assess_complete_candidate(
                self._instance,
                self._objectives,
                candidate,
                self._assess_plan,
                ensure_feasible=context.ensure_feasible,
            )
            if evaluation is not None and evaluation.objective_values is not None:
                assessed.append((candidate, evaluation.objective_values))
        return tuple(assessed)


def _service_units(solution: VrpSolution) -> tuple[_ServiceUnit, ...]:
    occurrences: dict[_UnitKey, list[_Occurrence]] = defaultdict(list)
    for route_index, route in enumerate(solution.routes):
        for stop_index, stop in enumerate(route.stops):
            key = _stop_unit_key(stop)
            if key is not None:
                occurrences[key].append((route_index, stop_index, stop))
    return tuple(
        _ServiceUnit(key, tuple(occurrences[key])) for key in sorted(occurrences)
    )


def _stop_unit_key(stop: RouteStop) -> _UnitKey | None:
    if isinstance(stop, ActivityStop):
        reference = stop.activity_ref
        if reference.kind in {'pickup', 'delivery'}:
            return ('package', reference.activity_id)
        return ('activity', reference.kind, reference.activity_id)
    if isinstance(stop, TransferOperationStop):
        return ('package', stop.transfer_step_ref.package_id)
    if isinstance(stop, DepotOnlyStop):
        return None
    raise TypeError('route stop must be a supported RouteStop variant')


def _relationship_groups(
    instance: LoadedVrpInstance,
    units: tuple[_ServiceUnit, ...],
) -> tuple[tuple[_ServiceUnit, ...], ...]:
    parents = {unit.key: unit.key for unit in units}

    def find(key: _UnitKey) -> _UnitKey:
        parent = parents[key]
        if parent != key:
            parents[key] = find(parent)
        return parents[key]

    def union(left: _UnitKey, right: _UnitKey) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    for precedence in instance.activity_relations.precedence:
        left = _activity_unit_key(
            precedence.before_activity_kind,
            precedence.before_activity_id,
        )
        right = _activity_unit_key(
            precedence.after_activity_kind,
            precedence.after_activity_id,
        )
        if left in parents and right in parents:
            union(left, right)
    for co_location in instance.activity_relations.co_location:
        left = _activity_unit_key(
            co_location.activity_kind_1,
            co_location.activity_id_1,
        )
        right = _activity_unit_key(
            co_location.activity_kind_2,
            co_location.activity_id_2,
        )
        if left in parents and right in parents:
            union(left, right)

    grouped: dict[_UnitKey, list[_ServiceUnit]] = defaultdict(list)
    for unit in units:
        grouped[find(unit.key)].append(unit)
    return tuple(
        tuple(sorted(group, key=lambda item: item.key))
        for _root, group in sorted(grouped.items())
    )


def _activity_unit_key(kind: str, activity_id: str) -> _UnitKey:
    if kind in {'pickup', 'delivery'}:
        return ('package', activity_id)
    return ('activity', kind, activity_id)


def _eligible_source_vehicles(
    solution: VrpSolution,
    groups: tuple[tuple[_ServiceUnit, ...], ...],
) -> tuple[str, ...]:
    active_vehicle_ids = tuple(
        dict.fromkeys(route.vehicle_id for route in solution.routes)
    )
    return tuple(
        vehicle_id
        for vehicle_id in active_vehicle_ids
        if any(
            solution.routes[route_index].vehicle_id == vehicle_id
            for group in groups
            for unit in group
            for route_index, _stop_index, _stop in unit.occurrences
        )
    )


def _without_tour_boundary(stop: RouteStop) -> RouteStop:
    if isinstance(stop, (ActivityStop, TransferOperationStop, DepotOnlyStop)):
        return replace(stop, tour_end=False)
    raise TypeError('route stop must be a supported RouteStop variant')


def _insert_package_chain(
    instance: LoadedVrpInstance,
    partial: VrpSolution,
    unit: _ServiceUnit,
    context: CandidateContext,
) -> VrpSolution | None:
    package_id = unit.key[1]
    package_stops = tuple(
        sorted(
            (_without_tour_boundary(occurrence[2]) for occurrence in unit.occurrences),
            key=_package_operation_order,
        )
    )
    if not package_stops or not partial.routes:
        return None

    compatibility = {
        (record.vehicle_id, record.package_id): record.compatible
        for record in instance.package_data.vehicle_package_compatibility
    }
    current_carrier: str | None = None
    working = partial

    for stop in package_stops:
        eligible_routes = tuple(
            route_index
            for route_index, route in enumerate(working.routes)
            if compatibility.get((route.vehicle_id, package_id), True)
            and _package_route_allowed(stop, route.vehicle_id, current_carrier)
        )
        if not eligible_routes:
            return None
        route_index = choose_uniformly(eligible_routes, context.random_source)
        route = working.routes[route_index]
        previous_package_positions = tuple(
            index
            for index, existing in enumerate(route.stops)
            if _stop_unit_key(existing) == unit.key
        )
        first_position = (
            max(previous_package_positions) + 1 if previous_package_positions else 0
        )
        insertion_index = choose_uniformly(
            tuple(range(first_position, len(route.stops) + 1)),
            context.random_source,
        )
        updated_routes = list(working.routes)
        updated_routes[route_index] = replace(
            route,
            stops=(
                *route.stops[:insertion_index],
                stop,
                *route.stops[insertion_index:],
            ),
        )
        working = replace(working, routes=tuple(updated_routes))

        if isinstance(stop, ActivityStop) and stop.activity_ref.kind == 'pickup':
            current_carrier = route.vehicle_id
        elif isinstance(stop, TransferOperationStop) and stop.role == 'in':
            current_carrier = route.vehicle_id
        elif isinstance(stop, TransferOperationStop) and stop.role == 'out':
            current_carrier = route.vehicle_id
        elif isinstance(stop, ActivityStop) and stop.activity_ref.kind == 'delivery':
            if current_carrier is not None and route.vehicle_id != current_carrier:
                return None

    return working


def _package_route_allowed(
    stop: RouteStop,
    vehicle_id: str,
    current_carrier: str | None,
) -> bool:
    if isinstance(stop, ActivityStop):
        if stop.activity_ref.kind == 'delivery' and current_carrier is not None:
            return vehicle_id == current_carrier
        return True
    if isinstance(stop, TransferOperationStop):
        if stop.role == 'out' and current_carrier is not None:
            return vehicle_id == current_carrier
        if stop.role == 'in' and current_carrier is not None:
            return vehicle_id != current_carrier
        return True
    return False


def _package_operation_order(stop: RouteStop) -> tuple[int, int, int]:
    if isinstance(stop, ActivityStop):
        if stop.activity_ref.kind == 'pickup':
            return (0, 0, 0)
        if stop.activity_ref.kind == 'delivery':
            return (2, 0, 0)
    elif isinstance(stop, TransferOperationStop):
        role_order = 0 if stop.role == 'out' else 1
        return (1, stop.transfer_step_ref.step_number, role_order)
    return (3, 0, 0)
