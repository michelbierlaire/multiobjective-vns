"""Availability, personnel, and operating-rule table parsing.

Created: 2026-11-03T16:34:50+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    ContinuousDrivingLimitRecord,
    DailyDrivingLimitRecord,
    DailyWorkLimitRecord,
    ObjectiveEventTypeRecord,
    PersonnelAvailabilityRecord,
    PersonRuleLimitRecord,
    VehicleAvailabilityRecord,
    VehicleDrivingSkillRecord,
    VehicleRecord,
    VrpEventData,
    VrpInstanceSettings,
    VrpInterventionData,
    VrpObjectiveData,
    VrpPackageData,
    VrpPersonnelData,
    VrpWorkRuleData,
)
from multiobjective_vns.multiday_vrp.input_utils import (
    CsvRow,
)
from multiobjective_vns.multiday_vrp.input_validation import (
    _cell_error,
    _elapsed_time_field,
    _float_field,
    _foreign,
    _id_field,
    _int_field,
    _missing_file,
    _numeric_tolerance,
    _optional_float_field,
    _require_exact_set,
    _require_rule_rows,
    _table_error,
    _unique,
    _unique_rows,
)


def _parse_work_rules(
    tables: dict[str, tuple[CsvRow, ...]],
    paths: dict[str, Path | None],
    personnel: VrpPersonnelData,
    day_count: int,
    settings: VrpInstanceSettings,
) -> VrpWorkRuleData:
    people = {item.person_id for item in personnel.personnel}
    drivers = {item.person_id for item in personnel.personnel if item.authorized_driver}
    daily_work = tuple(
        DailyWorkLimitRecord(
            _id_field(row, 'person_id', paths['daily_work_limits'], row_number),
            _int_field(
                row,
                'day_index',
                paths['daily_work_limits'],
                row_number,
                minimum=1,
                maximum=day_count,
            ),
            _float_field(
                row,
                'max_work_time',
                paths['daily_work_limits'],
                row_number,
                minimum=0.0,
            ),
        )
        for row_number, row in enumerate(tables['daily_work_limits'], start=2)
    )
    _unique_rows(daily_work, paths['daily_work_limits'])
    _foreign(
        (item.person_id for item in daily_work),
        people,
        paths['daily_work_limits'],
        'person_id',
    )
    _require_rule_rows(
        daily_work,
        {(person_id, day) for person_id in people for day in range(1, day_count + 1)},
        'daily_work',
        settings,
        paths['daily_work_limits'],
        lambda item: (item.person_id, item.day_index),
    )

    daily_driving = tuple(
        DailyDrivingLimitRecord(
            _id_field(row, 'driver_id', paths['daily_driving_limits'], row_number),
            _int_field(
                row,
                'day_index',
                paths['daily_driving_limits'],
                row_number,
                minimum=1,
                maximum=day_count,
            ),
            _float_field(
                row,
                'max_driving_time',
                paths['daily_driving_limits'],
                row_number,
                minimum=0.0,
            ),
        )
        for row_number, row in enumerate(tables['daily_driving_limits'], start=2)
    )
    _unique_rows(daily_driving, paths['daily_driving_limits'])
    _foreign(
        (item.driver_id for item in daily_driving),
        drivers,
        paths['daily_driving_limits'],
        'driver_id',
    )
    _require_rule_rows(
        daily_driving,
        {(person_id, day) for person_id in drivers for day in range(1, day_count + 1)},
        'daily_driving',
        settings,
        paths['daily_driving_limits'],
        lambda item: (item.driver_id, item.day_index),
    )

    continuous = tuple(
        ContinuousDrivingLimitRecord(
            _id_field(row, 'driver_id', paths['continuous_driving_limits'], row_number),
            _float_field(
                row,
                'max_continuous_driving_time',
                paths['continuous_driving_limits'],
                row_number,
                minimum=0.0,
            ),
        )
        for row_number, row in enumerate(tables['continuous_driving_limits'], start=2)
    )
    _unique(
        (item.driver_id for item in continuous),
        paths['continuous_driving_limits'],
        'driver_id',
    )
    _foreign(
        (item.driver_id for item in continuous),
        drivers,
        paths['continuous_driving_limits'],
        'driver_id',
    )
    if 'continuous_driving' in settings.active_simple_rules:
        _require_exact_set(
            {item.driver_id for item in continuous},
            drivers,
            paths['continuous_driving_limits'],
            'driver_id',
        )
    elif continuous:
        raise _table_error(
            paths['continuous_driving_limits'],
            'rows require continuous_driving to be active',
        )

    expected_person_rule_ids: set[str] = set()
    if (
        'minimum_rest' in settings.active_simple_rules
        or 'daily_work' in settings.active_simple_rules
    ):
        expected_person_rule_ids.update(people)
    if 'continuous_driving' in settings.active_simple_rules:
        expected_person_rule_ids.update(drivers)
    person_rule_limits: list[PersonRuleLimitRecord] = []
    for row_number, row in enumerate(tables['person_rule_limits'], start=2):
        person_id = _id_field(row, 'person_id', paths['person_rule_limits'], row_number)
        _foreign((person_id,), people, paths['person_rule_limits'], 'person_id')
        minimum_rest_time = _optional_float_field(
            row,
            'minimum_rest_time',
            paths['person_rule_limits'],
            row_number,
            minimum=0.0,
            strict_minimum=True,
        )
        qualifying_break_minimum = _optional_float_field(
            row,
            'qualifying_break_minimum',
            paths['person_rule_limits'],
            row_number,
            minimum=0.0,
            strict_minimum=True,
        )
        requires_rest = 'minimum_rest' in settings.active_simple_rules or (
            'continuous_driving' in settings.active_simple_rules
            and person_id in drivers
        )
        requires_break = 'daily_work' in settings.active_simple_rules or (
            'continuous_driving' in settings.active_simple_rules
            and person_id in drivers
        )
        if requires_rest != (minimum_rest_time is not None):
            raise _cell_error(
                paths['person_rule_limits'],
                row_number,
                'minimum_rest_time',
                'field presence must match the active rest rules',
            )
        if requires_break:
            if qualifying_break_minimum is None:
                raise _cell_error(
                    paths['person_rule_limits'],
                    row_number,
                    'qualifying_break_minimum',
                    'required by the active break rules',
                )
        elif qualifying_break_minimum is not None:
            raise _cell_error(
                paths['person_rule_limits'],
                row_number,
                'qualifying_break_minimum',
                'not used by the active rules',
            )
        person_rule_limits.append(
            PersonRuleLimitRecord(
                person_id, minimum_rest_time, qualifying_break_minimum
            )
        )
    _unique(
        (item.person_id for item in person_rule_limits),
        paths['person_rule_limits'],
        'person_id',
    )
    _require_exact_set(
        {item.person_id for item in person_rule_limits},
        expected_person_rule_ids,
        paths['person_rule_limits'],
        'person_id',
    )
    return VrpWorkRuleData(
        daily_work, daily_driving, tuple(person_rule_limits), continuous
    )


def _parse_settings_and_event_objectives(
    tables: dict[str, tuple[CsvRow, ...]],
    paths: dict[str, Path | None],
    objective_data: VrpObjectiveData,
    event_data: VrpEventData,
) -> VrpEventData:
    rows = tuple(
        ObjectiveEventTypeRecord(
            _id_field(row, 'objective_id', paths['objective_event_types'], row_number),
            _id_field(row, 'event_type_id', paths['objective_event_types'], row_number),
        )
        for row_number, row in enumerate(tables['objective_event_types'], start=2)
    )
    _unique_rows(rows, paths['objective_event_types'])
    _foreign(
        (item.objective_id for item in rows),
        {item.objective_id for item in objective_data.objectives},
        paths['objective_event_types'],
        'objective_id',
    )
    _foreign(
        (item.event_type_id for item in rows),
        {item.event_type_id for item in event_data.event_types},
        paths['objective_event_types'],
        'event_type_id',
    )
    event_objectives = {
        item.objective_id
        for item in objective_data.objectives
        if item.base_measure_id in {'event_count', 'maximum_event_exposure'}
    }
    actual = {item.objective_id for item in rows}
    if event_objectives != actual:
        raise _table_error(
            paths['objective_event_types'],
            'each event-count objective requires event types and other objectives cannot declare them',
        )
    return VrpEventData(event_data.event_types, event_data.events, rows)


def _availability_records(
    rows: tuple[CsvRow, ...],
    path: Path | None,
    entity_column: str,
    entity_ids: set[str],
    origin: datetime,
    time_zone: str,
    duration_scale: float,
    horizon: float,
    *,
    omitted_means_full: bool,
    record_type: str,
) -> tuple[VehicleAvailabilityRecord, ...] | tuple[PersonnelAvailabilityRecord, ...]:
    if omitted_means_full:
        full_calendar = tuple(
            VehicleAvailabilityRecord(entity_id, 0.0, horizon)
            if record_type == 'vehicle'
            else PersonnelAvailabilityRecord(entity_id, 0.0, horizon)
            for entity_id in sorted(entity_ids)
        )
        if record_type == 'vehicle':
            return cast(tuple[VehicleAvailabilityRecord, ...], full_calendar)
        return cast(tuple[PersonnelAvailabilityRecord, ...], full_calendar)
    if path is None:
        raise AssertionError('availability path is required when not omitted')
    parsed: list[VehicleAvailabilityRecord | PersonnelAvailabilityRecord] = []
    intervals: dict[str, list[tuple[float, float]]] = {}
    for row_number, row in enumerate(rows, start=2):
        entity_id = _id_field(row, entity_column, path, row_number)
        if entity_id not in entity_ids:
            raise _cell_error(
                path, row_number, entity_column, f'unknown entity {entity_id!r}'
            )
        start = _elapsed_time_field(
            row,
            'start_time',
            path,
            row_number,
            origin,
            time_zone,
            duration_scale,
            horizon,
        )
        end = _elapsed_time_field(
            row,
            'end_time',
            path,
            row_number,
            origin,
            time_zone,
            duration_scale,
            horizon,
        )
        if start >= end:
            raise _cell_error(
                path, row_number, 'end_time', 'must be later than start_time'
            )
        intervals.setdefault(entity_id, []).append((start, end))
        parsed.append(
            VehicleAvailabilityRecord(entity_id, start, end)
            if record_type == 'vehicle'
            else PersonnelAvailabilityRecord(entity_id, start, end)
        )
    for entity_id, values in intervals.items():
        values.sort()
        if any(
            left[1] > right[0] + _numeric_tolerance(left[1], right[0])
            for left, right in zip(values, values[1:])
        ):
            raise _table_error(
                path, f'availability intervals overlap for {entity_id!r}'
            )
    if record_type == 'vehicle':
        return cast(tuple[VehicleAvailabilityRecord, ...], tuple(parsed))
    return cast(tuple[PersonnelAvailabilityRecord, ...], tuple(parsed))


def _validate_skills_and_assignments(
    vehicle_skills: tuple[VehicleDrivingSkillRecord, ...],
    personnel: VrpPersonnelData,
    paths: dict[str, Path | None],
    vehicles: tuple[VehicleRecord, ...],
    interventions: VrpInterventionData,
) -> None:
    del vehicles, interventions
    available_skill_ids = {item.skill_id for item in personnel.driving_skills}
    missing = {item.skill_id for item in vehicle_skills} - available_skill_ids
    if missing:
        raise _table_error(
            paths['driving_skills'],
            f'vehicle skill {min(missing)!r} is absent from driving-skills.csv',
        )


def _require_conditionally_named_files(
    paths: dict[str, Path | None],
    packages: VrpPackageData,
    interventions: VrpInterventionData,
    personnel: VrpPersonnelData,
    vehicle_skills: tuple[VehicleDrivingSkillRecord, ...],
    settings: VrpInstanceSettings,
    events: VrpEventData,
    work_rules: VrpWorkRuleData,
) -> None:
    requirements: list[tuple[bool, str]] = [
        (bool(packages.packages), 'package_time_windows'),
        (bool(packages.packages), 'vehicle_package_compatibility'),
        (
            any(item.max_transfers > 0 for item in packages.packages),
            'package_transfer_facilities',
        ),
        (bool(interventions.interventions), 'intervention_time_windows'),
        (bool(interventions.intervention_tools), 'intervention_tools'),
        (bool(interventions.intervention_tools), 'tools'),
        (bool(vehicle_skills), 'vehicle_driving_skills'),
        (
            bool(vehicle_skills) or bool(personnel.personnel_driving_skills),
            'driving_skills',
        ),
        (bool(personnel.personnel_driving_skills), 'personnel_driving_skills'),
        (
            bool(personnel.personnel_intervention_qualifications),
            'personnel_intervention_qualifications',
        ),
        (
            bool(personnel.personnel_intervention_qualifications)
            or bool(interventions.intervention_qualifications_required),
            'intervention_qualifications',
        ),
        (
            bool(interventions.intervention_qualifications_required),
            'intervention_qualifications_required',
        ),
        (bool(events.event_types), 'event_types'),
        (
            settings.service_coverage_mode == 'penalized_omission'
            and bool(packages.packages or interventions.interventions),
            'objective_omission_penalties',
        ),
        ('daily_work' in settings.active_simple_rules, 'daily_work_limits'),
        ('daily_driving' in settings.active_simple_rules, 'daily_driving_limits'),
        (
            'continuous_driving' in settings.active_simple_rules,
            'continuous_driving_limits',
        ),
    ]
    needs_person_rule_limits = bool(work_rules.person_rule_limits)
    requirements.append((needs_person_rule_limits, 'person_rule_limits'))
    if interventions.tools and not paths['tools']:
        raise _missing_file('tools')
    for required, key in requirements:
        if required and paths[key] is None:
            raise _missing_file(key)
    if paths['transfer_facilities'] is None and packages.package_transfer_facilities:
        raise _missing_file('transfer_facilities')
