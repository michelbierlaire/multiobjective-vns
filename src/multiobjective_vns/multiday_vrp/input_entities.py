"""Package, intervention, and personnel table parsing.

Created: 2026-11-03T16:34:50+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    DrivingSkillRecord,
    InterventionQualificationRecord,
    InterventionQualificationRequiredRecord,
    InterventionRecord,
    InterventionTimeWindowRecord,
    InterventionToolRecord,
    PackageRecord,
    PackageTimeWindowRecord,
    PackageTransferFacilityRecord,
    PersonnelAvailabilityRecord,
    PersonnelDrivingSkillRecord,
    PersonnelInterventionQualificationRecord,
    PersonRecord,
    ToolRecord,
    TransferFacilityRecord,
    VehicleDrivingSkillRecord,
    VehiclePackageCompatibilityRecord,
    VehicleRecord,
    VrpInstanceSettings,
    VrpInterventionData,
    VrpPackageData,
    VrpPersonnelData,
)
from multiobjective_vns.multiday_vrp.input_rules import _availability_records
from multiobjective_vns.multiday_vrp.input_utils import (
    CsvRow,
)
from multiobjective_vns.multiday_vrp.input_validation import (
    _bool_field,
    _cell_error,
    _elapsed_time_field,
    _filename,
    _float_field,
    _foreign,
    _id_field,
    _id_value,
    _int_field,
    _missing_file,
    _numeric_tolerance,
    _table_error,
    _time_field,
    _unique,
    _unique_rows,
)


def _parse_packages(
    tables: dict[str, tuple[CsvRow, ...]],
    paths: dict[str, Path | None],
    node_ids: tuple[str, ...],
    vehicle_ids: set[str],
    facilities: tuple[TransferFacilityRecord, ...],
    vehicles: tuple[VehicleRecord, ...],
    origin: datetime,
    time_zone: str,
    duration_scale: float,
    horizon: float,
) -> VrpPackageData:
    packages = tuple(
        PackageRecord(
            _id_field(row, 'package_id', paths['packages'], row_number),
            _id_field(row, 'pickup_node_id', paths['packages'], row_number),
            _id_field(row, 'delivery_node_id', paths['packages'], row_number),
            _float_field(row, 'load', paths['packages'], row_number, minimum=0.0),
            _float_field(
                row,
                'pickup_service_duration',
                paths['packages'],
                row_number,
                minimum=0.0,
            ),
            _float_field(
                row,
                'delivery_service_duration',
                paths['packages'],
                row_number,
                minimum=0.0,
            ),
            _int_field(row, 'max_transfers', paths['packages'], row_number, minimum=0),
            _float_field(
                row,
                'transfer_out_service_duration',
                paths['packages'],
                row_number,
                minimum=0.0,
            ),
            _float_field(
                row,
                'transfer_in_service_duration',
                paths['packages'],
                row_number,
                minimum=0.0,
            ),
        )
        for row_number, row in enumerate(tables['packages'], start=2)
    )
    _unique((item.package_id for item in packages), paths['packages'], 'package_id')
    package_ids = {item.package_id for item in packages}
    _foreign(
        (item.pickup_node_id for item in packages),
        set(node_ids),
        paths['packages'],
        'pickup_node_id',
    )
    _foreign(
        (item.delivery_node_id for item in packages),
        set(node_ids),
        paths['packages'],
        'delivery_node_id',
    )
    facilities_set = {item.node_id for item in facilities}
    for package in packages:
        if package.max_transfers == 0 and (
            package.transfer_out_service_duration != 0.0
            or package.transfer_in_service_duration != 0.0
        ):
            raise _table_error(
                paths['packages'],
                f'transfer durations for {package.package_id!r} must be zero when max_transfers is zero',
            )

    windows: list[PackageTimeWindowRecord] = []
    for row_number, row in enumerate(tables['package_time_windows'], start=2):
        package_id = _id_field(
            row, 'package_id', paths['package_time_windows'], row_number
        )
        activity = _id_field(row, 'activity', paths['package_time_windows'], row_number)
        if activity not in {'pickup', 'delivery'}:
            raise _cell_error(
                paths['package_time_windows'],
                row_number,
                'activity',
                "must be 'pickup' or 'delivery'",
            )
        window_index = _int_field(
            row, 'window_index', paths['package_time_windows'], row_number, minimum=1
        )
        start = _time_field(
            row,
            'start_time',
            paths['package_time_windows'],
            row_number,
            origin,
            time_zone,
            duration_scale,
            horizon,
        )
        end = _time_field(
            row,
            'end_time',
            paths['package_time_windows'],
            row_number,
            origin,
            time_zone,
            duration_scale,
            horizon,
        )
        if start > end + _numeric_tolerance(start, end):
            raise _cell_error(
                paths['package_time_windows'],
                row_number,
                'end_time',
                'must be at or after start_time',
            )
        windows.append(
            PackageTimeWindowRecord(package_id, activity, window_index, start, end)
        )
    _foreign(
        (item.package_id for item in windows),
        package_ids,
        paths['package_time_windows'],
        'package_id',
    )
    _unique_rows(windows, paths['package_time_windows'])
    for package_id in package_ids:
        for activity in ('pickup', 'delivery'):
            if not any(
                item.package_id == package_id and item.activity == activity
                for item in windows
            ):
                raise _table_error(
                    paths['package_time_windows'],
                    f'{package_id!r} requires at least one {activity} window',
                )

    compatibility = tuple(
        VehiclePackageCompatibilityRecord(
            _id_field(
                row, 'vehicle_id', paths['vehicle_package_compatibility'], row_number
            ),
            _id_field(
                row, 'package_id', paths['vehicle_package_compatibility'], row_number
            ),
            _bool_field(
                row, 'compatible', paths['vehicle_package_compatibility'], row_number
            ),
        )
        for row_number, row in enumerate(
            tables['vehicle_package_compatibility'], start=2
        )
    )
    _unique_rows(compatibility, paths['vehicle_package_compatibility'])
    _foreign(
        (item.vehicle_id for item in compatibility),
        vehicle_ids,
        paths['vehicle_package_compatibility'],
        'vehicle_id',
    )
    _foreign(
        (item.package_id for item in compatibility),
        package_ids,
        paths['vehicle_package_compatibility'],
        'package_id',
    )
    expected_compatibility = {
        (vehicle_id, package_id)
        for vehicle_id in vehicle_ids
        for package_id in package_ids
    }
    actual_compatibility = {
        (item.vehicle_id, item.package_id) for item in compatibility
    }
    if expected_compatibility != actual_compatibility:
        raise _table_error(
            paths['vehicle_package_compatibility'],
            'must contain exactly one row for every vehicle-package pair',
        )

    transfers = tuple(
        PackageTransferFacilityRecord(
            _id_field(
                row, 'package_id', paths['package_transfer_facilities'], row_number
            ),
            _id_field(row, 'node_id', paths['package_transfer_facilities'], row_number),
        )
        for row_number, row in enumerate(tables['package_transfer_facilities'], start=2)
    )
    _unique_rows(transfers, paths['package_transfer_facilities'])
    _foreign(
        (item.package_id for item in transfers),
        package_ids,
        paths['package_transfer_facilities'],
        'package_id',
    )
    _foreign(
        (item.node_id for item in transfers),
        facilities_set,
        paths['package_transfer_facilities'],
        'node_id',
    )
    transfer_nodes_by_package: dict[str, set[str]] = {}
    for item in transfers:
        transfer_nodes_by_package.setdefault(item.package_id, set()).add(item.node_id)
    for package in packages:
        allowed = transfer_nodes_by_package.get(package.package_id, set())
        if package.max_transfers > 0 and not allowed:
            raise _table_error(
                paths['package_transfer_facilities'],
                f'{package.package_id!r} needs an eligible transfer facility',
            )
        if package.max_transfers == 0 and allowed:
            raise _table_error(
                paths['package_transfer_facilities'],
                f'{package.package_id!r} cannot list transfer facilities when max_transfers is zero',
            )

    if packages and paths['package_time_windows'] is None:
        raise _missing_file('package_time_windows')
    if packages and paths['vehicle_package_compatibility'] is None:
        raise _missing_file('vehicle_package_compatibility')
    if (
        any(item.max_transfers > 0 for item in packages)
        and paths['package_transfer_facilities'] is None
    ):
        raise _missing_file('package_transfer_facilities')
    # A listed transfer facility requires its explicit catalog even when no transfer is enabled.
    if facilities and paths['transfer_facilities'] is None:
        raise _missing_file('transfer_facilities')
    return VrpPackageData(packages, tuple(windows), compatibility, transfers)


def _parse_interventions(
    tables: dict[str, tuple[CsvRow, ...]],
    paths: dict[str, Path | None],
    node_ids: tuple[str, ...],
    day_count: int,
    day_boundaries: tuple[tuple[float, ...], ...],
    origin: datetime,
    time_zone: str,
    duration_scale: float,
    horizon: float,
) -> VrpInterventionData:
    interventions = tuple(
        InterventionRecord(
            _id_field(row, 'intervention_id', paths['interventions'], row_number),
            _id_field(row, 'node_id', paths['interventions'], row_number),
            _float_field(
                row, 'service_duration', paths['interventions'], row_number, minimum=0.0
            ),
            _int_field(
                row, 'minimum_team_size', paths['interventions'], row_number, minimum=1
            ),
        )
        for row_number, row in enumerate(tables['interventions'], start=2)
    )
    _unique(
        (item.intervention_id for item in interventions),
        paths['interventions'],
        'intervention_id',
    )
    intervention_ids = {item.intervention_id for item in interventions}
    _foreign(
        (item.node_id for item in interventions),
        set(node_ids),
        paths['interventions'],
        'node_id',
    )
    windows: list[InterventionTimeWindowRecord] = []
    for row_number, row in enumerate(tables['intervention_time_windows'], start=2):
        intervention_id = _id_field(
            row, 'intervention_id', paths['intervention_time_windows'], row_number
        )
        day_index = _int_field(
            row,
            'day_index',
            paths['intervention_time_windows'],
            row_number,
            minimum=1,
            maximum=day_count,
        )
        window_index = _int_field(
            row,
            'window_index',
            paths['intervention_time_windows'],
            row_number,
            minimum=1,
        )
        start = _elapsed_time_field(
            row,
            'start_time',
            paths['intervention_time_windows'],
            row_number,
            origin,
            time_zone,
            duration_scale,
            horizon,
        )
        end = _elapsed_time_field(
            row,
            'end_time',
            paths['intervention_time_windows'],
            row_number,
            origin,
            time_zone,
            duration_scale,
            horizon,
        )
        lower, upper = (
            day_boundaries[day_index - 1][0],
            day_boundaries[day_index - 1][-1],
        )
        if (
            start < lower - _numeric_tolerance(start, lower)
            or end > upper + _numeric_tolerance(end, upper)
            or start > end
        ):
            raise _cell_error(
                paths['intervention_time_windows'],
                row_number,
                'start_time',
                'window must be ordered and lie within its planning day',
            )
        windows.append(
            InterventionTimeWindowRecord(
                intervention_id, day_index, window_index, start, end
            )
        )
    _foreign(
        (item.intervention_id for item in windows),
        intervention_ids,
        paths['intervention_time_windows'],
        'intervention_id',
    )
    _unique_rows(windows, paths['intervention_time_windows'])
    for intervention_id in intervention_ids:
        if not any(item.intervention_id == intervention_id for item in windows):
            raise _table_error(
                paths['intervention_time_windows'],
                f'{intervention_id!r} needs at least one eligible day/window',
            )

    tools = tuple(
        ToolRecord(
            _id_field(row, 'tool_id', paths['tools'], row_number),
            _float_field(
                row, 'capacity_demand', paths['tools'], row_number, minimum=0.0
            ),
        )
        for row_number, row in enumerate(tables['tools'], start=2)
    )
    _unique((item.tool_id for item in tools), paths['tools'], 'tool_id')
    tool_ids = {item.tool_id for item in tools}
    intervention_tools = tuple(
        InterventionToolRecord(
            _id_field(row, 'intervention_id', paths['intervention_tools'], row_number),
            _id_field(row, 'tool_id', paths['intervention_tools'], row_number),
        )
        for row_number, row in enumerate(tables['intervention_tools'], start=2)
    )
    _unique_rows(intervention_tools, paths['intervention_tools'])
    _foreign(
        (item.intervention_id for item in intervention_tools),
        intervention_ids,
        paths['intervention_tools'],
        'intervention_id',
    )
    _foreign(
        (item.tool_id for item in intervention_tools),
        tool_ids,
        paths['intervention_tools'],
        'tool_id',
    )
    quals = tuple(
        InterventionQualificationRecord(
            _id_field(
                row,
                'qualification_id',
                paths['intervention_qualifications'],
                row_number,
            )
        )
        for row_number, row in enumerate(tables['intervention_qualifications'], start=2)
    )
    _unique(
        (item.qualification_id for item in quals),
        paths['intervention_qualifications'],
        'qualification_id',
    )
    required_quals = tuple(
        InterventionQualificationRequiredRecord(
            _id_field(
                row,
                'intervention_id',
                paths['intervention_qualifications_required'],
                row_number,
            ),
            _id_field(
                row,
                'qualification_id',
                paths['intervention_qualifications_required'],
                row_number,
            ),
        )
        for row_number, row in enumerate(
            tables['intervention_qualifications_required'], start=2
        )
    )
    _unique_rows(required_quals, paths['intervention_qualifications_required'])
    _foreign(
        (item.intervention_id for item in required_quals),
        intervention_ids,
        paths['intervention_qualifications_required'],
        'intervention_id',
    )
    _foreign(
        (item.qualification_id for item in required_quals),
        {item.qualification_id for item in quals},
        paths['intervention_qualifications_required'],
        'qualification_id',
    )
    if interventions and paths['intervention_time_windows'] is None:
        raise _missing_file('intervention_time_windows')
    return VrpInterventionData(
        interventions, tuple(windows), tools, intervention_tools, required_quals
    )


def _parse_personnel(
    tables: dict[str, tuple[CsvRow, ...]],
    paths: dict[str, Path | None],
    depot_ids: set[str],
    vehicle_skills: tuple[VehicleDrivingSkillRecord, ...],
    origin: datetime,
    time_zone: str,
    duration_scale: float,
    horizon: float,
    settings: VrpInstanceSettings,
) -> VrpPersonnelData:
    people: list[PersonRecord] = []
    for row_number, row in enumerate(tables['personnel'], start=2):
        person_id = _id_field(row, 'person_id', paths['personnel'], row_number)
        authorized = _bool_field(
            row, 'authorized_driver', paths['personnel'], row_number
        )
        initial_text = row['initial_depot_id']
        if settings.staff_depot_mode == 'available_at_assignment':
            if initial_text:
                raise _cell_error(
                    paths['personnel'],
                    row_number,
                    'initial_depot_id',
                    'must be empty in available_at_assignment mode',
                )
            initial_depot_id = None
        else:
            initial_depot_id = _id_value(
                initial_text,
                _filename(paths['personnel']),
                row_number,
                'initial_depot_id',
            )
            if initial_depot_id not in depot_ids:
                raise _cell_error(
                    paths['personnel'],
                    row_number,
                    'initial_depot_id',
                    f'unknown depot {initial_depot_id!r}',
                )
        people.append(PersonRecord(person_id, authorized, initial_depot_id))
    _unique((item.person_id for item in people), paths['personnel'], 'person_id')
    if not people:
        raise _table_error(paths['personnel'], 'personnel catalog must not be empty')
    drivers = {item.person_id for item in people if item.authorized_driver}
    if not drivers:
        raise _table_error(
            paths['personnel'], 'at least one authorized driver is required'
        )
    person_ids = {item.person_id for item in people}

    skill_catalog = tuple(
        DrivingSkillRecord(
            _id_field(row, 'skill_id', paths['driving_skills'], row_number)
        )
        for row_number, row in enumerate(tables['driving_skills'], start=2)
    )
    _unique(
        (item.skill_id for item in skill_catalog), paths['driving_skills'], 'skill_id'
    )
    all_skill_ids = {item.skill_id for item in skill_catalog}
    personnel_skills = tuple(
        PersonnelDrivingSkillRecord(
            _id_field(row, 'person_id', paths['personnel_driving_skills'], row_number),
            _id_field(row, 'skill_id', paths['personnel_driving_skills'], row_number),
        )
        for row_number, row in enumerate(tables['personnel_driving_skills'], start=2)
    )
    _unique_rows(personnel_skills, paths['personnel_driving_skills'])
    _foreign(
        (item.person_id for item in personnel_skills),
        person_ids,
        paths['personnel_driving_skills'],
        'person_id',
    )
    _foreign(
        (item.skill_id for item in personnel_skills),
        all_skill_ids,
        paths['personnel_driving_skills'],
        'skill_id',
    )
    if any(item.person_id not in drivers for item in personnel_skills):
        raise _table_error(
            paths['personnel_driving_skills'],
            'only authorized drivers may have driving skills',
        )
    if any(item.skill_id not in all_skill_ids for item in vehicle_skills):
        raise _table_error(
            paths['driving_skills'],
            'vehicle skill reference is missing from driving-skills.csv',
        )

    qualification_catalog = tuple(
        InterventionQualificationRecord(
            _id_field(
                row,
                'qualification_id',
                paths['intervention_qualifications'],
                row_number,
            )
        )
        for row_number, row in enumerate(tables['intervention_qualifications'], start=2)
    )
    qualification_ids = {item.qualification_id for item in qualification_catalog}
    personnel_qualifications = tuple(
        PersonnelInterventionQualificationRecord(
            _id_field(
                row,
                'person_id',
                paths['personnel_intervention_qualifications'],
                row_number,
            ),
            _id_field(
                row,
                'qualification_id',
                paths['personnel_intervention_qualifications'],
                row_number,
            ),
        )
        for row_number, row in enumerate(
            tables['personnel_intervention_qualifications'], start=2
        )
    )
    _unique_rows(
        personnel_qualifications, paths['personnel_intervention_qualifications']
    )
    _foreign(
        (item.person_id for item in personnel_qualifications),
        person_ids,
        paths['personnel_intervention_qualifications'],
        'person_id',
    )
    _foreign(
        (item.qualification_id for item in personnel_qualifications),
        qualification_ids,
        paths['personnel_intervention_qualifications'],
        'qualification_id',
    )
    availability = cast(
        tuple[PersonnelAvailabilityRecord, ...],
        _availability_records(
            tables['personnel_availability'],
            paths['personnel_availability'],
            'person_id',
            person_ids,
            origin,
            time_zone,
            duration_scale,
            horizon,
            omitted_means_full=paths['personnel_availability'] is None,
            record_type='person',
        ),
    )
    return VrpPersonnelData(
        tuple(people),
        skill_catalog,
        qualification_catalog,
        personnel_skills,
        personnel_qualifications,
        availability,
    )
