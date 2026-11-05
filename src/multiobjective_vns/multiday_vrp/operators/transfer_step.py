"""Add, remove, or reassign package transfer steps.

Created: 2026-11-04T12:09:06+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

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
from multiobjective_vns.multiday_vrp.operators.feature_common import objective_position
from multiobjective_vns.multiday_vrp.operators.transfer_step_support import (
    activity_occurrence as _activity_occurrence,
)
from multiobjective_vns.multiday_vrp.operators.transfer_step_support import (
    compatibility as _compatibility,
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
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

type TransferStepMode = Literal['add', 'remove', 'reassign']
type _Target = tuple[str, int]

__all__ = ['TransferStepMode', 'TransferStepOperator']


class TransferStepOperator:
    """Change package custody handoffs while preserving paired operations.

    A registration selects one of ``add``, ``remove``, or ``reassign``.
    Added steps transfer the package from its current delivery carrier to a
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

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` distinct transfer-step changes.

        :param context: Search base and shared callback inputs.
        :returns: A complete candidate when a transfer change succeeds.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        working = require_vrp_base(context)
        changed_packages: set[str] = set()
        modifications = 0
        for _ in range(size):
            targets = tuple(
                target
                for target in self._targets(working)
                if target[0] not in changed_packages
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            changed_packages.add(target[0])
            candidates = self._candidates(working, target, context)
            if not candidates:
                continue
            working = self._choose(candidates, context)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _targets(self, solution: VrpSolution) -> tuple[_Target, ...]:
        transfers_by_package: dict[str, list[int]] = {}
        for step in solution.transfer_steps:
            transfers_by_package.setdefault(step.package_id, []).append(
                step.step_number
            )
        if self._mode == 'add':
            pickup_ids = {
                stop.activity_ref.activity_id
                for route in solution.routes
                for stop in route.stops
                if type(stop) is ActivityStop and stop.activity_ref.kind == 'pickup'
            }
            delivery_ids = {
                stop.activity_ref.activity_id
                for route in solution.routes
                for stop in route.stops
                if type(stop) is ActivityStop and stop.activity_ref.kind == 'delivery'
            }
            maximum = {
                item.package_id: item.max_transfers
                for item in self._instance.package_data.packages
            }
            return tuple(
                (package_id, len(steps))
                for package_id in sorted(pickup_ids & delivery_ids)
                for steps in (transfers_by_package.get(package_id, []),)
                if len(steps) < maximum.get(package_id, 0)
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
        for candidate in proposals:
            evaluation = self._assess_plan(self._instance, self._objectives, candidate)
            if not isinstance(evaluation, ApplicationEvaluation):
                raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
            if not _usable(evaluation, self._objectives):
                continue
            if context.ensure_feasible and evaluation.feasible is not True:
                continue
            assert evaluation.objective_values is not None
            result.append((candidate, evaluation.objective_values))
        return tuple(result)

    def _add_candidates(
        self, solution: VrpSolution, package_id: str, context: CandidateContext
    ) -> tuple[VrpSolution, ...]:
        delivery = _activity_occurrence(solution, package_id, 'delivery')
        if delivery is None:
            return ()
        sender_route_index, delivery_index, delivery_stop = delivery
        sender_route = solution.routes[sender_route_index]
        compatibility = _compatibility(self._instance, package_id)
        receiver_routes = tuple(
            index
            for index, route in enumerate(solution.routes)
            if route.day_index == sender_route.day_index
            and route.vehicle_id != sender_route.vehicle_id
            and compatibility.get(route.vehicle_id, True)
        )
        if not receiver_routes:
            return ()
        existing = sum(
            step.package_id == package_id for step in solution.transfer_steps
        )
        step_number = existing + 1
        facilities = tuple(
            sorted(
                item.node_id
                for item in self._instance.package_data.package_transfer_facilities
                if item.package_id == package_id
            )
        )
        if not facilities:
            return ()
        trials: list[VrpSolution] = []
        for receiver_index in randomized_sample(
            receiver_routes, self._candidate_limit, context.random_source
        ):
            for facility_id in randomized_sample(
                facilities, self._candidate_limit, context.random_source
            ):
                stops_by_route = {
                    sender_route_index: list(sender_route.stops),
                    receiver_index: list(solution.routes[receiver_index].stops),
                }
                del stops_by_route[sender_route_index][delivery_index]
                out_stop = TransferOperationStop(
                    TransferStepRef(package_id, step_number),
                    'out',
                    0.0,
                    0.0,
                    0.0,
                    False,
                )
                stops_by_route[sender_route_index].insert(delivery_index, out_stop)
                incoming = TransferOperationStop(
                    TransferStepRef(package_id, step_number),
                    'in',
                    0.0,
                    0.0,
                    0.0,
                    False,
                )
                insertion = choose_uniformly(
                    tuple(range(len(stops_by_route[receiver_index]) + 1)),
                    context.random_source,
                )
                stops_by_route[receiver_index][insertion:insertion] = [
                    incoming,
                    delivery_stop,
                ]
                provisional = replace(
                    solution,
                    transfer_steps=(
                        *solution.transfer_steps,
                        TransferStep(package_id, step_number, facility_id),
                    ),
                )
                routes = list(provisional.routes)
                routes[sender_route_index] = replace(
                    routes[sender_route_index],
                    stops=tuple(stops_by_route[sender_route_index]),
                )
                routes[receiver_index] = replace(
                    routes[receiver_index],
                    stops=tuple(stops_by_route[receiver_index]),
                )
                rebuilt = self._rebuild(
                    solution,
                    replace(provisional, routes=tuple(routes)),
                    {sender_route_index, receiver_index},
                    context,
                )
                trials.extend(rebuilt)
                if len(trials) >= self._candidate_limit:
                    return tuple(trials[: self._candidate_limit])
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
        following = next(
            (
                step
                for step in solution.transfer_steps
                if step.package_id == package_id and step.step_number == step_number + 1
            ),
            None,
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
        if move_occurrence is not None and destination_route_index is not None:
            source_index, source_stop_index, moving_stop = move_occurrence
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
            insertion = len(stop_lists[destination_route_index])
            stop_lists[destination_route_index].insert(insertion, moving_stop)
            affected.update((source_index, destination_route_index))

        transfer_steps = tuple(
            replace(step, step_number=step.step_number - 1)
            if step.package_id == package_id and step.step_number > step_number
            else step
            for step in solution.transfer_steps
            if not (step.package_id == package_id and step.step_number == step_number)
        )
        for route_index, stops in enumerate(stop_lists):
            updated: list[RouteStop] = []
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
                updated.append(stop)
            stop_lists[route_index] = updated
        routes = list(solution.routes)
        for index in affected:
            routes[index] = replace(routes[index], stops=tuple(stop_lists[index]))
        provisional = replace(
            solution, routes=tuple(routes), transfer_steps=transfer_steps
        )
        return self._rebuild(
            solution,
            provisional,
            affected,
            context,
        )

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
        eligible_facilities = tuple(
            sorted(
                item.node_id
                for item in self._instance.package_data.package_transfer_facilities
                if item.package_id == package_id and item.node_id != step.facility_id
            )
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
                if candidate not in trials:
                    trials.append(candidate)

        for facility_id in randomized_sample(
            eligible_facilities, self._candidate_limit, context.random_source
        ):
            updated_steps = list(solution.transfer_steps)
            updated_steps[step_index] = replace(step, facility_id=facility_id)
            provisional = replace(solution, transfer_steps=tuple(updated_steps))
            collect(self._rebuild(solution, provisional, affected, context))

        pair = _transfer_occurrences(solution, package_id, step_number)
        if pair is not None:
            for route_index, stop_index, operation in pair:
                remaining = list(solution.routes[route_index].stops)
                del remaining[stop_index]
                positions = tuple(range(len(remaining) + 1))
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
                    collect(
                        self._rebuild(
                            solution,
                            replace(solution, routes=tuple(routes)),
                            {route_index},
                            context,
                        )
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
        compatibility = _compatibility(self._instance, package_id)
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
            collect(self._rebuild(solution, provisional, affected_routes, context))
        return _sample_candidates(trials, self._candidate_limit, context)

    def _rebuild(
        self,
        original: VrpSolution,
        provisional: VrpSolution,
        route_indices: set[int],
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        return _rebuild_routes(
            self._instance,
            original,
            provisional,
            route_indices,
            random_source=context.random_source,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_candidate_alternatives=self._candidate_limit,
        )

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


def _sample_candidates(
    candidates: list[VrpSolution], limit: int, context: CandidateContext
) -> tuple[VrpSolution, ...]:
    """Sample a bounded set of structurally distinct complete alternatives."""
    unique = tuple(dict.fromkeys(candidates))
    return randomized_sample(unique, limit, context.random_source)
