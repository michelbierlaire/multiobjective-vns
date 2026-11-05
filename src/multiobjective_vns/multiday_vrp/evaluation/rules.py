"""Measure simple work, driving, break, and rest rules in one scenario.

Created: 2026-11-03T19:34:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import fsum

from multiobjective_vns.multiday_vrp.evaluation.records import (
    ResourceFindingKind,
)
from multiobjective_vns.multiday_vrp.extensions import (
    ViolationCategory,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.solution import ActivityStop, VrpSolution

__all__ = ['RuleMeasure', 'assess_simple_rules']

_TOL_ABSOLUTE = 1e-9
_TOL_RELATIVE = 1e-12


@dataclass(frozen=True, slots=True)
class RuleMeasure:
    """One simple-rule violation measure for one resource and scenario."""

    kind: ResourceFindingKind
    penalty_category: ViolationCategory
    rule_id: str
    resource_id: str
    vehicle_id: str | None
    day_index: int | None
    measure: float


def assess_simple_rules(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    scenario: VrpScenarioEvaluation,
) -> tuple[RuleMeasure, ...]:
    """Calculate all activated simple-rule measures for one scenario.

    :param instance: Validated instance and selected simple-rule set.
    :param solution: Structurally valid plan aligned with ``scenario``.
    :param scenario: One complete projected scenario trace.
    :returns: Rule measures in documented rule, person, and route order.
    """
    active = instance.settings.active_simple_rules
    if not active:
        return ()

    route_by_key = {
        (route.vehicle_id, route.day_index): route for route in solution.routes
    }
    duties_by_person: dict[str, list[tuple[float, float, str, int]]] = defaultdict(list)
    work_by_person_day: dict[tuple[str, int], list[float]] = defaultdict(list)
    driving_by_driver_day: dict[tuple[str, int], list[float]] = defaultdict(list)

    break_thresholds = {
        item.person_id: item.qualifying_break_minimum
        for item in instance.work_rule_data.person_rule_limits
    }
    rest_thresholds = {
        item.person_id: item.minimum_rest_time
        for item in instance.work_rule_data.person_rule_limits
    }

    for day in scenario.vehicle_days:
        route = route_by_key[(day.vehicle_id, day.day_index)]
        crew = {route.driver_id}
        crew.update(
            person_id
            for stop in route.stops
            if type(stop) is ActivityStop
            for person_id in stop.team_member_ids
        )
        for person_id in crew:
            duties_by_person[person_id].append(
                (
                    day.duty_start_time,
                    day.duty_end_time,
                    route.vehicle_id,
                    route.day_index,
                )
            )
            threshold = break_thresholds.get(person_id)
            qualifying_break = fsum(
                stop.break_duration
                for stop in route.stops
                if threshold is not None
                and stop.break_duration
                >= threshold - _tolerance(stop.break_duration, threshold)
            )
            work = max(
                0.0,
                day.duty_end_time - day.duty_start_time - qualifying_break,
            )
            work_by_person_day[(person_id, day.day_index)].append(work)

        driving = fsum(
            traversal.travel_time
            for leg in day.legs
            for traversal in leg.arc_traversals
        )
        driving_by_driver_day[(route.driver_id, day.day_index)].append(driving)

    result: list[RuleMeasure] = []
    if 'daily_work' in active:
        for work_limit in instance.work_rule_data.daily_work_limits:
            work = fsum(
                work_by_person_day.get((work_limit.person_id, work_limit.day_index), ())
            )
            measure = _upper_violation(work, work_limit.max_work_time)
            result.append(
                RuleMeasure(
                    'driver_work',
                    'simple_rule',
                    'daily_work',
                    f'{work_limit.person_id}:daily_work:{work_limit.day_index}',
                    None,
                    work_limit.day_index,
                    measure,
                )
            )

    if 'daily_driving' in active:
        for driving_limit in instance.work_rule_data.daily_driving_limits:
            driving = fsum(
                driving_by_driver_day.get(
                    (driving_limit.driver_id, driving_limit.day_index), ()
                )
            )
            measure = _upper_violation(driving, driving_limit.max_driving_time)
            result.append(
                RuleMeasure(
                    'driving_time',
                    'simple_rule',
                    'daily_driving',
                    f'{driving_limit.driver_id}:daily_driving:{driving_limit.day_index}',
                    None,
                    driving_limit.day_index,
                    measure,
                )
            )

    if 'minimum_rest' in active:
        for person in instance.personnel_data.personnel:
            minimum_rest = rest_thresholds.get(person.person_id)
            if minimum_rest is None:
                continue
            person_duties = sorted(duties_by_person.get(person.person_id, ()))
            for previous, following in zip(
                person_duties, person_duties[1:], strict=False
            ):
                gap = max(0.0, following[0] - previous[1])
                measure = _lower_violation(gap, minimum_rest)
                result.append(
                    RuleMeasure(
                        'rest',
                        'simple_rule',
                        'minimum_rest',
                        f'{person.person_id}:minimum_rest:{previous[3]}:{following[3]}',
                        following[2],
                        following[3],
                        measure,
                    )
                )

    if 'continuous_driving' in active:
        limits = instance.work_rule_data.continuous_driving_limits
        for continuous_limit in limits:
            driver_id = continuous_limit.driver_id
            break_threshold = break_thresholds.get(driver_id)
            minimum_rest = rest_thresholds.get(driver_id)
            driver_duties = sorted(
                (
                    day
                    for day in scenario.vehicle_days
                    if route_by_key[(day.vehicle_id, day.day_index)].driver_id
                    == driver_id
                ),
                key=lambda item: (
                    item.duty_start_time,
                    item.vehicle_id,
                    item.day_index,
                ),
            )
            blocks: list[tuple[float, int | None]] = []
            current_drive = 0.0
            previous_end: float | None = None
            previous_day: int | None = None
            for day in driver_duties:
                if (
                    previous_end is not None
                    and minimum_rest is not None
                    and max(0.0, day.duty_start_time - previous_end)
                    >= minimum_rest
                    - _tolerance(day.duty_start_time - previous_end, minimum_rest)
                ):
                    blocks.append((current_drive, previous_day))
                    current_drive = 0.0
                route = route_by_key[(day.vehicle_id, day.day_index)]
                for leg_index, leg in enumerate(day.legs):
                    current_drive = fsum(
                        (
                            current_drive,
                            *(item.travel_time for item in leg.arc_traversals),
                        )
                    )
                    if leg_index < len(day.stops):
                        stop = route.stops[leg_index]
                        if (
                            break_threshold is not None
                            and stop.break_duration
                            >= break_threshold
                            - _tolerance(stop.break_duration, break_threshold)
                        ):
                            blocks.append((current_drive, day.day_index))
                            current_drive = 0.0
                previous_end = day.duty_end_time
                previous_day = day.day_index
            if driver_duties:
                blocks.append((current_drive, previous_day))
            if not blocks:
                blocks.append((0.0, None))
            for block_index, (duration, day_index) in enumerate(blocks, start=1):
                measure = _upper_violation(
                    duration, continuous_limit.max_continuous_driving_time
                )
                result.append(
                    RuleMeasure(
                        'driving_time',
                        'simple_rule',
                        'continuous_driving',
                        f'{driver_id}:continuous_driving:{block_index}',
                        None,
                        day_index,
                        measure,
                    )
                )

    return tuple(result)


def _upper_violation(value: float, bound: float) -> float:
    if value <= bound + _tolerance(value, bound):
        return 0.0
    return value - bound


def _lower_violation(value: float, bound: float) -> float:
    if value >= bound - _tolerance(value, bound):
        return 0.0
    return bound - value


def _tolerance(left: float, right: float) -> float:
    return _TOL_ABSOLUTE + _TOL_RELATIVE * max(abs(left), abs(right))
