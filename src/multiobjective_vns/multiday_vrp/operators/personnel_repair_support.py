"""Shared rule measurements and assignment helpers for personnel repairs.

Created: 2026-11-04T09:56:38+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from math import fsum
from typing import Literal

from multiobjective_vns.multiday_vrp.evaluation.records import (
    ResourceFinding,
    VrpOperatorAnalysis,
)
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation

type PersonnelRepairMode = Literal[
    'skill_guided', 'availability', 'staff_continuity', 'work_rule'
]
type AssignmentRole = Literal['driver', 'team']

__all__ = [
    'AssignmentRole',
    'DutyTarget',
    'PersonnelRepairMode',
    'RepairTarget',
    'analysis_of',
    'person_duty_targets',
    'qualified_drivers',
    'repair_score',
    'skill_violations',
]


@dataclass(frozen=True, slots=True)
class DutyTarget:
    """One person assigned to one vehicle-day duty in one role."""

    route_index: int
    person_id: str
    role: AssignmentRole


@dataclass(frozen=True, slots=True)
class RepairTarget:
    """A selected duty assignment and optional work-rule finding."""

    duty: DutyTarget
    finding: ResourceFinding | None = None


def analysis_of(
    evaluation: ApplicationEvaluation | None,
) -> VrpOperatorAnalysis | None:
    """Return typed VRP analysis when the application supplied it."""
    if evaluation is None or not isinstance(evaluation.analysis, VrpOperatorAnalysis):
        return None
    return evaluation.analysis


def person_duty_targets(solution: VrpSolution) -> tuple[DutyTarget, ...]:
    """List each route's driver and distinct non-driver intervention personnel."""
    result: list[DutyTarget] = []
    for route_index, route in enumerate(solution.routes):
        result.append(DutyTarget(route_index, route.driver_id, 'driver'))
        team_members = {
            person_id
            for stop in route.stops
            if type(stop) is ActivityStop
            for person_id in stop.team_member_ids
        }
        result.extend(
            DutyTarget(route_index, person_id, 'team')
            for person_id in sorted(team_members - {route.driver_id})
        )
    return tuple(result)


def repair_score(
    instance: LoadedVrpInstance,
    mode: PersonnelRepairMode,
    solution: VrpSolution,
    evaluation: ApplicationEvaluation | None,
    target: RepairTarget,
) -> float:
    """Measure remaining violations relevant to one selected repair target."""
    if mode == 'skill_guided':
        return float(skill_violations(instance, solution))
    if mode == 'staff_continuity':
        return float(_continuity_violation_count(instance, solution))
    if mode == 'work_rule' and target.finding is not None:
        analysis = analysis_of(evaluation)
        if analysis is None:
            return float('inf')
        matches = tuple(
            item
            for item in analysis.resource_findings
            if _same_finding_key(item, target.finding)
        )
        if matches:
            return max(item.measure for item in matches)
        return 0.0 if analysis.is_complete else float('inf')
    return 0.0


def skill_violations(instance: LoadedVrpInstance, solution: VrpSolution) -> int:
    """Count routes whose assigned driver lacks authorization or required skills."""
    return sum(
        not _driver_qualified(instance, solution, route_index)
        for route_index in range(len(solution.routes))
    )


def qualified_drivers(
    instance: LoadedVrpInstance, solution: VrpSolution, route_index: int
) -> tuple[str, ...]:
    """List authorized personnel satisfying the selected vehicle's skill set."""
    route = solution.routes[route_index]
    required = {
        item.skill_id
        for item in instance.fleet_data.vehicle_driving_skills
        if item.vehicle_id == route.vehicle_id
    }
    held: dict[str, set[str]] = defaultdict(set)
    for item in instance.personnel_data.personnel_driving_skills:
        held[item.person_id].add(item.skill_id)
    return tuple(
        sorted(
            item.person_id
            for item in instance.personnel_data.personnel
            if item.authorized_driver and required <= held.get(item.person_id, set())
        )
    )


def _driver_qualified(
    instance: LoadedVrpInstance, solution: VrpSolution, route_index: int
) -> bool:
    route = solution.routes[route_index]
    required = {
        item.skill_id
        for item in instance.fleet_data.vehicle_driving_skills
        if item.vehicle_id == route.vehicle_id
    }
    held = {
        item.skill_id
        for item in instance.personnel_data.personnel_driving_skills
        if item.person_id == route.driver_id
    }
    person = next(
        (
            item
            for item in instance.personnel_data.personnel
            if item.person_id == route.driver_id
        ),
        None,
    )
    return person is not None and person.authorized_driver and required <= held


def _has_restricted_calendar(instance: LoadedVrpInstance, person_id: str) -> bool:
    intervals = tuple(
        sorted(
            (item.start_time, item.end_time)
            for item in instance.personnel_data.personnel_availability
            if item.person_id == person_id
        )
    )
    if not intervals:
        return False
    start = instance.scenario_bank.day_boundaries[0][0]
    end = instance.scenario_bank.day_boundaries[-1][-1]
    cursor = start
    for interval_start, interval_end in intervals:
        if interval_start > cursor + _tolerance(interval_start, cursor):
            return True
        cursor = max(cursor, interval_end)
    return cursor < end - _tolerance(cursor, end)


def _availability_exposure(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    duty: DutyTarget,
) -> float:
    intervals = tuple(
        (item.start_time, item.end_time)
        for item in instance.personnel_data.personnel_availability
        if item.person_id == duty.person_id
    )
    route = solution.routes[duty.route_index]
    values: list[float] = []
    for scenario in analysis.scenario_evaluations:
        vehicle_day = next(
            (
                item
                for item in scenario.vehicle_days
                if item.vehicle_id == route.vehicle_id
                and item.day_index == route.day_index
            ),
            None,
        )
        if vehicle_day is not None:
            values.append(
                scenario.probability
                * _outside_intervals(
                    vehicle_day.duty_start_time,
                    vehicle_day.duty_end_time,
                    intervals,
                )
            )
    return fsum(values)


def _outside_intervals(
    start: float, end: float, intervals: tuple[tuple[float, float], ...]
) -> float:
    clipped = sorted(
        (max(start, left), min(end, right))
        for left, right in intervals
        if right > start and left < end
    )
    covered = 0.0
    cursor = start
    for left, right in clipped:
        if right <= cursor:
            continue
        covered += max(0.0, right - max(left, cursor))
        cursor = right
    return max(0.0, end - start - covered)


def _continuity_targets(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> tuple[DutyTarget, ...]:
    if instance.settings.staff_depot_mode != 'depot_continuity':
        return ()
    mismatches = _continuity_mismatch_duties(instance, solution)
    return tuple(duty for duty in person_duty_targets(solution) if duty in mismatches)


def _continuity_violation_count(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> int:
    return len(_continuity_mismatch_duties(instance, solution))


def _continuity_mismatch_duties(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> set[DutyTarget]:
    if instance.settings.staff_depot_mode != 'depot_continuity':
        return set()
    starting_depots: dict[int, str] = {}
    routes_by_vehicle: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for index, route in enumerate(solution.routes):
        routes_by_vehicle[route.vehicle_id].append((route.day_index, index))
    for vehicle_id, day_routes in routes_by_vehicle.items():
        vehicle = next(
            item
            for item in instance.fleet_data.vehicles
            if item.vehicle_id == vehicle_id
        )
        previous_depot = vehicle.initial_depot_id
        for _day_index, route_index in sorted(day_routes):
            starting_depots[route_index] = previous_depot
            previous_depot = solution.routes[route_index].overnight_depot_id

    duties_by_person: dict[str, list[tuple[float, str, int, int, str, str]]] = (
        defaultdict(list)
    )
    for duty in person_duty_targets(solution):
        route = solution.routes[duty.route_index]
        duties_by_person[duty.person_id].append(
            (
                route.start_time,
                route.vehicle_id,
                route.day_index,
                duty.route_index,
                starting_depots[duty.route_index],
                route.overnight_depot_id,
            )
        )
    initial_depots = {
        item.person_id: item.initial_depot_id
        for item in instance.personnel_data.personnel
    }
    result: set[DutyTarget] = set()
    for person_id, duties in duties_by_person.items():
        expected = initial_depots.get(person_id)
        for _start, _vehicle, _day, route_index, start_depot, end_depot in sorted(
            duties
        ):
            if start_depot != expected:
                route = solution.routes[route_index]
                if route.driver_id == person_id:
                    result.add(DutyTarget(route_index, person_id, 'driver'))
                elif _team_assigned_on_route(route, person_id):
                    result.add(DutyTarget(route_index, person_id, 'team'))
            expected = end_depot
    return result


def _team_assigned_on_route(route: VehicleDayRoute, person_id: str) -> bool:
    return any(
        type(stop) is ActivityStop and person_id in stop.team_member_ids
        for stop in route.stops
    )


def _person_from_resource_id(
    instance: LoadedVrpInstance, resource_id: str
) -> str | None:
    matches = tuple(
        person.person_id
        for person in instance.personnel_data.personnel
        if resource_id == person.person_id
        or resource_id.startswith(person.person_id + ':')
    )
    return max(matches, key=len) if matches else None


def _finding_matches_duty(
    finding: ResourceFinding, duty: DutyTarget, solution: VrpSolution
) -> bool:
    route = solution.routes[duty.route_index]
    if finding.vehicle_id is not None and route.vehicle_id != finding.vehicle_id:
        return False
    if finding.day_index is not None and route.day_index != finding.day_index:
        return False
    if finding.kind == 'driving_time' and duty.role != 'driver':
        return False
    return True


def _maximum_finding(
    findings: tuple[ResourceFinding, ...],
    value_of: Callable[[ResourceFinding], float],
) -> ResourceFinding | None:
    if not findings:
        return None
    return max(findings, key=value_of)


def _same_finding_key(left: ResourceFinding, right: ResourceFinding) -> bool:
    return (
        left.kind == right.kind
        and left.resource_id == right.resource_id
        and left.vehicle_id == right.vehicle_id
        and left.day_index == right.day_index
    )


def _tolerance(left: float, right: float) -> float:
    return 1e-9 + 1e-12 * max(abs(left), abs(right))
