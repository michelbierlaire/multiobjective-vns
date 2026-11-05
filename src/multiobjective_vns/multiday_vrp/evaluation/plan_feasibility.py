"""Plan feasibility methods for multi-day VRP evaluation.

Created: 2026-11-03T18:47:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from math import fsum
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.helpers import (
    _greater,
)
from multiobjective_vns.multiday_vrp.evaluation.runtime import (
    _EvaluatorRuntime,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityKind,
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    TransferOperationStop,
    VehicleDayRoute,
    VrpSolution,
)


class _PlanFeasibilityMethods(_EvaluatorRuntime):
    """Provide plan feasibility methods checks."""

    __slots__ = ()

    def _static_hard_failure(self, solution: VrpSolution) -> str | None:

        instance = self._instance

        scenario_bank = instance.scenario_bank

        day_boundaries = scenario_bank.day_boundaries

        vehicle_map = {item.vehicle_id: item for item in instance.fleet_data.vehicles}

        depot_map = {
            item.depot_id: item.node_id for item in instance.network_data.depots
        }

        overnight: dict[str, set[str]] = defaultdict(set)

        for item in instance.fleet_data.vehicle_overnight_depots:
            overnight[item.vehicle_id].add(item.depot_id)

        driver_ids = {
            item.person_id
            for item in instance.personnel_data.personnel
            if item.authorized_driver
        }

        vehicle_skills: dict[str, set[str]] = defaultdict(set)

        for skill_record in instance.fleet_data.vehicle_driving_skills:
            vehicle_skills[skill_record.vehicle_id].add(skill_record.skill_id)

        person_skills: dict[str, set[str]] = defaultdict(set)

        for personnel_skill in instance.personnel_data.personnel_driving_skills:
            person_skills[personnel_skill.person_id].add(personnel_skill.skill_id)

        if self._instance.settings.staff_depot_mode == 'depot_continuity':
            for person in instance.personnel_data.personnel:
                if person.initial_depot_id not in depot_map:
                    return f'person {person.person_id!r} has no initial depot'

        occurrences: dict[ActivityRef, list[tuple[VehicleDayRoute, int]]] = defaultdict(
            list
        )

        staff_duties: dict[str, list[tuple[float, str, int, str, str]]] = defaultdict(
            list
        )

        routes_by_vehicle_day = {
            (item.vehicle_id, item.day_index): item for item in solution.routes
        }

        for route in solution.routes:
            vehicle = vehicle_map[route.vehicle_id]

            day_start, day_end = (
                day_boundaries[route.day_index - 1][0],
                day_boundaries[route.day_index - 1][-1],
            )

            if route.start_time < day_start or route.start_time >= day_end:
                return 'route start time is outside its calendar day'

            if route.overnight_depot_id not in overnight[route.vehicle_id]:
                return 'route overnight depot is not eligible for its vehicle'

            if route.driver_id not in driver_ids:
                return 'route driver is not authorized'

            if not vehicle_skills[route.vehicle_id].issubset(
                person_skills[route.driver_id]
            ):
                return 'route driver lacks a required vehicle driving skill'

            if (
                sum(1 for stop in route.stops if stop.tour_end) + 1
                > vehicle.max_tours_per_day
            ):
                return 'route exceeds its vehicle tour limit'

            crew = {route.driver_id}

            for stop_index, stop in enumerate(route.stops):
                if type(stop) is not ActivityStop:
                    continue

                reference = stop.activity_ref

                occurrences[reference].append((route, stop_index))

                if reference.kind == 'intervention':
                    crew.update(stop.team_member_ids)

                    intervention = next(
                        item
                        for item in instance.intervention_data.interventions
                        if item.intervention_id == reference.activity_id
                    )

                    if len(stop.team_member_ids) < intervention.minimum_team_size:
                        return 'intervention team is smaller than its minimum size'

                    required = {
                        item.qualification_id
                        for item in instance.intervention_data.intervention_qualifications_required
                        if item.intervention_id == reference.activity_id
                    }

                    qualifications: dict[str, set[str]] = defaultdict(set)

                    for (
                        qualification_record
                    ) in instance.personnel_data.personnel_intervention_qualifications:
                        qualifications[qualification_record.person_id].add(
                            qualification_record.qualification_id
                        )

                    held = set().union(
                        *(
                            qualifications[person_id]
                            for person_id in stop.team_member_ids
                        )
                    )

                    if not required.issubset(held):
                        return 'intervention team lacks a required qualification'

                    eligible_days = {
                        item.day_index
                        for item in instance.intervention_data.intervention_time_windows
                        if item.intervention_id == reference.activity_id
                    }

                    if route.day_index not in eligible_days:
                        return 'intervention is assigned to an ineligible day'

            if len(crew) > vehicle.seat_capacity:
                return 'route crew exceeds vehicle seat capacity'

            previous_routes = [
                prior
                for (vehicle_id, day_index), prior in routes_by_vehicle_day.items()
                if vehicle_id == route.vehicle_id and day_index < route.day_index
            ]

            starting_depot_id = (
                max(previous_routes, key=lambda item: item.day_index).overnight_depot_id
                if previous_routes
                else vehicle.initial_depot_id
            )

            for person_id in crew:
                staff_duties[person_id].append(
                    (
                        route.start_time,
                        route.vehicle_id,
                        route.day_index,
                        starting_depot_id,
                        route.overnight_depot_id,
                    )
                )

        if instance.settings.staff_depot_mode == 'depot_continuity':
            person_map = {
                item.person_id: item for item in instance.personnel_data.personnel
            }

            for person_id, duties in staff_duties.items():
                expected_depot = person_map[person_id].initial_depot_id

                for duty in sorted(duties, key=lambda item: item[:3]):
                    if duty[3] != expected_depot:
                        return (
                            f'person {person_id!r} starts a duty at a depot that '
                            'breaks staff depot continuity'
                        )

                    expected_depot = duty[4]

        expected = [
            reference
            for package in instance.package_data.packages
            for reference in (
                ActivityRef('pickup', package.package_id),
                ActivityRef('delivery', package.package_id),
            )
        ]

        expected.extend(
            ActivityRef('intervention', item.intervention_id)
            for item in instance.intervention_data.interventions
        )

        for reference, found in occurrences.items():
            if len(found) > 1:
                return f'activity {reference!r} occurs more than once'

        for reference in expected:
            count = len(occurrences.get(reference, ()))

            if instance.settings.service_coverage_mode == 'mandatory' and count != 1:
                return f'required activity {reference!r} is missing'

            if count > 1:
                return f'activity {reference!r} occurs more than once'

        for precedence in instance.activity_relations.precedence:
            before = ActivityRef(
                cast(ActivityKind, precedence.before_activity_kind),
                precedence.before_activity_id,
            )

            after = ActivityRef(
                cast(ActivityKind, precedence.after_activity_kind),
                precedence.after_activity_id,
            )

            if occurrences.get(after) and not occurrences.get(before):
                return f'activity {after!r} occurs without prerequisite {before!r}'

        for co_location in instance.activity_relations.co_location:
            first = ActivityRef(
                cast(ActivityKind, co_location.activity_kind_1),
                co_location.activity_id_1,
            )

            second = ActivityRef(
                cast(ActivityKind, co_location.activity_kind_2),
                co_location.activity_id_2,
            )

            if not occurrences.get(first) or not occurrences.get(second):
                continue

            if self._activity_node(first) != self._activity_node(second):
                return f'co-located activities {first!r} and {second!r} use different nodes'

        for package in instance.package_data.packages:
            pickup = occurrences.get(ActivityRef('pickup', package.package_id), [])

            delivery = occurrences.get(ActivityRef('delivery', package.package_id), [])

            transfers = [
                item
                for item in solution.transfer_steps
                if item.package_id == package.package_id
            ]

            if len(transfers) > package.max_transfers:
                return f'package {package.package_id!r} exceeds its transfer limit'

            if (
                pickup
                and delivery
                and not transfers
                and pickup[0][0].vehicle_id != delivery[0][0].vehicle_id
            ):
                return (
                    f'package {package.package_id!r} changes carrier without a transfer'
                )

            for step in transfers:
                matching: dict[str, list[tuple[VehicleDayRoute, int]]] = {
                    'out': [],
                    'in': [],
                }

                for route in solution.routes:
                    for index, stop in enumerate(route.stops):
                        if (
                            type(stop) is TransferOperationStop
                            and stop.transfer_step_ref.package_id == step.package_id
                            and stop.transfer_step_ref.step_number == step.step_number
                        ):
                            matching[stop.role].append((route, index))

                if len(matching['out']) != 1 or len(matching['in']) != 1:
                    return f'transfer step {step!r} needs one outgoing and one incoming operation'

                out_route = matching['out'][0][0]

                in_route = matching['in'][0][0]

                if out_route.vehicle_id == in_route.vehicle_id:
                    return f'transfer step {step!r} must use distinct vehicles'

                if step.facility_id not in {
                    item.node_id
                    for item in instance.package_data.package_transfer_facilities
                    if item.package_id == package.package_id
                }:
                    return f'transfer step {step!r} uses an ineligible facility'

                if (
                    pickup
                    and out_route.vehicle_id != pickup[0][0].vehicle_id
                    and step.step_number == 1
                ):
                    return f'package {package.package_id!r} transfer starts with the wrong carrier'

                if (
                    delivery
                    and step.step_number == len(transfers)
                    and in_route.vehicle_id != delivery[0][0].vehicle_id
                ):
                    return f'package {package.package_id!r} delivery uses the wrong carrier'

        compatibility = {
            (item.vehicle_id, item.package_id): item.compatible
            for item in instance.package_data.vehicle_package_compatibility
        }

        package_by_id = {
            item.package_id: item for item in instance.package_data.packages
        }

        onboard_by_vehicle: dict[str, set[str]] = defaultdict(set)

        ordered_routes = sorted(
            solution.routes, key=lambda item: (item.day_index, item.vehicle_id)
        )

        for route in ordered_routes:
            vehicle = vehicle_map[route.vehicle_id]

            onboard = onboard_by_vehicle[route.vehicle_id]

            tool_load_by_stop = self._tool_load_by_stop(solution, route)

            if _greater(
                fsum(package_by_id[item].load for item in sorted(onboard)),
                vehicle.cargo_capacity,
            ):
                return f'vehicle {route.vehicle_id!r} begins over cargo capacity'

            for stop_index, stop in enumerate(route.stops):
                tool_load = tool_load_by_stop[stop_index]

                current_load = fsum(
                    package_by_id[item].load for item in sorted(onboard)
                )

                if _greater(fsum((current_load, tool_load)), vehicle.cargo_capacity):
                    return f'vehicle {route.vehicle_id!r} exceeds cargo/tool capacity'

                package_id: str | None = None

                operation: str | None = None

                if type(stop) is ActivityStop and stop.activity_ref.kind in {
                    'pickup',
                    'delivery',
                }:
                    package_id = stop.activity_ref.activity_id

                    operation = stop.activity_ref.kind

                elif type(stop) is TransferOperationStop:
                    package_id = stop.transfer_step_ref.package_id

                    operation = 'transfer_out' if stop.role == 'out' else 'transfer_in'

                if package_id is None or operation is None:
                    continue

                if operation in {'pickup', 'transfer_in'}:
                    if package_id in onboard:
                        return f'package {package_id!r} is loaded onto a vehicle twice'

                    if not compatibility.get((route.vehicle_id, package_id), False):
                        return (
                            f'vehicle {route.vehicle_id!r} is incompatible with '
                            f'package {package_id!r}'
                        )

                    onboard.add(package_id)

                else:
                    if package_id not in onboard:
                        return (
                            f'package {package_id!r} leaves a vehicle it is not aboard'
                        )

                    onboard.remove(package_id)

                load = fsum(package_by_id[item].load for item in sorted(onboard))

                if _greater(fsum((load, tool_load)), vehicle.cargo_capacity):
                    return f'vehicle {route.vehicle_id!r} exceeds cargo/tool capacity'

        return None

    def _tool_load_by_stop(
        self, solution: VrpSolution, route: VehicleDayRoute
    ) -> tuple[float, ...]:

        if not route.stops:
            return ()

        depot_nodes = {item.node_id for item in self._instance.network_data.depots}

        depot_by_id = {
            item.depot_id: item.node_id for item in self._instance.network_data.depots
        }

        vehicle = next(
            item
            for item in self._instance.fleet_data.vehicles
            if item.vehicle_id == route.vehicle_id
        )

        previous_routes = [
            item
            for item in solution.routes
            if item.vehicle_id == route.vehicle_id and item.day_index < route.day_index
        ]

        starting_depot_id = (
            max(previous_routes, key=lambda item: item.day_index).overnight_depot_id
            if previous_routes
            else vehicle.initial_depot_id
        )

        start_node = depot_by_id[starting_depot_id]

        transfer_steps = {
            (item.package_id, item.step_number): item
            for item in solution.transfer_steps
        }

        stop_nodes: list[str] = []

        for stop in route.stops:
            if type(stop) is ActivityStop:
                stop_nodes.append(self._activity_node(stop.activity_ref))

            elif type(stop) is TransferOperationStop:
                step = transfer_steps[
                    (
                        stop.transfer_step_ref.package_id,
                        stop.transfer_step_ref.step_number,
                    )
                ]

                stop_nodes.append(step.facility_id)

            elif type(stop) is DepotOnlyStop:
                stop_nodes.append(stop.node_id)

        required_tools_by_intervention: dict[str, set[str]] = defaultdict(set)

        for relation in self._instance.intervention_data.intervention_tools:
            required_tools_by_intervention[relation.intervention_id].add(
                relation.tool_id
            )

        tool_demands = {
            item.tool_id: item.capacity_demand
            for item in self._instance.intervention_data.tools
        }

        loads = [0.0] * len(route.stops)

        segment_start = 0

        start_side_end = -1

        if stop_nodes[0] == start_node and not route.legs[0].arc_ids:
            start_side_end = 0

            while (
                start_side_end + 1 < len(route.stops)
                and stop_nodes[start_side_end + 1] == start_node
                and not route.legs[start_side_end + 1].arc_ids
            ):
                start_side_end += 1

        def assign_segment(start: int, end: int) -> None:

            required: set[str] = set()

            for index in range(start, end + 1):
                stop = route.stops[index]

                if (
                    type(stop) is ActivityStop
                    and stop.activity_ref.kind == 'intervention'
                ):
                    required.update(
                        required_tools_by_intervention[stop.activity_ref.activity_id]
                    )

            load = fsum(tool_demands[item] for item in sorted(required))

            for index in range(start, end + 1):
                loads[index] = load

        index = start_side_end + 1

        while index < len(route.stops):
            if stop_nodes[index] not in depot_nodes:
                index += 1

                continue

            block_end = index

            while (
                block_end + 1 < len(route.stops)
                and stop_nodes[block_end + 1] == stop_nodes[index]
                and not route.legs[block_end + 1].arc_ids
            ):
                block_end += 1

            assign_segment(segment_start, block_end)

            segment_start = block_end + 1

            index = block_end + 1

        if segment_start < len(route.stops):
            assign_segment(segment_start, len(route.stops) - 1)

        elif start_side_end >= 0 and segment_start == 0:
            assign_segment(0, len(route.stops) - 1)

        return tuple(loads)
