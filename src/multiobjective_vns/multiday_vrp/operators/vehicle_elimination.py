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
from multiobjective_vns.multiday_vrp.operators.path_search import (
    route_node_sequence,
    stop_node_id,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_insertion_search import (
    InsertionStrategy,
    build_insertion_beam,
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
    :param insertion_strategy: Best insertion or regret insertion.
    :param max_candidate_alternatives: Per-unit proposal limit and beam width.
    :param max_paths_per_leg: Maximum sampled paths for a route leg.
    :param max_arc_expansion_steps: Search bound for each sampled path.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_position',
        '_insertion_strategy',
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
        insertion_strategy: InsertionStrategy = 'best',
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
        if insertion_strategy not in {'best', 'regret'}:
            raise ValueError("insertion_strategy must be 'best' or 'regret'")
        self._insertion_strategy = insertion_strategy
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
            assessed = self._repair_candidates(
                working,
                source_vehicle_id,
                selected_groups,
                context,
            )
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
    ) -> tuple[_ScoredPlan, ...]:
        if not groups:
            return ()

        def propose(
            plan: VrpSolution,
            group: tuple[_ServiceUnit, ...],
            proposal_context: CandidateContext,
        ) -> tuple[VrpSolution, ...]:
            return self._relationship_group_placements(
                plan,
                source_vehicle_id,
                group,
                proposal_context,
            )

        def assess(plan: VrpSolution) -> tuple[object, ...] | None:
            evaluation = assess_complete_candidate(
                self._instance,
                self._objectives,
                plan,
                self._assess_plan,
                ensure_feasible=context.ensure_feasible,
            )
            return None if evaluation is None else evaluation.objective_values

        return build_insertion_beam(
            base,
            groups,
            propose,
            assess,
            self._objectives,
            objective_position=self._objective_position,
            strategy=self._insertion_strategy,
            context=context,
            candidate_limit=self._candidate_limit,
        )

    def _relationship_group_placements(
        self,
        base: VrpSolution,
        source_vehicle_id: str,
        group: tuple[_ServiceUnit, ...],
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        keys = {unit.key for unit in group}
        moved_stops = tuple(
            stop
            for route in base.routes
            for stop in route.stops
            if _stop_unit_key(stop) in keys
        )
        if not moved_stops:
            return ()

        remaining_routes = tuple(
            replace(
                route,
                stops=tuple(
                    stop for stop in route.stops if _stop_unit_key(stop) not in keys
                ),
            )
            for route in base.routes
        )
        receiving_plan = replace(base, routes=remaining_routes)
        receiving_plan = _sort_routes(
            replace(
                receiving_plan,
                routes=tuple(
                    route
                    for route in receiving_plan.routes
                    if any(_stop_unit_key(stop) is not None for stop in route.stops)
                ),
            ),
            self._instance,
        )
        if not receiving_plan.routes:
            return ()

        partial_plans: tuple[VrpSolution, ...] = (receiving_plan,)
        for unit in randomized_order(group, context.random_source):
            expanded: list[VrpSolution] = []
            for partial in partial_plans:
                expanded.extend(
                    self._service_unit_placements(
                        partial,
                        source_vehicle_id,
                        unit,
                        context,
                    )
                )
            unique = _unique_plans(expanded)
            if not unique:
                return ()
            ordered = randomized_order(unique, context.random_source)
            partial_plans = ordered[: self._candidate_limit]

        complete: list[VrpSolution] = []
        for plan in partial_plans:
            ordered_plan = _sort_routes(plan, self._instance)
            with_paths = _rebuild_paths(
                self._instance,
                base,
                ordered_plan,
                context,
                max_paths=self._path_limit,
                max_expansions=self._expansion_limit,
            )
            if with_paths is not None and with_paths != base:
                _append_unique_plan(complete, with_paths)
        return tuple(complete)

    def _service_unit_placements(
        self,
        plan: VrpSolution,
        source_vehicle_id: str,
        unit: _ServiceUnit,
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        if unit.key[0] == 'package':
            package_stops = tuple(
                sorted(
                    (replace(item[2], tour_end=False) for item in unit.occurrences),
                    key=_package_operation_order,
                )
            )
            package_states: tuple[tuple[VrpSolution, str | None], ...] = ((plan, None),)
            for stop in package_stops:
                expanded: list[tuple[VrpSolution, str | None]] = []
                for current, carrier in package_states:
                    expanded.extend(
                        self._package_stop_placements(
                            current,
                            source_vehicle_id,
                            unit.key[1],
                            stop,
                            carrier,
                            context,
                        )
                    )
                package_states = randomized_order(
                    _unique_carrier_plans(expanded), context.random_source
                )[: self._candidate_limit]
                if not package_states:
                    return ()
            return tuple(current for current, _carrier in package_states)

        activity_states: tuple[VrpSolution, ...] = (plan,)
        stops = tuple(replace(item[2], tour_end=False) for item in unit.occurrences)
        for stop in stops:
            expanded_plans: list[VrpSolution] = []
            for current in activity_states:
                expanded_plans.extend(
                    self._activity_stop_placements(
                        current,
                        source_vehicle_id,
                        stop,
                        context,
                    )
                )
            activity_states = randomized_order(
                _unique_plans(expanded_plans), context.random_source
            )[: self._candidate_limit]
            if not activity_states:
                return ()
        return activity_states

    def _activity_stop_placements(
        self,
        plan: VrpSolution,
        source_vehicle_id: str,
        stop: RouteStop,
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        choices = tuple(
            (route_index, position)
            for route_index, route in enumerate(plan.routes)
            if route.vehicle_id != source_vehicle_id
            for position in range(len(route.stops) + 1)
        )
        candidates = _unique_plans(
            tuple(
                _insert_stop(plan, route_index, position, stop)
                for route_index, position in choices
            )
        )
        return randomized_order(candidates, context.random_source)[
            : self._candidate_limit
        ]

    def _package_stop_placements(
        self,
        plan: VrpSolution,
        source_vehicle_id: str,
        package_id: str,
        stop: RouteStop,
        current_carrier: str | None,
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, str | None], ...]:
        compatibility = {
            (record.vehicle_id, record.package_id): record.compatible
            for record in self._instance.package_data.vehicle_package_compatibility
        }
        choices: list[tuple[VrpSolution, str | None]] = []
        for route_index, route in enumerate(plan.routes):
            if route.vehicle_id == source_vehicle_id:
                continue
            if not compatibility.get((route.vehicle_id, package_id), True):
                continue
            if not _package_route_allowed(stop, route.vehicle_id, current_carrier):
                continue
            positions = _package_insertion_positions(
                self._instance,
                plan,
                route_index,
                package_id,
                stop,
            )
            for position in positions:
                updated = _insert_stop(plan, route_index, position, stop)
                carrier = _next_carrier(stop, route.vehicle_id, current_carrier)
                choices.append((updated, carrier))
        ordered = randomized_order(
            _unique_carrier_plans(choices), context.random_source
        )
        return ordered[: self._candidate_limit]


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


def _insert_stop(
    plan: VrpSolution,
    route_index: int,
    insertion_index: int,
    stop: RouteStop,
) -> VrpSolution:
    route = plan.routes[route_index]
    updated_routes = list(plan.routes)
    updated_routes[route_index] = replace(
        route,
        stops=(
            *route.stops[:insertion_index],
            stop,
            *route.stops[insertion_index:],
        ),
    )
    return replace(plan, routes=tuple(updated_routes))


def _package_insertion_positions(
    instance: LoadedVrpInstance,
    plan: VrpSolution,
    route_index: int,
    package_id: str,
    stop: RouteStop,
) -> tuple[int, ...]:
    route = plan.routes[route_index]
    package_positions = tuple(
        index
        for index, existing in enumerate(route.stops)
        if _stop_unit_key(existing) == ('package', package_id)
    )
    first_position = max(package_positions) + 1 if package_positions else 0
    if (
        isinstance(stop, ActivityStop)
        and stop.activity_ref.kind == 'pickup'
        and stop_node_id(instance, plan, stop)
        == route_node_sequence(instance, plan, route)[0]
    ):
        return (0,)
    return tuple(range(first_position, len(route.stops) + 1))


def _next_carrier(
    stop: RouteStop,
    vehicle_id: str,
    current_carrier: str | None,
) -> str | None:
    if isinstance(stop, ActivityStop) and stop.activity_ref.kind == 'pickup':
        return vehicle_id
    if isinstance(stop, TransferOperationStop) and stop.role == 'in':
        return vehicle_id
    if isinstance(stop, TransferOperationStop) and stop.role == 'out':
        return vehicle_id
    if isinstance(stop, ActivityStop) and stop.activity_ref.kind == 'delivery':
        return current_carrier
    return current_carrier


def _unique_plans(plans: Sequence[VrpSolution]) -> tuple[VrpSolution, ...]:
    unique: list[VrpSolution] = []
    for plan in plans:
        _append_unique_plan(unique, plan)
    return tuple(unique)


def _append_unique_plan(plans: list[VrpSolution], candidate: VrpSolution) -> None:
    if not any(existing == candidate for existing in plans):
        plans.append(candidate)


def _unique_carrier_plans(
    plans: Sequence[tuple[VrpSolution, str | None]],
) -> tuple[tuple[VrpSolution, str | None], ...]:
    unique: list[tuple[VrpSolution, str | None]] = []
    for candidate, carrier in plans:
        if not any(
            existing == candidate and existing_carrier == carrier
            for existing, existing_carrier in unique
        ):
            unique.append((candidate, carrier))
    return tuple(unique)


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
