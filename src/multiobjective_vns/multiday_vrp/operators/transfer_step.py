"""Add, remove, or reassign package transfer steps.

Created: 2026-11-04T12:09:06+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from functools import cmp_to_key
from typing import Literal

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    randomized_order,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import objective_position
from multiobjective_vns.multiday_vrp.operators.transfer_step_support import (
    activity_occurrence as _activity_occurrence,
)
from multiobjective_vns.multiday_vrp.operators.transfer_step_support import (
    delete_identity as _delete_identity,
)
from multiobjective_vns.multiday_vrp.operators.transfer_step_support import (
    limit as _limit,
)
from multiobjective_vns.multiday_vrp.operators.transfer_step_support import (
    rebuild_routes as _rebuild_routes,
)
from multiobjective_vns.multiday_vrp.operators.transfer_step_support import (
    route_index as _route_index,
)
from multiobjective_vns.multiday_vrp.operators.transfer_step_support import (
    transfer_occurrence as _transfer_occurrence,
)
from multiobjective_vns.multiday_vrp.operators.transfer_step_support import (
    transfer_occurrences as _transfer_occurrences,
)
from multiobjective_vns.multiday_vrp.operators.transfer_step_support import (
    usable_evaluation as _usable,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteStop,
    TransferOperationStop,
    TransferStep,
    TransferStepRef,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

type TransferStepMode = Literal['add', 'remove', 'reassign']
type _Target = tuple[str, int]

__all__ = ['TransferStepMode', 'TransferStepOperator']


class TransferStepOperator:
    """Change package custody handoffs while preserving paired operations.

    A registration selects one of ``add``, ``remove``, or ``reassign``.
    Added steps transfer the package from its current custody carrier to a
    distinct compatible vehicle. Removal reconnects the downstream custody
    chain to the removed step's sender. Reassignment changes an eligible
    facility, operation positions, or receiver while preserving the downstream
    custody chain.
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
        '_maximum_transfers_by_package',
        '_compatibility_by_package',
        '_facilities_by_package',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        mode: TransferStepMode,
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        if mode not in {'add', 'remove', 'reassign'}:
            raise ValueError(f'unsupported transfer-step mode: {mode!r}')
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
        self._maximum_transfers_by_package = {
            item.package_id: item.max_transfers
            for item in instance.package_data.packages
        }
        compatibility_by_package: dict[str, dict[str, bool]] = {}
        for record in instance.package_data.vehicle_package_compatibility:
            compatibility_by_package.setdefault(record.package_id, {})[
                record.vehicle_id
            ] = record.compatible
        self._compatibility_by_package = compatibility_by_package
        facilities_by_package: dict[str, list[str]] = {}
        for facility_record in instance.package_data.package_transfer_facilities:
            facilities_by_package.setdefault(facility_record.package_id, []).append(
                facility_record.node_id
            )
        self._facilities_by_package = {
            package_id: tuple(sorted(set(facilities)))
            for package_id, facilities in facilities_by_package.items()
        }

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` sequential transfer-step changes.

        :param context: Search base and shared callback inputs.
        :returns: Up to ``context.max_candidates`` ranked complete plans.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        working = require_vrp_base(context)
        changed_targets: set[_Target] = set()
        blocked_targets: set[_Target] = set()
        modifications = 0
        last_candidates: tuple[tuple[VrpSolution, tuple[object, ...]], ...] = ()
        for _ in range(size):
            targets = self._targets(working)
            targets = tuple(
                target for target in targets if target not in blocked_targets
            )
            if self._mode != 'remove':
                targets = tuple(
                    target for target in targets if target not in changed_targets
                )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            if self._mode != 'remove':
                changed_targets.add(target)
            candidates = self._candidates(working, target, context)
            if not candidates:
                blocked_targets.add(target)
                continue
            blocked_targets.clear()
            last_candidates = _order_candidates(
                candidates,
                self._objectives,
                self._objective_position,
                context,
            )
            working = last_candidates[0][0]
            modifications += 1
        if not modifications:
            return OperatorResult(candidates=())
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), modifications)
                for solution, _values in last_candidates[: context.max_candidates]
            )
        )

    def _targets(self, solution: VrpSolution) -> tuple[_Target, ...]:
        transfers_by_package: dict[str, list[int]] = {}
        for step in solution.transfer_steps:
            transfers_by_package.setdefault(step.package_id, []).append(
                step.step_number
            )
        if self._mode == 'add':
            active_package_ids: set[str] = set()
            for route in solution.routes:
                for stop in route.stops:
                    if (
                        type(stop) is ActivityStop
                        and stop.activity_ref.kind == 'pickup'
                    ):
                        active_package_ids.add(stop.activity_ref.activity_id)
                    elif type(stop) is TransferOperationStop and stop.role == 'in':
                        active_package_ids.add(stop.transfer_step_ref.package_id)
            return tuple(
                (package_id, len(steps))
                for package_id in sorted(active_package_ids)
                for steps in (transfers_by_package.get(package_id, []),)
                if len(steps) < self._maximum_transfers_by_package.get(package_id, 0)
            )
        return tuple(
            (step.package_id, step.step_number) for step in solution.transfer_steps
        )

    def _candidates(
        self,
        solution: VrpSolution,
        target: _Target,
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
        if self._mode == 'add':
            proposals = self._add_candidates(solution, target[0], context)
        elif self._mode == 'remove':
            proposals = self._remove_candidates(solution, target, context)
        else:
            proposals = self._reassign_candidates(solution, target, context)
        result: list[tuple[VrpSolution, tuple[object, ...]]] = []
        seen: set[VrpSolution] = set()
        for candidate in proposals:
            if candidate in seen:
                continue
            if not self._transfer_chain_is_valid(candidate, target[0]):
                continue
            evaluation = self._assess_plan(self._instance, self._objectives, candidate)
            if not isinstance(evaluation, ApplicationEvaluation):
                raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
            if not _usable(evaluation, self._objectives):
                continue
            if context.ensure_feasible and evaluation.feasible is not True:
                continue
            assert evaluation.objective_values is not None
            result.append((candidate, evaluation.objective_values))
            seen.add(candidate)
            if len(result) >= self._candidate_limit:
                break
        return tuple(result)

    def _transfer_chain_is_valid(self, solution: VrpSolution, package_id: str) -> bool:
        """Check package custody invariants independently of global feasibility."""
        package_steps = tuple(
            step for step in solution.transfer_steps if step.package_id == package_id
        )
        if len(package_steps) > self._maximum_transfers_by_package.get(package_id, 0):
            return False
        if tuple(step.step_number for step in package_steps) != tuple(
            range(1, len(package_steps) + 1)
        ):
            return False
        pickup = _activity_occurrence(solution, package_id, 'pickup')
        delivery = _activity_occurrence(solution, package_id, 'delivery')
        if not package_steps:
            if pickup is not None and delivery is not None:
                if (
                    solution.routes[pickup[0]].vehicle_id
                    != solution.routes[delivery[0]].vehicle_id
                ):
                    return False
                if pickup[0] == delivery[0] and pickup[1] >= delivery[1]:
                    return False
            return True

        compatibility = self._compatibility_by_package.get(package_id, {})
        eligible_facilities = set(self._facilities_by_package.get(package_id, ()))
        previous_receiver: str | None = None
        previous_event: tuple[int, int, RouteStop] | None = pickup
        last_incoming: tuple[int, int, TransferOperationStop] | None = None
        for step in package_steps:
            pair = _transfer_occurrences(solution, package_id, step.step_number)
            if pair is None or step.facility_id not in eligible_facilities:
                return False
            outgoing, incoming = pair
            outgoing_vehicle = solution.routes[outgoing[0]].vehicle_id
            incoming_vehicle = solution.routes[incoming[0]].vehicle_id
            if (
                outgoing_vehicle == incoming_vehicle
                or not compatibility.get(outgoing_vehicle, True)
                or not compatibility.get(incoming_vehicle, True)
                or (
                    previous_receiver is not None
                    and outgoing_vehicle != previous_receiver
                )
            ):
                return False
            if (
                previous_event is not None
                and previous_event[0] == outgoing[0]
                and previous_event[1] >= outgoing[1]
            ):
                return False
            previous_receiver = incoming_vehicle
            previous_event = incoming
            last_incoming = incoming
        if delivery is not None:
            if previous_receiver != solution.routes[delivery[0]].vehicle_id:
                return False
            if (
                last_incoming is not None
                and last_incoming[0] == delivery[0]
                and last_incoming[1] >= delivery[1]
            ):
                return False
        return True

    def _add_candidates(
        self, solution: VrpSolution, package_id: str, context: CandidateContext
    ) -> tuple[VrpSolution, ...]:
        existing = sum(
            step.package_id == package_id for step in solution.transfer_steps
        )
        previous_event = (
            _transfer_occurrence(solution, package_id, existing, 'in')
            if existing
            else _activity_occurrence(solution, package_id, 'pickup')
        )
        if previous_event is None:
            return ()
        sender_route_index, previous_index, previous_stop = previous_event
        sender_route = solution.routes[sender_route_index]
        compatibility = self._compatibility_by_package.get(package_id, {})
        if not compatibility.get(sender_route.vehicle_id, True):
            return ()
        receiver_routes = tuple(
            index
            for index, route in enumerate(solution.routes)
            if route.day_index == sender_route.day_index
            and route.vehicle_id != sender_route.vehicle_id
            and compatibility.get(route.vehicle_id, True)
        )
        if not receiver_routes:
            return ()
        delivery = _activity_occurrence(solution, package_id, 'delivery')
        step_number = existing + 1
        facilities = self._facilities_by_package.get(package_id, ())
        if not facilities:
            return ()
        trials: list[VrpSolution] = []
        seen: set[VrpSolution] = set()
        for receiver_index in randomized_sample(
            receiver_routes, self._candidate_limit, context.random_source
        ):
            receiver_vehicle = solution.routes[receiver_index].vehicle_id
            delivery_destination: int | None = None
            if delivery is not None:
                delivery_day = solution.routes[delivery[0]].day_index
                delivery_destination = _route_index(
                    solution, receiver_vehicle, delivery_day
                )
                if delivery_destination is None:
                    continue
            for facility_id in randomized_sample(
                facilities, self._candidate_limit, context.random_source
            ):
                out_stop = TransferOperationStop(
                    TransferStepRef(package_id, step_number),
                    'out',
                    0.0,
                    0.0,
                    0.0,
                    False,
                )
                incoming = TransferOperationStop(
                    TransferStepRef(package_id, step_number),
                    'in',
                    0.0,
                    0.0,
                    0.0,
                    False,
                )
                provisional = replace(
                    solution,
                    transfer_steps=(
                        *solution.transfer_steps,
                        TransferStep(package_id, step_number, facility_id),
                    ),
                )
                route_indices = {sender_route_index, receiver_index}
                if delivery_destination is not None:
                    route_indices.add(delivery_destination)
                initial_stops = [list(route.stops) for route in solution.routes]
                if delivery is not None and delivery_destination is not None:
                    _delete_identity(initial_stops, delivery[0], delivery[2])
                sender_stops = initial_stops[sender_route_index]
                previous_position = next(
                    (
                        index
                        for index, stop in enumerate(sender_stops)
                        if stop is previous_stop
                    ),
                    None,
                )
                if previous_position is None:
                    continue
                sender_stops.insert(previous_position + 1, out_stop)
                insertion_positions = randomized_sample(
                    tuple(range(len(initial_stops[receiver_index]) + 1)),
                    self._candidate_limit,
                    context.random_source,
                )
                for insertion in insertion_positions:
                    stops_by_route = {
                        index: list(initial_stops[index]) for index in route_indices
                    }
                    receiver_stops = stops_by_route[receiver_index]
                    receiver_stops.insert(insertion, incoming)
                    if delivery is not None and delivery_destination is not None:
                        if delivery_destination == receiver_index:
                            receiver_stops.insert(insertion + 1, delivery[2])
                        else:
                            stops_by_route[delivery_destination].append(delivery[2])
                    routes = list(provisional.routes)
                    for route_number, stops in stops_by_route.items():
                        routes[route_number] = replace(
                            routes[route_number], stops=tuple(stops)
                        )
                    rebuilt = self._rebuild(
                        solution,
                        replace(provisional, routes=tuple(routes)),
                        route_indices,
                        context,
                        max_candidate_alternatives=(
                            self._candidate_limit - len(trials)
                        ),
                    )
                    for candidate in rebuilt:
                        if candidate not in seen:
                            seen.add(candidate)
                            trials.append(candidate)
                            if len(trials) >= self._candidate_limit:
                                return tuple(trials)
        return tuple(trials)

    def _remove_candidates(
        self, solution: VrpSolution, target: _Target, context: CandidateContext
    ) -> tuple[VrpSolution, ...]:
        package_id, step_number = target
        pair = _transfer_occurrences(solution, package_id, step_number)
        if pair is None:
            return ()
        out_occurrence, in_occurrence = pair
        out_route_index, _out_index, out_stop = out_occurrence
        in_route_index, _in_index, in_stop = in_occurrence
        sender_vehicle = solution.routes[out_route_index].vehicle_id
        compatibility = self._compatibility_by_package.get(package_id, {})
        if not compatibility.get(sender_vehicle, True):
            return ()
        following = next(
            (
                step
                for step in solution.transfer_steps
                if step.package_id == package_id and step.step_number == step_number + 1
            ),
            None,
        )
        previous_event = (
            _activity_occurrence(solution, package_id, 'pickup')
            if step_number == 1
            else _transfer_occurrence(solution, package_id, step_number - 1, 'in')
        )
        next_out_after_following = (
            None
            if following is None
            else _transfer_occurrence(
                solution, package_id, following.step_number + 1, 'out'
            )
        )
        delivery = _activity_occurrence(solution, package_id, 'delivery')
        move_occurrence: tuple[int, int, RouteStop] | None = None
        destination_route_index: int | None = None
        if following is not None:
            move_occurrence = _transfer_occurrence(
                solution, package_id, following.step_number, 'out'
            )
            if move_occurrence is None:
                return ()
            next_receiver = _transfer_occurrence(
                solution, package_id, following.step_number, 'in'
            )
            if next_receiver is None:
                return ()
            next_receiver_vehicle = solution.routes[next_receiver[0]].vehicle_id
            if next_receiver_vehicle == sender_vehicle or not compatibility.get(
                next_receiver_vehicle, True
            ):
                return ()
            old_index, _position, _stop = move_occurrence
            day = solution.routes[old_index].day_index
            destination_route_index = _route_index(solution, sender_vehicle, day)
        elif delivery is not None:
            old_index = delivery[0]
            day = solution.routes[old_index].day_index
            destination_route_index = _route_index(solution, sender_vehicle, day)
            move_occurrence = delivery
        if move_occurrence is not None and destination_route_index is None:
            return ()

        stop_lists = [list(route.stops) for route in solution.routes]
        _delete_identity(stop_lists, out_route_index, out_stop)
        _delete_identity(stop_lists, in_route_index, in_stop)
        affected = {out_route_index, in_route_index}
        insertion_positions: tuple[int | None, ...] = (None,)
        if move_occurrence is not None and destination_route_index is not None:
            source_index, _source_stop_index, moving_stop = move_occurrence
            current_index = next(
                (
                    index
                    for index, stop in enumerate(stop_lists[source_index])
                    if stop is moving_stop
                ),
                None,
            )
            if current_index is None:
                return ()
            del stop_lists[source_index][current_index]
            affected.update((source_index, destination_route_index))
            destination_stops = stop_lists[destination_route_index]
            lower_bound = 0
            upper_bound = len(destination_stops)
            if (
                previous_event is not None
                and previous_event[0] == destination_route_index
            ):
                previous_position = next(
                    (
                        index
                        for index, stop in enumerate(destination_stops)
                        if stop is previous_event[2]
                    ),
                    None,
                )
                if previous_position is not None:
                    lower_bound = previous_position + 1
            if (
                next_out_after_following is not None
                and next_out_after_following[0] == destination_route_index
            ):
                following_position = next(
                    (
                        index
                        for index, stop in enumerate(destination_stops)
                        if stop is next_out_after_following[2]
                    ),
                    None,
                )
                if following_position is not None:
                    upper_bound = following_position
            if lower_bound > upper_bound:
                return ()
            insertion_positions = randomized_sample(
                tuple(range(lower_bound, upper_bound + 1)),
                self._candidate_limit,
                context.random_source,
            )

        transfer_steps = tuple(
            replace(step, step_number=step.step_number - 1)
            if step.package_id == package_id and step.step_number > step_number
            else step
            for step in solution.transfer_steps
            if not (step.package_id == package_id and step.step_number == step_number)
        )
        trials: list[VrpSolution] = []
        seen: set[VrpSolution] = set()
        for insertion in insertion_positions:
            candidate_stops = [list(stops) for stops in stop_lists]
            if (
                insertion is not None
                and move_occurrence is not None
                and destination_route_index is not None
            ):
                candidate_stops[destination_route_index].insert(
                    insertion, move_occurrence[2]
                )
            candidate_affected = set(affected)
            for route_index, stops in enumerate(candidate_stops):
                updated: list[RouteStop] = []
                references_changed = False
                for stop in stops:
                    if (
                        type(stop) is TransferOperationStop
                        and stop.transfer_step_ref.package_id == package_id
                        and stop.transfer_step_ref.step_number > step_number
                    ):
                        stop = replace(
                            stop,
                            transfer_step_ref=TransferStepRef(
                                package_id, stop.transfer_step_ref.step_number - 1
                            ),
                        )
                        references_changed = True
                    updated.append(stop)
                candidate_stops[route_index] = updated
                if references_changed:
                    candidate_affected.add(route_index)
            routes = list(solution.routes)
            for index in candidate_affected:
                routes[index] = replace(
                    routes[index], stops=tuple(candidate_stops[index])
                )
            provisional = replace(
                solution, routes=tuple(routes), transfer_steps=transfer_steps
            )
            remaining = self._candidate_limit - len(trials)
            if remaining <= 0:
                break
            for candidate in self._rebuild(
                solution,
                provisional,
                candidate_affected,
                context,
                max_candidate_alternatives=remaining,
            ):
                if candidate not in seen:
                    seen.add(candidate)
                    trials.append(candidate)
                    if len(trials) >= self._candidate_limit:
                        return tuple(trials)
        return tuple(trials)

    def _reassign_candidates(
        self, solution: VrpSolution, target: _Target, context: CandidateContext
    ) -> tuple[VrpSolution, ...]:
        package_id, step_number = target
        step_index = next(
            (
                index
                for index, step in enumerate(solution.transfer_steps)
                if step.package_id == package_id and step.step_number == step_number
            ),
            None,
        )
        if step_index is None:
            return ()
        step = solution.transfer_steps[step_index]
        outgoing = _transfer_occurrence(solution, package_id, step_number, 'out')
        if outgoing is None:
            return ()
        sender_vehicle = solution.routes[outgoing[0]].vehicle_id
        if not self._compatibility_by_package.get(package_id, {}).get(
            sender_vehicle, True
        ):
            return ()
        eligible_facilities = tuple(
            facility_id
            for facility_id in self._facilities_by_package.get(package_id, ())
            if facility_id != step.facility_id
        )
        affected = {
            index
            for index, route in enumerate(solution.routes)
            if any(
                type(stop) is TransferOperationStop
                and stop.transfer_step_ref == TransferStepRef(package_id, step_number)
                for stop in route.stops
            )
        }
        trials: list[VrpSolution] = []

        def collect(candidates: tuple[VrpSolution, ...]) -> None:
            for candidate in candidates:
                if candidate not in trials and len(trials) < self._candidate_limit:
                    trials.append(candidate)

        def collect_rebuilt(provisional: VrpSolution, route_indices: set[int]) -> None:
            remaining = self._candidate_limit - len(trials)
            if remaining > 0:
                collect(
                    self._rebuild(
                        solution,
                        provisional,
                        route_indices,
                        context,
                        max_candidate_alternatives=remaining,
                    )
                )

        for facility_id in randomized_sample(
            eligible_facilities, self._candidate_limit, context.random_source
        ):
            updated_steps = list(solution.transfer_steps)
            updated_steps[step_index] = replace(step, facility_id=facility_id)
            provisional = replace(solution, transfer_steps=tuple(updated_steps))
            collect_rebuilt(provisional, affected)

        pair = _transfer_occurrences(solution, package_id, step_number)
        if pair is not None:
            for route_index, stop_index, operation in pair:
                remaining = list(solution.routes[route_index].stops)
                del remaining[stop_index]
                positions = _custody_ordered_positions(
                    solution,
                    package_id,
                    step_number,
                    operation.role,
                    route_index,
                    remaining,
                )
                sampled_positions = randomized_sample(
                    positions,
                    self._candidate_limit,
                    context.random_source,
                )
                for insertion in sampled_positions:
                    stops = list(remaining)
                    stops.insert(insertion, operation)
                    routes = list(solution.routes)
                    routes[route_index] = replace(
                        routes[route_index], stops=tuple(stops)
                    )
                    collect_rebuilt(
                        replace(solution, routes=tuple(routes)),
                        {route_index},
                    )

        if pair is None:
            return _sample_candidates(trials, self._candidate_limit, context)
        outgoing, incoming = pair
        in_route_index = incoming[0]
        in_route = solution.routes[in_route_index]
        sender_vehicle = solution.routes[outgoing[0]].vehicle_id
        next_out = _transfer_occurrence(solution, package_id, step_number + 1, 'out')
        next_in = (
            None
            if next_out is None
            else _transfer_occurrence(solution, package_id, step_number + 1, 'in')
        )
        delivery = (
            None
            if next_out is not None
            else _activity_occurrence(solution, package_id, 'delivery')
        )
        downstream = next_out if next_out is not None else delivery
        forbidden_receivers = {sender_vehicle, in_route.vehicle_id}
        if next_in is not None:
            forbidden_receivers.add(solution.routes[next_in[0]].vehicle_id)
        compatibility = self._compatibility_by_package.get(package_id, {})
        eligible_receivers = tuple(
            vehicle_id
            for vehicle_id in sorted({route.vehicle_id for route in solution.routes})
            if vehicle_id not in forbidden_receivers
            and compatibility.get(vehicle_id, True)
            and _route_index(solution, vehicle_id, in_route.day_index) is not None
            and (
                downstream is None
                or _route_index(
                    solution,
                    vehicle_id,
                    solution.routes[downstream[0]].day_index,
                )
                is not None
            )
        )
        for receiver_vehicle in randomized_sample(
            eligible_receivers, self._candidate_limit, context.random_source
        ):
            incoming_destination = _route_index(
                solution, receiver_vehicle, in_route.day_index
            )
            if incoming_destination is None:
                continue
            downstream_destination = (
                None
                if downstream is None
                else _route_index(
                    solution,
                    receiver_vehicle,
                    solution.routes[downstream[0]].day_index,
                )
            )
            if downstream is not None and downstream_destination is None:
                continue
            stop_lists = [list(route.stops) for route in solution.routes]
            _delete_identity(stop_lists, incoming[0], incoming[2])
            if downstream is not None:
                _delete_identity(stop_lists, downstream[0], downstream[2])
            stop_lists[incoming_destination].append(incoming[2])
            if downstream is not None and downstream_destination is not None:
                stop_lists[downstream_destination].append(downstream[2])
            affected_routes = {
                in_route_index,
                incoming_destination,
            }
            if downstream is not None and downstream_destination is not None:
                affected_routes.update((downstream[0], downstream_destination))
            routes = list(solution.routes)
            for index in affected_routes:
                routes[index] = replace(routes[index], stops=tuple(stop_lists[index]))
            provisional = replace(solution, routes=tuple(routes))
            collect_rebuilt(provisional, affected_routes)
        return _sample_candidates(trials, self._candidate_limit, context)

    def _rebuild(
        self,
        original: VrpSolution,
        provisional: VrpSolution,
        route_indices: set[int],
        context: CandidateContext,
        *,
        max_candidate_alternatives: int | None = None,
    ) -> tuple[VrpSolution, ...]:
        return _rebuild_routes(
            self._instance,
            original,
            provisional,
            route_indices,
            random_source=context.random_source,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_candidate_alternatives=(
                self._candidate_limit
                if max_candidate_alternatives is None
                else max_candidate_alternatives
            ),
        )


def _sample_candidates(
    candidates: list[VrpSolution], limit: int, context: CandidateContext
) -> tuple[VrpSolution, ...]:
    """Sample a bounded set of structurally distinct complete alternatives."""
    unique = tuple(dict.fromkeys(candidates))
    return randomized_sample(unique, limit, context.random_source)


def _custody_ordered_positions(
    solution: VrpSolution,
    package_id: str,
    step_number: int,
    role: Literal['out', 'in'],
    route_index: int,
    remaining_stops: list[RouteStop],
) -> tuple[int, ...]:
    """Return insertion positions that keep same-route custody events ordered."""
    lower_bound = 0
    upper_bound = len(remaining_stops)
    previous = (
        _activity_occurrence(solution, package_id, 'pickup')
        if step_number == 1
        else _transfer_occurrence(solution, package_id, step_number - 1, 'in')
    )
    next_out = _transfer_occurrence(solution, package_id, step_number + 1, 'out')
    delivery = (
        None
        if next_out is not None
        else _activity_occurrence(solution, package_id, 'delivery')
    )
    following = next_out if next_out is not None else delivery
    if role == 'out' and previous is not None and previous[0] == route_index:
        previous_position = next(
            (
                index
                for index, stop in enumerate(remaining_stops)
                if stop is previous[2]
            ),
            None,
        )
        if previous_position is not None:
            lower_bound = previous_position + 1
    if role == 'out' and next_out is not None and next_out[0] == route_index:
        next_position = next(
            (
                index
                for index, stop in enumerate(remaining_stops)
                if stop is next_out[2]
            ),
            None,
        )
        if next_position is not None:
            upper_bound = next_position
    if role == 'in' and following is not None and following[0] == route_index:
        following_position = next(
            (
                index
                for index, stop in enumerate(remaining_stops)
                if stop is following[2]
            ),
            None,
        )
        if following_position is not None:
            upper_bound = following_position
    if lower_bound > upper_bound:
        return ()
    return tuple(range(lower_bound, upper_bound + 1))


def _order_candidates(
    candidates: tuple[tuple[VrpSolution, tuple[object, ...]], ...],
    objectives: ObjectiveDeclaration,
    objective_index: int | None,
    context: CandidateContext,
) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
    if objective_index is None:
        return randomized_order(candidates, context.random_source)
    objective = objectives.objectives[objective_index]

    def compare(
        left: tuple[VrpSolution, tuple[object, ...]],
        right: tuple[VrpSolution, tuple[object, ...]],
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
    ordered: list[tuple[VrpSolution, tuple[object, ...]]] = []
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
