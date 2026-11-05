"""Activity relationships, events, and omission table parsing.

Created: 2026-11-03T16:34:50+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path

from multiobjective_vns.multiday_vrp.input_records import (
    ArcRecord,
    CoLocationRecord,
    EventRecord,
    EventTypeRecord,
    ObjectiveOmissionPenaltyRecord,
    PrecedenceRecord,
    VrpActivityRelationData,
    VrpEventData,
    VrpInstanceSettings,
    VrpInterventionData,
    VrpPackageData,
)
from multiobjective_vns.multiday_vrp.input_utils import (
    CsvRow,
)
from multiobjective_vns.multiday_vrp.input_validation import (
    _cell_error,
    _float_field,
    _foreign,
    _id_field,
    _require_activity,
    _table_error,
    _unique,
    _unique_rows,
)


def _parse_relations(
    tables: dict[str, tuple[CsvRow, ...]],
    paths: dict[str, Path | None],
    package_data: VrpPackageData,
    intervention_data: VrpInterventionData,
    settings: VrpInstanceSettings,
) -> VrpActivityRelationData:
    activity_nodes: dict[tuple[str, str], str] = {}
    for package in package_data.packages:
        activity_nodes['pickup', package.package_id] = package.pickup_node_id
        activity_nodes['delivery', package.package_id] = package.delivery_node_id
    for intervention in intervention_data.interventions:
        activity_nodes['intervention', intervention.intervention_id] = (
            intervention.node_id
        )
    precedence: list[PrecedenceRecord] = []
    for row_number, row in enumerate(tables['precedence'], start=2):
        before_kind = _id_field(
            row, 'before_activity_kind', paths['precedence'], row_number
        )
        before_id = _id_field(
            row, 'before_activity_id', paths['precedence'], row_number
        )
        after_kind = _id_field(
            row, 'after_activity_kind', paths['precedence'], row_number
        )
        after_id = _id_field(row, 'after_activity_id', paths['precedence'], row_number)
        _require_activity(
            (before_kind, before_id),
            activity_nodes,
            paths['precedence'],
            row_number,
            'before_activity_kind',
        )
        _require_activity(
            (after_kind, after_id),
            activity_nodes,
            paths['precedence'],
            row_number,
            'after_activity_kind',
        )
        if (before_kind, before_id) == (after_kind, after_id):
            raise _cell_error(
                paths['precedence'],
                row_number,
                'after_activity_id',
                'self-precedence is invalid',
            )
        precedence.append(
            PrecedenceRecord(before_kind, before_id, after_kind, after_id)
        )
    _unique_rows(precedence, paths['precedence'])

    co_location: list[CoLocationRecord] = []
    for row_number, row in enumerate(tables['co_location'], start=2):
        kind_1 = _id_field(row, 'activity_kind_1', paths['co_location'], row_number)
        id_1 = _id_field(row, 'activity_id_1', paths['co_location'], row_number)
        kind_2 = _id_field(row, 'activity_kind_2', paths['co_location'], row_number)
        id_2 = _id_field(row, 'activity_id_2', paths['co_location'], row_number)
        first, second = (kind_1, id_1), (kind_2, id_2)
        _require_activity(
            first, activity_nodes, paths['co_location'], row_number, 'activity_kind_1'
        )
        _require_activity(
            second, activity_nodes, paths['co_location'], row_number, 'activity_kind_2'
        )
        if first == second:
            raise _cell_error(
                paths['co_location'],
                row_number,
                'activity_id_2',
                'co-location must name distinct activities',
            )
        if first > second:
            raise _cell_error(
                paths['co_location'],
                row_number,
                'activity_kind_1',
                'activity references must be in ascending lexicographic order',
            )
        if (
            settings.service_coverage_mode == 'mandatory'
            and activity_nodes[first] != activity_nodes[second]
        ):
            raise _cell_error(
                paths['co_location'],
                row_number,
                'activity_kind_2',
                'mandatory co-located activities have different nodes',
            )
        co_location.append(CoLocationRecord(kind_1, id_1, kind_2, id_2))
    _unique_rows(co_location, paths['co_location'])
    return VrpActivityRelationData(tuple(precedence), tuple(co_location))


def _parse_events(
    tables: dict[str, tuple[CsvRow, ...]],
    paths: dict[str, Path | None],
    node_ids: tuple[str, ...],
    arcs: tuple[ArcRecord, ...],
) -> VrpEventData:
    event_types = tuple(
        EventTypeRecord(
            _id_field(row, 'event_type_id', paths['event_types'], row_number),
            _id_field(row, 'location_domain', paths['event_types'], row_number),
        )
        for row_number, row in enumerate(tables['event_types'], start=2)
    )
    _unique(
        (item.event_type_id for item in event_types),
        paths['event_types'],
        'event_type_id',
    )
    for row_number, event_type in enumerate(event_types, start=2):
        if event_type.location_domain not in {'node', 'arc'}:
            raise _cell_error(
                paths['event_types'],
                row_number,
                'location_domain',
                "must be 'node' or 'arc'",
            )
    domains = {item.event_type_id: item.location_domain for item in event_types}
    event_records = tuple(
        EventRecord(
            _id_field(row, 'event_id', paths['events'], row_number),
            _id_field(row, 'event_type_id', paths['events'], row_number),
            _id_field(row, 'location_id', paths['events'], row_number),
        )
        for row_number, row in enumerate(tables['events'], start=2)
    )
    _unique((item.event_id for item in event_records), paths['events'], 'event_id')
    _foreign(
        (item.event_type_id for item in event_records),
        set(domains),
        paths['events'],
        'event_type_id',
    )
    node_set = set(node_ids)
    arc_set = {item.arc_id for item in arcs}
    for row_number, event in enumerate(event_records, start=2):
        allowed = node_set if domains[event.event_type_id] == 'node' else arc_set
        if event.location_id not in allowed:
            raise _cell_error(
                paths['events'],
                row_number,
                'location_id',
                f'unknown {domains[event.event_type_id]} location {event.location_id!r}',
            )
    return VrpEventData(event_types, event_records, ())


def _parse_omission_penalties(
    tables: dict[str, tuple[CsvRow, ...]],
    paths: dict[str, Path | None],
    settings: VrpInstanceSettings,
    packages: VrpPackageData,
    interventions: VrpInterventionData,
) -> tuple[ObjectiveOmissionPenaltyRecord, ...]:
    result = tuple(
        ObjectiveOmissionPenaltyRecord(
            _id_field(
                row, 'objective_id', paths['objective_omission_penalties'], row_number
            ),
            _id_field(
                row, 'activity_kind', paths['objective_omission_penalties'], row_number
            ),
            _id_field(
                row, 'activity_id', paths['objective_omission_penalties'], row_number
            ),
            _float_field(
                row,
                'amount',
                paths['objective_omission_penalties'],
                row_number,
                minimum=0.0,
                strict_minimum=True,
            ),
        )
        for row_number, row in enumerate(
            tables['objective_omission_penalties'], start=2
        )
    )
    _unique_rows(result, paths['objective_omission_penalties'])
    if settings.service_coverage_mode == 'mandatory' and result:
        raise _table_error(
            paths['objective_omission_penalties'],
            'omission penalties apply only in penalized_omission mode',
        )
    return result
