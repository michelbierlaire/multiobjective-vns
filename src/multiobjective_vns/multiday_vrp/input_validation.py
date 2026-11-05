"""Shared path, field, and relationship validation.

Created: 2026-11-03T16:34:50+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from multiobjective_vns.multiday_vrp.input_errors import VrpInputError
from multiobjective_vns.multiday_vrp.input_records import (
    ArcRecord,
    FrozenTomlTable,
    LoadedScenarioBank,
    VrpActivityRelationData,
    VrpFleetData,
    VrpInstanceSettings,
    VrpInterventionData,
    VrpNetworkData,
    VrpObjectiveData,
    VrpPackageData,
    VrpPersonnelData,
    VrpWorkRuleData,
)
from multiobjective_vns.multiday_vrp.input_schema import FILE_NAMES, UNIT_SECONDS
from multiobjective_vns.multiday_vrp.input_utils import (
    CsvRow,
    parse_bool,
    parse_finite_float,
    parse_identifier,
    parse_integer,
    parse_offset_datetime,
    read_csv_table,
    read_toml_table,
)

type _Table = dict[str, object]


def _read_table(path: Path, columns: tuple[str, ...]) -> tuple[CsvRow, ...]:
    return read_csv_table(path, columns)


def _file_path(
    root: Path,
    files: dict[str, object],
    key: str,
    manifest_name: str,
    *,
    required: bool = True,
) -> Path | None:
    value = files.get(key)
    if value is None:
        if required:
            raise VrpInputError(
                file=manifest_name,
                column=f'files.{key}',
                reason='required file reference is missing',
            )
        return None
    if type(value) is not str or not value.strip() or value.strip() != value:
        raise VrpInputError(
            file=manifest_name,
            column=f'files.{key}',
            reason='must be a nonempty relative path',
        )
    relative = Path(value)
    if relative.is_absolute():
        raise VrpInputError(
            file=manifest_name,
            column=f'files.{key}',
            reason='file paths must be relative',
        )
    return root / relative


def _relative_path(root: Path, value: str, filename: str, column: str) -> Path:
    if not value or value.strip() != value:
        raise VrpInputError(
            file=filename, column=column, reason='must be a nonempty relative path'
        )
    result = Path(value)
    if result.is_absolute():
        raise VrpInputError(
            file=filename, column=column, reason='path must be relative'
        )
    return root / result


def _id_field(row: CsvRow, column: str, path: Path | None, row_number: int) -> str:
    return _id_value(row[column], _filename(path), row_number, column)


def _id_value(value: str, filename: str, row_number: int, column: str) -> str:
    return parse_identifier(value, file=filename, row=row_number, column=column)


def _float_field(
    row: CsvRow,
    column: str,
    path: Path | None,
    row_number: int,
    *,
    minimum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    return parse_finite_float(
        row[column],
        file=_filename(path),
        row=row_number,
        column=column,
        minimum=minimum,
        strict_minimum=strict_minimum,
    )


def _optional_float_field(
    row: CsvRow,
    column: str,
    path: Path | None,
    row_number: int,
    *,
    minimum: float | None = None,
    strict_minimum: bool = False,
) -> float | None:
    if row[column] == '':
        return None
    return _float_field(
        row,
        column,
        path,
        row_number,
        minimum=minimum,
        strict_minimum=strict_minimum,
    )


def _int_field(
    row: CsvRow,
    column: str,
    path: Path | None,
    row_number: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    return parse_integer(
        row[column],
        file=_filename(path),
        row=row_number,
        column=column,
        minimum=minimum,
        maximum=maximum,
    )


def _bool_field(row: CsvRow, column: str, path: Path | None, row_number: int) -> bool:
    return parse_bool(row[column], file=_filename(path), row=row_number, column=column)


def _elapsed_time_field(
    row: CsvRow,
    column: str,
    path: Path | None,
    row_number: int,
    origin: datetime,
    time_zone: str,
    duration_scale: float,
    horizon: float,
) -> float:
    filename = _filename(path)
    timestamp = parse_offset_datetime(
        row[column], file=filename, row=row_number, column=column
    )
    zone = ZoneInfo(time_zone)
    local = timestamp.astimezone(zone)
    if (
        local.replace(tzinfo=None) != timestamp.replace(tzinfo=None)
        or local.utcoffset() != timestamp.utcoffset()
    ):
        raise _cell_error(
            path, row_number, column, f'UTC offset does not match {time_zone!r}'
        )
    elapsed = (
        timestamp.astimezone(UTC) - origin.astimezone(UTC)
    ).total_seconds() / duration_scale
    if elapsed < -_numeric_tolerance(
        elapsed, 0.0
    ) or elapsed > horizon + _numeric_tolerance(elapsed, horizon):
        raise _cell_error(
            path, row_number, column, 'timestamp must lie within the planning horizon'
        )
    return min(max(elapsed, 0.0), horizon)


def _time_field(
    row: CsvRow,
    column: str,
    path: Path | None,
    row_number: int,
    origin: datetime,
    time_zone: str,
    duration_scale: float,
    horizon: float,
) -> float:
    return _elapsed_time_field(
        row, column, path, row_number, origin, time_zone, duration_scale, horizon
    )


def _ids(rows: tuple[CsvRow, ...], path: Path | None, column: str) -> tuple[str, ...]:
    values = tuple(
        _id_field(row, column, path, row_number)
        for row_number, row in enumerate(rows, start=2)
    )
    _unique(values, path, column)
    return values


def _unique(values: Any, path: Path | None, column: str) -> None:
    seen: set[object] = set()
    for row_number, value in enumerate(values, start=2):
        if value in seen:
            raise _cell_error(path, row_number, column, f'duplicate value {value!r}')
        seen.add(value)


def _unique_rows(rows: Any, path: Path | None) -> None:
    _unique(rows, path, 'row')


def _foreign(values: Any, allowed: set[str], path: Path | None, column: str) -> None:
    for row_number, value in enumerate(values, start=2):
        if value not in allowed:
            raise _cell_error(path, row_number, column, f'unknown reference {value!r}')


def _require_each(
    relation: Any,
    entities: Any,
    entity_field: str,
    path: Path | None,
    entity_label: str,
) -> None:
    found = {getattr(row, entity_field) for row in relation}
    for entity in entities:
        entity_id = getattr(entity, entity_field)
        if entity_id not in found:
            raise _table_error(
                path, f'{entity_label} {entity_id!r} needs at least one row'
            )


def _require_rule_rows(
    records: Any,
    expected: set[tuple[str, int]],
    rule: str,
    settings: VrpInstanceSettings,
    path: Path | None,
    key: Any,
) -> None:
    actual = {key(item) for item in records}
    if rule in settings.active_simple_rules:
        if actual != expected:
            raise _table_error(
                path,
                f'{rule} table must contain exactly one row for each applicable entity and day',
            )
    elif records:
        raise _table_error(path, f'rows require {rule} to be active')


def _require_exact_set(
    actual: set[str], expected: set[str], path: Path | None, column: str
) -> None:
    if actual != expected:
        missing = expected - actual
        extra = actual - expected
        detail = (
            f'missing {min(missing)!r}' if missing else f'unexpected {min(extra)!r}'
        )
        raise _table_error(path, f'{column} set is incomplete: {detail}')


def _require_activity(
    activity: tuple[str, str],
    activity_nodes: dict[tuple[str, str], str],
    path: Path | None,
    row_number: int,
    column: str,
) -> None:
    if activity[0] not in {'pickup', 'delivery', 'intervention'}:
        raise _cell_error(path, row_number, column, 'unknown activity kind')
    if activity not in activity_nodes:
        raise _cell_error(
            path, row_number, column, f'unknown activity reference {activity!r}'
        )


def _table_error(path: Path | None, reason: str) -> VrpInputError:
    return VrpInputError(file=_filename(path), reason=reason)


def _cell_error(
    path: Path | None,
    row_number: int,
    column: str,
    reason: str,
) -> VrpInputError:
    return VrpInputError(
        file=_filename(path), row=row_number, column=column, reason=reason
    )


def _missing_file(key: str) -> VrpInputError:
    filename = FILE_NAMES.get(key, f'{key.replace("_", "-")}.csv')
    return VrpInputError(
        file='instance.toml',
        column=f'files.{key}',
        reason=f'required file {filename!r} is missing',
    )


def _filename(path: Path | None) -> str:
    return path.name if path is not None else 'instance.toml'


def _intervals_cover_horizon(
    intervals: Sequence[tuple[float, float]], start: float, end: float
) -> bool:
    ordered = sorted(intervals)
    cursor = start
    for interval_start, interval_end in ordered:
        if interval_start > cursor + _numeric_tolerance(interval_start, cursor):
            return False
        cursor = max(cursor, interval_end)
        if cursor >= end - _numeric_tolerance(cursor, end):
            return True
    return cursor >= end - _numeric_tolerance(cursor, end)


def _has_restrictive_windows(
    scenario_bank: LoadedScenarioBank,
    packages: VrpPackageData,
    interventions: VrpInterventionData,
) -> bool:
    start = scenario_bank.day_boundaries[0][0]
    end = scenario_bank.day_boundaries[-1][-1]
    for package in packages.packages:
        for activity in ('pickup', 'delivery'):
            windows = tuple(
                (item.start_time, item.end_time)
                for item in packages.package_time_windows
                if item.package_id == package.package_id and item.activity == activity
            )
            if not _intervals_cover_horizon(windows, start, end):
                return True
    for intervention in interventions.interventions:
        by_day: dict[int, list[tuple[float, float]]] = {}
        for item in interventions.intervention_time_windows:
            if item.intervention_id == intervention.intervention_id:
                by_day.setdefault(item.day_index, []).append(
                    (item.start_time, item.end_time)
                )
        if len(by_day) != len(scenario_bank.planning_dates):
            return True
        for day_index, day in enumerate(scenario_bank.day_boundaries, start=1):
            if not _intervals_cover_horizon(by_day.get(day_index, []), day[0], day[-1]):
                return True
    return False


def _numeric_tolerance(first: float, second: float) -> float:
    return 1e-9 + 1e-12 * max(abs(first), abs(second))


def _toml_string(
    table: dict[str, object], key: str, filename: str, location: str = ''
) -> str:
    value = table.get(key)
    if type(value) is not str:
        column = f'{location}.{key}' if location else key
        raise VrpInputError(file=filename, column=column, reason='must be a string')
    return value


def _toml_integer(table: dict[str, object], key: str, filename: str) -> int:
    value = table.get(key)
    if type(value) is not int:
        raise VrpInputError(file=filename, column=key, reason='must be an integer')
    return value


def _toml_float(
    value: object,
    filename: str,
    location: str,
    *,
    minimum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    if type(value) not in {int, float}:
        raise VrpInputError(
            file=filename, column=location, reason='must be a TOML number'
        )
    if type(value) is bool:
        raise VrpInputError(
            file=filename, column=location, reason='must be a numeric value'
        )
    parsed = float(cast(int | float, value))
    if not math.isfinite(parsed):
        raise VrpInputError(file=filename, column=location, reason='must be finite')
    if minimum is not None:
        invalid = parsed <= minimum if strict_minimum else parsed < minimum
        if invalid:
            relation = 'greater than' if strict_minimum else 'at least'
            raise VrpInputError(
                file=filename, column=location, reason=f'must be {relation} {minimum}'
            )
    return parsed


def _as_table(value: object, filename: str, location: str) -> dict[str, object]:
    if type(value) is not dict:
        raise VrpInputError(
            file=filename, column=location, reason='must be a TOML table'
        )
    return cast(dict[str, object], value)


def _exact_dict_keys(
    table: dict[str, object], expected: set[str], filename: str, location: str
) -> None:
    missing = expected - table.keys()
    extra = table.keys() - expected
    if missing:
        key = min(missing)
        raise VrpInputError(
            file=filename, column=f'{location}.{key}', reason='required key is missing'
        )
    if extra:
        key = min(extra)
        raise VrpInputError(
            file=filename, column=f'{location}.{key}', reason='unknown key'
        )


def _load_optional_toml(
    root: Path, manifest: dict[str, object], key: str, filename: str
) -> FrozenTomlTable | None:
    value = manifest.get(key)
    if value is None:
        return None
    if type(value) is not str:
        raise VrpInputError(
            file=filename, column=key, reason='must be a relative path string'
        )
    path = _relative_path(root, value, filename, key)
    parsed = read_toml_table(path)
    return FrozenTomlTable(parsed)


def _validate_setting_relations(
    paths: dict[str, Path | None],
    network: VrpNetworkData,
    fleet: VrpFleetData,
    packages: VrpPackageData,
    interventions: VrpInterventionData,
    personnel: VrpPersonnelData,
    relations: VrpActivityRelationData,
    work_rules: VrpWorkRuleData,
    settings: VrpInstanceSettings,
    scenario_bank: LoadedScenarioBank,
) -> None:
    del work_rules
    _unique_by(
        packages.package_time_windows,
        lambda item: (item.package_id, item.activity, item.window_index),
        paths['package_time_windows'],
        'package/activity/window_index',
    )
    _unique_by(
        interventions.intervention_time_windows,
        lambda item: (item.intervention_id, item.day_index, item.window_index),
        paths['intervention_time_windows'],
        'intervention/day/window_index',
    )
    if settings.staff_depot_mode == 'depot_continuity' and any(
        person.initial_depot_id is None for person in personnel.personnel
    ):
        raise _table_error(
            paths['personnel'],
            'depot_continuity requires an initial depot for every person',
        )
    if settings.service_coverage_mode == 'mandatory':
        for pair in relations.co_location:
            first = _activity_node(
                pair.activity_kind_1, pair.activity_id_1, packages, interventions
            )
            second = _activity_node(
                pair.activity_kind_2, pair.activity_id_2, packages, interventions
            )
            if first != second:
                raise _table_error(
                    paths['co_location'],
                    'mandatory co-located activities have different nodes',
                )
    if scenario_bank.time_unit not in UNIT_SECONDS:
        raise _table_error(None, 'ScenarioBank has an unsupported time unit')
    if not fleet.vehicles:
        raise _table_error(paths['vehicles'], 'the instance requires vehicles')
    if not network.depots:
        raise _table_error(paths['depots'], 'the instance requires depots')


def _validate_objective_omissions(
    data: VrpObjectiveData,
    settings: VrpInstanceSettings,
    packages: VrpPackageData,
    interventions: VrpInterventionData,
    filename: str,
) -> None:
    tasks = (
        [('pickup', item.package_id) for item in packages.packages]
        + [('delivery', item.package_id) for item in packages.packages]
        + [
            ('intervention', item.intervention_id)
            for item in interventions.interventions
        ]
    )
    expected = {
        (objective.objective_id, kind, activity_id)
        for objective in data.objectives
        for kind, activity_id in tasks
    }
    actual = {
        (item.objective_id, item.activity_kind, item.activity_id)
        for item in data.objective_omission_penalties
    }
    if settings.service_coverage_mode == 'penalized_omission':
        if actual != expected:
            missing = expected - actual
            extra = actual - expected
            value = min(missing or extra)
            status = 'missing' if missing else 'unexpected'
            raise VrpInputError(
                file=filename, reason=f'{status} omission penalty for {value!r}'
            )
    elif actual:
        raise VrpInputError(
            file=filename, reason='omission penalties require penalized_omission mode'
        )
    _foreign(
        (item.objective_id for item in data.objective_omission_penalties),
        {item.objective_id for item in data.objectives},
        Path(filename),
        'objective_id',
    )


def _require_equal_arc_catalog(
    arcs: tuple[ArcRecord, ...], scenario_arc_ids: tuple[str, ...], bank_file: str
) -> None:
    instance_arc_ids = {item.arc_id for item in arcs}
    bank_ids = set(scenario_arc_ids)
    if instance_arc_ids != bank_ids:
        missing = bank_ids - instance_arc_ids
        extra = instance_arc_ids - bank_ids
        if missing:
            reason = f'arc catalog is missing ScenarioBank arc {min(missing)!r}'
        else:
            reason = f'arc catalog has extra instance arc {min(extra)!r}'
        raise VrpInputError(file=Path(bank_file).name, reason=reason)


def _activity_node(
    kind: str,
    activity_id: str,
    packages: VrpPackageData,
    interventions: VrpInterventionData,
) -> str:
    if kind == 'pickup':
        return next(
            item.pickup_node_id
            for item in packages.packages
            if item.package_id == activity_id
        )
    if kind == 'delivery':
        return next(
            item.delivery_node_id
            for item in packages.packages
            if item.package_id == activity_id
        )
    return next(
        item.node_id
        for item in interventions.interventions
        if item.intervention_id == activity_id
    )


def _unique_by(
    values: Any,
    key: Any,
    path: Path | None,
    label: str,
) -> None:
    _unique((key(item) for item in values), path, label)
