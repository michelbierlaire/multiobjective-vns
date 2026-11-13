"""Scenario feasibility methods for multi-day VRP evaluation.

Created: 2026-11-03T18:47:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from math import fsum
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.helpers import (
    _custody_event_rank,
    _greater,
)
from multiobjective_vns.multiday_vrp.evaluation.records import ResourceFinding
from multiobjective_vns.multiday_vrp.evaluation.runtime import (
    _EvaluatorRuntime,
)
from multiobjective_vns.multiday_vrp.extensions import (
    StopScenarioEvaluation,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityKind,
    ActivityRef,
    ActivityStop,
    TransferOperationStop,
    TransferStepRef,
    VrpSolution,
)


class _ScenarioFeasibilityMethods(_EvaluatorRuntime):
    """Provide scenario feasibility methods checks."""

    __slots__ = ()

    def _transfer_storage_resource_findings(
        self, solution: VrpSolution, trace: VrpScenarioEvaluation
    ) -> tuple[ResourceFinding, ...]:
        """Return each facility's maximum storage excess in one scenario."""
        package_by_id = {
            item.package_id: item for item in self._instance.package_data.packages
        }
        transfer_steps_by_ref = {
            (item.package_id, item.step_number): item
            for item in solution.transfer_steps
        }
        facilities = {
            item.node_id: item.storage_capacity
            for item in self._instance.network_data.transfer_facilities
        }
        storage_by_facility: dict[str, set[str]] = defaultdict(set)
        storage_load_by_facility = {storage_id: 0.0 for storage_id in facilities}
        owner_by_package: dict[str, str | None] = {}
        stored_step_by_package: dict[str, TransferStepRef | None] = {}
        for package in self._instance.package_data.packages:
            owner = (
                package.pickup_node_id if package.pickup_node_id in facilities else None
            )
            owner_by_package[package.package_id] = owner
            stored_step_by_package[package.package_id] = None
            if owner is not None:
                storage_by_facility[owner].add(package.package_id)
                storage_load_by_facility[owner] += package.load
        largest_excess = {storage_id: 0.0 for storage_id in facilities}
        peak_transfer_steps: dict[str, set[TransferStepRef]] = {
            storage_id: set() for storage_id in facilities
        }

        def record_excess(facility_id: str) -> None:
            load = storage_load_by_facility[facility_id]
            capacity = facilities[facility_id]
            if not _greater(load, capacity):
                return
            excess = load - capacity
            if excess > largest_excess[facility_id]:
                largest_excess[facility_id] = excess
                peak_transfer_steps[facility_id] = {
                    step_ref
                    for package_id in storage_by_facility[facility_id]
                    if (step_ref := stored_step_by_package[package_id]) is not None
                }
            elif excess == largest_excess[facility_id]:
                peak_transfer_steps[facility_id].update(
                    step_ref
                    for package_id in storage_by_facility[facility_id]
                    if (step_ref := stored_step_by_package[package_id]) is not None
                )

        for storage_facility_id in facilities:
            record_excess(storage_facility_id)
        events: list[tuple[float, int, int, str, str, int | None, str | None, str]] = []
        for route_index, (route, vehicle_day) in enumerate(
            zip(solution.routes, trace.vehicle_days, strict=True)
        ):
            for stop_index, (route_stop, evaluated_stop) in enumerate(
                zip(route.stops, vehicle_day.stops, strict=True)
            ):
                action: str
                package_id: str
                facility_id: str | None
                step_number: int | None
                if type(route_stop) is ActivityStop:
                    if route_stop.activity_ref.kind not in {'pickup', 'delivery'}:
                        continue
                    action = route_stop.activity_ref.kind
                    package_id = route_stop.activity_ref.activity_id
                    facility_id = None
                    step_number = None
                elif type(route_stop) is TransferOperationStop:
                    reference = route_stop.transfer_step_ref
                    step = transfer_steps_by_ref[
                        (reference.package_id, reference.step_number)
                    ]
                    action = (
                        'transfer_out' if route_stop.role == 'out' else 'transfer_in'
                    )
                    package_id = reference.package_id
                    facility_id = step.facility_id
                    step_number = reference.step_number
                else:
                    continue
                if evaluated_stop.service_finish is None:
                    continue
                events.append(
                    (
                        evaluated_stop.service_finish,
                        route_index,
                        stop_index,
                        action,
                        package_id,
                        step_number,
                        facility_id,
                        route.vehicle_id,
                    )
                )

        events.sort(key=lambda item: (item[0], item[1], item[2]))
        event_index = 0
        while event_index < len(events):
            event_time = events[event_index][0]
            matching_events = []
            while event_index < len(events) and events[event_index][0] == event_time:
                matching_events.append(events[event_index])
                event_index += 1
            matching_events.sort(
                key=lambda item: (
                    item[4],
                    _custody_event_rank(
                        item[3], item[5], package_by_id[item[4]].max_transfers
                    ),
                    item[1],
                    item[2],
                )
            )
            changed_facilities: set[str] = set()
            for (
                _time,
                _route_index,
                _stop_index,
                action,
                package_id,
                _step_number,
                facility_id,
                vehicle_id,
            ) in matching_events:
                owner = owner_by_package[package_id]
                if action == 'pickup':
                    if owner in facilities:
                        storage_by_facility[owner].discard(package_id)
                        storage_load_by_facility[owner] -= package_by_id[
                            package_id
                        ].load
                        changed_facilities.add(owner)
                    owner_by_package[package_id] = vehicle_id
                elif action == 'delivery':
                    owner_by_package[package_id] = None
                elif action == 'transfer_out':
                    if facility_id is not None:
                        owner_by_package[package_id] = facility_id
                        storage_by_facility[facility_id].add(package_id)
                        storage_load_by_facility[facility_id] += package_by_id[
                            package_id
                        ].load
                        if _step_number is not None:
                            stored_step_by_package[package_id] = TransferStepRef(
                                package_id, _step_number
                            )
                        changed_facilities.add(facility_id)
                else:
                    if facility_id is not None:
                        storage_by_facility[facility_id].discard(package_id)
                        storage_load_by_facility[facility_id] -= package_by_id[
                            package_id
                        ].load
                        stored_step_by_package[package_id] = None
                        owner_by_package[package_id] = vehicle_id
                        changed_facilities.add(facility_id)
            for facility_id in sorted(changed_facilities):
                record_excess(facility_id)

        findings: list[ResourceFinding] = []
        for reported_facility_id, measure in sorted(largest_excess.items()):
            if measure <= 0.0:
                continue
            contributing_steps: tuple[TransferStepRef, ...] = tuple(
                sorted(
                    peak_transfer_steps[reported_facility_id],
                    key=lambda step_ref: (step_ref.package_id, step_ref.step_number),
                )
            )
            transfer_step_refs: tuple[TransferStepRef | None, ...] = (
                contributing_steps if contributing_steps else (None,)
            )
            findings.extend(
                ResourceFinding(
                    kind='transfer_storage',
                    resource_id=reported_facility_id,
                    scenario_id=trace.scenario_id,
                    vehicle_id=None,
                    day_index=None,
                    activity_ref=None,
                    measure=measure,
                    penalty_by_objective=(),
                    transfer_step_ref=step_ref,
                )
                for step_ref in transfer_step_refs
            )
        return tuple(findings)

    def _scenario_hard_failure(
        self, trace: VrpScenarioEvaluation, solution: VrpSolution
    ) -> str | None:

        boundaries = self._instance.scenario_bank.day_boundaries

        for vehicle_day in trace.vehicle_days:
            day_end = boundaries[vehicle_day.day_index - 1][-1]

            if _greater(vehicle_day.duty_end_time, day_end):
                return 'vehicle route returns after its calendar-day boundary'

            for stop in vehicle_day.stops:
                if stop.service_start is not None and stop.service_start >= day_end:
                    return 'service starts after its calendar-day boundary'

                if stop.service_finish is not None and _greater(
                    stop.service_finish, day_end
                ):
                    return 'service finishes after its calendar-day boundary'

        stop_by_activity: dict[ActivityRef, StopScenarioEvaluation] = {}

        for route, vehicle_day in zip(solution.routes, trace.vehicle_days, strict=True):
            for route_stop, evaluated_stop in zip(
                route.stops, vehicle_day.stops, strict=True
            ):
                if type(route_stop) is ActivityStop:
                    stop_by_activity[route_stop.activity_ref] = evaluated_stop

        for precedence in self._instance.activity_relations.precedence:
            before = ActivityRef(
                cast(ActivityKind, precedence.before_activity_kind),
                precedence.before_activity_id,
            )

            after = ActivityRef(
                cast(ActivityKind, precedence.after_activity_kind),
                precedence.after_activity_id,
            )

            before_stop = stop_by_activity.get(before)

            after_stop = stop_by_activity.get(after)

            if before_stop is None or after_stop is None:
                continue

            if (
                before_stop.service_finish is not None
                and after_stop.service_start is not None
                and _greater(before_stop.service_finish, after_stop.service_start)
            ):
                return f'precedence {before!r} before {after!r} is violated'

        custody_failure = self._scenario_custody_failure(solution, trace)

        if custody_failure is not None:
            return custody_failure

        return None

    def _scenario_custody_failure(
        self, solution: VrpSolution, trace: VrpScenarioEvaluation
    ) -> str | None:

        package_by_id = {
            item.package_id: item for item in self._instance.package_data.packages
        }

        transfer_steps = {
            (item.package_id, item.step_number): item
            for item in solution.transfer_steps
        }

        facilities = {
            item.node_id: item.storage_capacity
            for item in self._instance.network_data.transfer_facilities
        }

        storage_by_facility: dict[str, set[str]] = defaultdict(set)

        for package in self._instance.package_data.packages:
            if package.pickup_node_id in facilities:
                storage_by_facility[package.pickup_node_id].add(package.package_id)

        owner_by_package: dict[str, str | None] = {
            package.package_id: (
                package.pickup_node_id if package.pickup_node_id in facilities else None
            )
            for package in self._instance.package_data.packages
        }

        initial_overload = self._first_storage_overload(storage_by_facility, facilities)

        if initial_overload is not None:
            return initial_overload

        events: list[tuple[float, int, int, str, str, int | None, str | None, str]] = []

        for route_index, (route, vehicle_day) in enumerate(
            zip(solution.routes, trace.vehicle_days, strict=True)
        ):
            for stop_index, (route_stop, evaluated_stop) in enumerate(
                zip(route.stops, vehicle_day.stops, strict=True)
            ):
                if type(route_stop) is ActivityStop:
                    activity_reference = route_stop.activity_ref

                    if activity_reference.kind not in {'pickup', 'delivery'}:
                        continue

                    action: str = activity_reference.kind

                    package_id = activity_reference.activity_id

                    facility_id = None

                    step_number = None

                elif type(route_stop) is TransferOperationStop:
                    transfer_reference = route_stop.transfer_step_ref

                    step = transfer_steps[
                        (
                            transfer_reference.package_id,
                            transfer_reference.step_number,
                        )
                    ]

                    action = (
                        'transfer_out' if route_stop.role == 'out' else 'transfer_in'
                    )

                    package_id = transfer_reference.package_id

                    facility_id = step.facility_id

                    step_number = transfer_reference.step_number

                else:
                    continue

                if evaluated_stop.service_finish is None:
                    continue

                events.append(
                    (
                        evaluated_stop.service_finish,
                        route_index,
                        stop_index,
                        action,
                        package_id,
                        step_number,
                        facility_id,
                        route.vehicle_id,
                    )
                )

        events.sort(key=lambda item: (item[0], item[1], item[2]))

        event_index = 0

        while event_index < len(events):
            event_time = events[event_index][0]

            matching_events: list[
                tuple[
                    float,
                    int,
                    int,
                    str,
                    str,
                    int | None,
                    str | None,
                    str,
                ]
            ] = []

            while event_index < len(events) and events[event_index][0] == event_time:
                matching_events.append(events[event_index])

                event_index += 1

            matching_events.sort(
                key=lambda item: (
                    item[4],
                    _custody_event_rank(
                        item[3], item[5], package_by_id[item[4]].max_transfers
                    ),
                    item[1],
                    item[2],
                )
            )

            for (
                _,
                _,
                _,
                action,
                package_id,
                _,
                facility_id,
                vehicle_id,
            ) in matching_events:
                package = package_by_id[package_id]

                owner = owner_by_package[package_id]

                if action == 'pickup':
                    if owner is not None:
                        if owner not in facilities:
                            return f'package {package_id!r} is picked up while already aboard'

                        storage_by_facility[owner].remove(package_id)

                    owner_by_package[package_id] = vehicle_id

                elif action == 'delivery':
                    if owner != vehicle_id:
                        return f'package {package_id!r} is delivered by a vehicle that does not carry it'

                    owner_by_package[package_id] = None

                elif action == 'transfer_out':
                    if owner != vehicle_id or facility_id is None:
                        return f'package {package_id!r} is transferred out by a non-carrier'

                    owner_by_package[package_id] = facility_id

                    storage_by_facility[facility_id].add(package_id)

                else:
                    if owner != facility_id:
                        return f'package {package_id!r} is transferred in before it reaches the facility'

                    assert facility_id is not None

                    storage_by_facility[facility_id].remove(package_id)

                    owner_by_package[package_id] = vehicle_id

            failure = self._first_storage_overload(storage_by_facility, facilities)

            if failure is not None:
                return failure

        return None

    def _first_storage_overload(
        self,
        storage_by_facility: dict[str, set[str]],
        facilities: dict[str, float],
    ) -> str | None:

        package_loads = {
            item.package_id: item.load for item in self._instance.package_data.packages
        }

        for facility_id, package_ids in storage_by_facility.items():
            load = fsum(package_loads[package_id] for package_id in sorted(package_ids))

            if _greater(load, facilities[facility_id]):
                return f'transfer facility {facility_id!r} exceeds storage capacity'

        return None
