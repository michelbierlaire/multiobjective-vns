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
from multiobjective_vns.multiday_vrp.extensions import VehicleDayScenarioEvaluation
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
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
    'restricted_calendar_people',
    'skill_violations',
]


@dataclass(frozen=True, slots=True)
class DutyTarget:
    """One person assigned to a vehicle-day duty, optionally narrowed by activity."""

    route_index: int
    person_id: str
    role: AssignmentRole
    activity_ref: ActivityRef | None = None


@dataclass(frozen=True, slots=True)
class RepairTarget:
    """A selected duty assignment and optional work-rule finding."""

    duty: DutyTarget
    finding: ResourceFinding | None = None


@dataclass(frozen=True, slots=True)
class _ContinuityIndex:
    """Route endpoints and personnel duty order for one fixed schedule."""

    duties: tuple[DutyTarget, ...]
    starting_depots: dict[int, str]
    route_indices_by_person: dict[str, tuple[int, ...]]
    initial_depots_by_person: dict[str, str | None]


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


def restricted_calendar_people(instance: LoadedVrpInstance) -> frozenset[str]:
    """Return personnel whose declared intervals leave a planning-horizon gap."""
    intervals_by_person: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for item in instance.personnel_data.personnel_availability:
        intervals_by_person[item.person_id].append((item.start_time, item.end_time))
    start = instance.scenario_bank.day_boundaries[0][0]
    end = instance.scenario_bank.day_boundaries[-1][-1]
    restricted: set[str] = set()
    for person_id, intervals in intervals_by_person.items():
        cursor = start
        for interval_start, interval_end in sorted(intervals):
            if interval_start > cursor + _tolerance(interval_start, cursor):
                restricted.add(person_id)
                break
            cursor = max(cursor, interval_end)
        else:
            if cursor < end - _tolerance(cursor, end):
                restricted.add(person_id)
    return frozenset(restricted)


def _availability_exposures(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    duties: tuple[DutyTarget, ...],
) -> dict[DutyTarget, float]:
    """Measure target-duty exposure using indexes shared across the batch."""
    target_people = {duty.person_id for duty in duties}
    intervals_by_person: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for item in instance.personnel_data.personnel_availability:
        if item.person_id in target_people:
            intervals_by_person[item.person_id].append((item.start_time, item.end_time))
    normalized_intervals = {
        person_id: _merge_intervals(intervals)
        for person_id, intervals in intervals_by_person.items()
    }
    vehicle_days_by_scenario: tuple[
        tuple[float, dict[tuple[str, int], VehicleDayScenarioEvaluation]], ...
    ] = tuple(
        (
            scenario.probability,
            {(item.vehicle_id, item.day_index): item for item in scenario.vehicle_days},
        )
        for scenario in analysis.scenario_evaluations
    )

    exposures: dict[DutyTarget, float] = {}
    for duty in duties:
        route = solution.routes[duty.route_index]
        intervals = normalized_intervals.get(duty.person_id, ())
        values = (
            probability
            * _outside_intervals(
                vehicle_day.duty_start_time,
                vehicle_day.duty_end_time,
                intervals,
            )
            for probability, vehicle_days in vehicle_days_by_scenario
            if (vehicle_day := vehicle_days.get((route.vehicle_id, route.day_index)))
            is not None
        )
        exposures[duty] = fsum(values)
    return exposures


def _merge_intervals(
    intervals: list[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    """Merge overlapping or adjacent availability intervals."""
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _outside_intervals(
    start: float, end: float, intervals: tuple[tuple[float, float], ...]
) -> float:
    covered = 0.0
    cursor = start
    for interval_start, interval_end in intervals:
        if interval_end <= start or interval_start >= end:
            continue
        left = max(start, interval_start)
        right = min(end, interval_end)
        if right <= cursor:
            continue
        covered += max(0.0, right - max(left, cursor))
        cursor = right
    return max(0.0, end - start - covered)


def _continuity_targets(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    continuity: _ContinuityIndex | None = None,
) -> tuple[DutyTarget, ...]:
    if instance.settings.staff_depot_mode != 'depot_continuity':
        return ()
    if continuity is None:
        continuity = _continuity_index(instance, solution)
    mismatches = _continuity_mismatch_duties(instance, solution, continuity)
    return tuple(duty for duty in continuity.duties if duty in mismatches)


def _continuity_mismatch_duties(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    continuity: _ContinuityIndex | None = None,
) -> set[DutyTarget]:
    if instance.settings.staff_depot_mode != 'depot_continuity':
        return set()
    if continuity is None:
        continuity = _continuity_index(instance, solution)
    result: set[DutyTarget] = set()
    for person_id, route_indices in continuity.route_indices_by_person.items():
        expected = continuity.initial_depots_by_person.get(person_id)
        for route_index in route_indices:
            if continuity.starting_depots[route_index] != expected:
                route = solution.routes[route_index]
                if route.driver_id == person_id:
                    result.add(DutyTarget(route_index, person_id, 'driver'))
                elif _team_assigned_on_route(route, person_id):
                    result.add(DutyTarget(route_index, person_id, 'team'))
            expected = solution.routes[route_index].overnight_depot_id
    return result


def _continuity_index(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> _ContinuityIndex:
    """Index fixed route endpoints and each person's chronological duties."""
    starting_depots: dict[int, str] = {}
    routes_by_vehicle: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for index, route in enumerate(solution.routes):
        routes_by_vehicle[route.vehicle_id].append((route.day_index, index))
    initial_depots_by_vehicle = {
        item.vehicle_id: item.initial_depot_id for item in instance.fleet_data.vehicles
    }
    for vehicle_id, day_routes in routes_by_vehicle.items():
        previous_depot = initial_depots_by_vehicle[vehicle_id]
        for _day_index, route_index in sorted(day_routes):
            starting_depots[route_index] = previous_depot
            previous_depot = solution.routes[route_index].overnight_depot_id

    duties = person_duty_targets(solution)
    routes_by_person: dict[str, set[int]] = defaultdict(set)
    for duty in duties:
        routes_by_person[duty.person_id].add(duty.route_index)
    route_order = {
        route_index: (
            route.start_time,
            route.vehicle_id,
            route.day_index,
            route_index,
        )
        for route_index, route in enumerate(solution.routes)
    }
    ordered_routes_by_person = {
        person_id: tuple(sorted(route_indices, key=route_order.__getitem__))
        for person_id, route_indices in routes_by_person.items()
    }
    initial_depots_by_person = {
        item.person_id: item.initial_depot_id
        for item in instance.personnel_data.personnel
    }
    return _ContinuityIndex(
        duties,
        starting_depots,
        ordered_routes_by_person,
        initial_depots_by_person,
    )


def _staff_continuity_replacement_compatible(
    instance: LoadedVrpInstance,
    before: VrpSolution,
    after: VrpSolution,
    target: DutyTarget,
    continuity: _ContinuityIndex,
) -> bool:
    """Check the replacement's neighboring links and the removed duty's gap."""
    route_index = target.route_index
    before_route = before.routes[route_index]
    after_route = after.routes[route_index]
    if target.person_id in _people_on_route(after_route):
        if not _person_links_are_continuous(
            after,
            target.person_id,
            route_index,
            continuity,
        ):
            return False
    else:
        prior_routes = continuity.route_indices_by_person.get(target.person_id, ())
        if route_index not in prior_routes:
            return False
        prior_position = prior_routes.index(route_index)
        previous_index = (
            prior_routes[prior_position - 1] if prior_position > 0 else None
        )
        next_index = (
            prior_routes[prior_position + 1]
            if prior_position + 1 < len(prior_routes)
            else None
        )
        if next_index is not None:
            expected_depot = (
                continuity.initial_depots_by_person.get(target.person_id)
                if previous_index is None
                else before.routes[previous_index].overnight_depot_id
            )
            if continuity.starting_depots[next_index] != expected_depot:
                return False

    if target.role == 'driver':
        replacement_people = {after_route.driver_id}
    else:
        replacement_people = set()
        for before_stop, after_stop in zip(
            before_route.stops, after_route.stops, strict=True
        ):
            if type(before_stop) is ActivityStop and type(after_stop) is ActivityStop:
                replacement_people.update(
                    after_stop.team_member_ids - before_stop.team_member_ids
                )
    replacement_people.discard(target.person_id)
    return all(
        _person_links_are_continuous(after, person_id, route_index, continuity)
        for person_id in replacement_people
    )


def _people_on_route(route: VehicleDayRoute) -> set[str]:
    return {
        route.driver_id,
        *(
            person_id
            for stop in route.stops
            if type(stop) is ActivityStop
            for person_id in stop.team_member_ids
        ),
    }


def _person_links_are_continuous(
    solution: VrpSolution,
    person_id: str,
    target_route_index: int,
    continuity: _ContinuityIndex,
) -> bool:
    route_indices = continuity.route_indices_by_person.get(person_id, ())
    if target_route_index not in route_indices:
        route_indices = tuple(
            sorted(
                (*route_indices, target_route_index),
                key=lambda index: (
                    solution.routes[index].start_time,
                    solution.routes[index].vehicle_id,
                    solution.routes[index].day_index,
                    index,
                ),
            )
        )
    position = route_indices.index(target_route_index)
    route = solution.routes[target_route_index]
    previous_index = route_indices[position - 1] if position > 0 else None
    expected_depot = (
        continuity.initial_depots_by_person.get(person_id)
        if previous_index is None
        else solution.routes[previous_index].overnight_depot_id
    )
    if continuity.starting_depots[target_route_index] != expected_depot:
        return False
    if position + 1 < len(route_indices):
        next_index = route_indices[position + 1]
        if continuity.starting_depots[next_index] != route.overnight_depot_id:
            return False
    return True


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
    if finding.activity_ref is not None:
        return any(
            type(stop) is ActivityStop and stop.activity_ref == finding.activity_ref
            for stop in route.stops
        )
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
        and left.activity_ref == right.activity_ref
    )


def _tolerance(left: float, right: float) -> float:
    return 1e-9 + 1e-12 * max(abs(left), abs(right))
