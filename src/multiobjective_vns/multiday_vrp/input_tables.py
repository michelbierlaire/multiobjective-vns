"""Coordinate parsing of normalized VRP table groups.

Created: 2026-11-03T16:34:50+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast

from multiobjective_vns.multiday_vrp.input_entities import (
    _parse_interventions,
    _parse_packages,
    _parse_personnel,
)
from multiobjective_vns.multiday_vrp.input_records import (
    ArcRecord,
    DepotRecord,
    NodeRecord,
    ObjectiveOmissionPenaltyRecord,
    TransferFacilityRecord,
    VehicleAvailabilityRecord,
    VehicleDrivingSkillRecord,
    VehicleOvernightDepotRecord,
    VehicleRecord,
    VrpActivityRelationData,
    VrpEventData,
    VrpFleetData,
    VrpInstanceSettings,
    VrpInterventionData,
    VrpNetworkData,
    VrpPackageData,
    VrpPersonnelData,
    VrpWorkRuleData,
)
from multiobjective_vns.multiday_vrp.input_relations import (
    _parse_events,
    _parse_omission_penalties,
    _parse_relations,
)
from multiobjective_vns.multiday_vrp.input_rules import (
    _availability_records,
    _parse_work_rules,
    _require_conditionally_named_files,
    _validate_skills_and_assignments,
)
from multiobjective_vns.multiday_vrp.input_utils import (
    CsvRow,
)
from multiobjective_vns.multiday_vrp.input_validation import (
    _float_field,
    _foreign,
    _id_field,
    _ids,
    _int_field,
    _require_each,
    _table_error,
    _unique,
    _unique_rows,
)


def _parse_tables(
    *,
    tables: dict[str, tuple[CsvRow, ...]],
    paths: dict[str, Path | None],
    settings: VrpInstanceSettings,
    scenario_bank: Any,
    origin: datetime,
    duration_scale: float,
    horizon: float,
) -> tuple[
    VrpNetworkData,
    VrpFleetData,
    VrpPackageData,
    VrpInterventionData,
    VrpPersonnelData,
    VrpActivityRelationData,
    VrpWorkRuleData,
    VrpEventData,
    tuple[ObjectiveOmissionPenaltyRecord, ...],
]:
    node_ids = _ids(tables['nodes'], paths['nodes'], 'node_id')
    if not node_ids:
        raise _table_error(paths['nodes'], 'node catalog must not be empty')
    nodes = tuple(NodeRecord(value) for value in node_ids)
    arc_records: list[ArcRecord] = []
    for row_number, row in enumerate(tables['arcs'], start=2):
        arc_records.append(
            ArcRecord(
                _id_field(row, 'arc_id', paths['arcs'], row_number),
                _id_field(row, 'tail_node_id', paths['arcs'], row_number),
                _id_field(row, 'head_node_id', paths['arcs'], row_number),
            )
        )
    _unique((item.arc_id for item in arc_records), paths['arcs'], 'arc_id')
    _foreign(
        (item.tail_node_id for item in arc_records),
        set(node_ids),
        paths['arcs'],
        'tail_node_id',
    )
    _foreign(
        (item.head_node_id for item in arc_records),
        set(node_ids),
        paths['arcs'],
        'head_node_id',
    )
    arcs = tuple(arc_records)

    depots = tuple(
        DepotRecord(
            _id_field(row, 'depot_id', paths['depots'], row_number),
            _id_field(row, 'node_id', paths['depots'], row_number),
        )
        for row_number, row in enumerate(tables['depots'], start=2)
    )
    _unique((item.depot_id for item in depots), paths['depots'], 'depot_id')
    _foreign(
        (item.node_id for item in depots), set(node_ids), paths['depots'], 'node_id'
    )
    if not depots:
        raise _table_error(paths['depots'], 'depot catalog must not be empty')
    depot_ids = {item.depot_id for item in depots}

    facilities = tuple(
        TransferFacilityRecord(
            _id_field(row, 'node_id', paths['transfer_facilities'], row_number),
            _float_field(
                row,
                'storage_capacity',
                paths['transfer_facilities'],
                row_number,
                minimum=0.0,
            ),
        )
        for row_number, row in enumerate(tables['transfer_facilities'], start=2)
    )
    _unique(
        (item.node_id for item in facilities), paths['transfer_facilities'], 'node_id'
    )
    _foreign(
        (item.node_id for item in facilities),
        set(node_ids),
        paths['transfer_facilities'],
        'node_id',
    )
    network = VrpNetworkData(nodes, arcs, depots, facilities)

    vehicles = tuple(
        VehicleRecord(
            _id_field(row, 'vehicle_id', paths['vehicles'], row_number),
            _float_field(
                row, 'cargo_capacity', paths['vehicles'], row_number, minimum=0.0
            ),
            _int_field(row, 'seat_capacity', paths['vehicles'], row_number, minimum=1),
            _id_field(row, 'initial_depot_id', paths['vehicles'], row_number),
            _int_field(
                row, 'max_tours_per_day', paths['vehicles'], row_number, minimum=1
            ),
        )
        for row_number, row in enumerate(tables['vehicles'], start=2)
    )
    _unique((item.vehicle_id for item in vehicles), paths['vehicles'], 'vehicle_id')
    if not vehicles:
        raise _table_error(paths['vehicles'], 'vehicle catalog must not be empty')
    _foreign(
        (item.initial_depot_id for item in vehicles),
        depot_ids,
        paths['vehicles'],
        'initial_depot_id',
    )
    vehicle_ids = {item.vehicle_id for item in vehicles}
    overnight = tuple(
        VehicleOvernightDepotRecord(
            _id_field(row, 'vehicle_id', paths['vehicle_overnight_depots'], row_number),
            _id_field(row, 'depot_id', paths['vehicle_overnight_depots'], row_number),
        )
        for row_number, row in enumerate(tables['vehicle_overnight_depots'], start=2)
    )
    _unique_rows(overnight, paths['vehicle_overnight_depots'])
    _foreign(
        (item.vehicle_id for item in overnight),
        vehicle_ids,
        paths['vehicle_overnight_depots'],
        'vehicle_id',
    )
    _foreign(
        (item.depot_id for item in overnight),
        depot_ids,
        paths['vehicle_overnight_depots'],
        'depot_id',
    )
    _require_each(
        overnight, vehicles, 'vehicle_id', paths['vehicle_overnight_depots'], 'vehicle'
    )
    overnight_by_vehicle: dict[str, set[str]] = {}
    for item in overnight:
        overnight_by_vehicle.setdefault(item.vehicle_id, set()).add(item.depot_id)
    for vehicle in vehicles:
        if vehicle.initial_depot_id not in overnight_by_vehicle.get(
            vehicle.vehicle_id, set()
        ):
            raise _table_error(
                paths['vehicle_overnight_depots'],
                f'initial depot for {vehicle.vehicle_id!r} must be overnight-eligible',
            )

    vehicle_skills = tuple(
        VehicleDrivingSkillRecord(
            _id_field(row, 'vehicle_id', paths['vehicle_driving_skills'], row_number),
            _id_field(row, 'skill_id', paths['vehicle_driving_skills'], row_number),
        )
        for row_number, row in enumerate(tables['vehicle_driving_skills'], start=2)
    )
    _unique_rows(vehicle_skills, paths['vehicle_driving_skills'])
    _foreign(
        (item.vehicle_id for item in vehicle_skills),
        vehicle_ids,
        paths['vehicle_driving_skills'],
        'vehicle_id',
    )
    vehicle_availability = cast(
        tuple[VehicleAvailabilityRecord, ...],
        _availability_records(
            tables['vehicle_availability'],
            paths['vehicle_availability'],
            'vehicle_id',
            vehicle_ids,
            origin,
            scenario_bank.time_zone,
            duration_scale,
            horizon,
            omitted_means_full=paths['vehicle_availability'] is None,
            record_type='vehicle',
        ),
    )

    package_data = _parse_packages(
        tables,
        paths,
        node_ids,
        vehicle_ids,
        facilities,
        vehicles,
        origin,
        scenario_bank.time_zone,
        duration_scale,
        horizon,
    )
    intervention_data = _parse_interventions(
        tables,
        paths,
        node_ids,
        len(scenario_bank.planning_dates),
        scenario_bank.day_boundaries,
        origin,
        scenario_bank.time_zone,
        duration_scale,
        horizon,
    )
    personnel_data = _parse_personnel(
        tables,
        paths,
        depot_ids,
        vehicle_skills,
        origin,
        scenario_bank.time_zone,
        duration_scale,
        horizon,
        settings,
    )
    _validate_skills_and_assignments(
        vehicle_skills, personnel_data, paths, vehicles, intervention_data
    )
    fleet = VrpFleetData(vehicles, overnight, vehicle_skills, vehicle_availability)
    relations = _parse_relations(
        tables, paths, package_data, intervention_data, settings
    )
    work_rules = _parse_work_rules(
        tables, paths, personnel_data, len(scenario_bank.planning_dates), settings
    )
    event_data = _parse_events(tables, paths, node_ids, arcs)
    omission_penalties = _parse_omission_penalties(
        tables, paths, settings, package_data, intervention_data
    )
    _require_conditionally_named_files(
        paths,
        package_data,
        intervention_data,
        personnel_data,
        vehicle_skills,
        settings,
        event_data,
        work_rules,
    )
    return (
        network,
        fleet,
        package_data,
        intervention_data,
        personnel_data,
        relations,
        work_rules,
        event_data,
        omission_penalties,
    )
