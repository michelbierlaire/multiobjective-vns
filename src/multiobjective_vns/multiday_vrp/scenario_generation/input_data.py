"""Parsing and validation for standalone scenario-generation inputs.

Created: 2026-11-03T17:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from multiobjective_vns.multiday_vrp.input_errors import VrpInputError
from multiobjective_vns.multiday_vrp.input_utils import (
    parse_date,
    parse_finite_float,
    parse_identifier,
    parse_integer,
    parse_offset_datetime,
    read_csv_table,
    read_toml_table,
)

_TIME_UNITS = {'seconds': 1.0, 'minutes': 60.0, 'hours': 3600.0}
_REQUIRED_MANIFEST_KEYS = {
    'format_version',
    'arc_catalog_file',
    'target_grid_file',
    'observations_file',
    'scenario_count',
    'time_unit',
    'maximum_interpolation_gap',
    'historical_date_start',
    'historical_date_end',
}
_OPTIONAL_MANIFEST_KEYS = {
    'time_zone',
    'random_seed',
    'block_weights_file',
    'post_horizon_rule_id',
    'post_horizon_rule_configuration_id',
    'date_selection',
}

__all__ = ['InputData', 'Observation', 'read_generation_input']


@dataclass(frozen=True, slots=True)
class Observation:
    instant_utc: datetime
    local_time: datetime
    travel_time: float


@dataclass(frozen=True, slots=True)
class InputData:
    manifest_path: Path
    arc_ids: tuple[str, ...]
    time_zone_name: str
    zone: ZoneInfo
    time_unit: str
    seconds_per_unit: float
    scenario_count: int
    maximum_interpolation_gap: float
    random_seed: int
    historical_dates: tuple[date, ...]
    target_dates: tuple[date, ...]
    target_boundaries: tuple[tuple[datetime, ...], ...]
    target_boundary_times: Mapping[tuple[int, int], datetime]
    target_grid_rows: tuple[tuple[int, date, int, str], ...]
    observations: Mapping[tuple[date, str], tuple[Observation, ...]]
    date_selected_starts: tuple[date, ...]
    block_weights: Mapping[date, float] | None
    post_horizon_rule_id: str
    post_horizon_rule_configuration_id: str


def read_generation_input(manifest_path: Path) -> InputData:
    """Read and validate the manifest and every referenced input file."""
    manifest = read_toml_table(manifest_path)
    unknown = set(manifest) - _REQUIRED_MANIFEST_KEYS - _OPTIONAL_MANIFEST_KEYS
    missing = _REQUIRED_MANIFEST_KEYS - set(manifest)
    if unknown:
        raise VrpInputError(
            file=manifest_path.name,
            reason=f'unknown manifest fields: {", ".join(sorted(unknown))}',
        )
    if missing:
        raise VrpInputError(
            file=manifest_path.name,
            reason=f'missing manifest fields: {", ".join(sorted(missing))}',
        )
    if (
        _integer(manifest['format_version'], manifest_path.name, 'format_version', 1)
        != 1
    ):
        raise VrpInputError(
            file=manifest_path.name,
            column='format_version',
            reason='supported format_version is 1',
        )
    scenario_count = _integer(
        manifest['scenario_count'], manifest_path.name, 'scenario_count', 1
    )
    random_seed = _integer(
        manifest.get('random_seed', 0), manifest_path.name, 'random_seed', 0
    )
    time_unit = _string(manifest['time_unit'], manifest_path.name, 'time_unit')
    if time_unit not in _TIME_UNITS:
        raise VrpInputError(
            file=manifest_path.name,
            column='time_unit',
            reason="must be 'seconds', 'minutes', or 'hours'",
        )
    maximum_gap = _finite_number(
        manifest['maximum_interpolation_gap'],
        manifest_path.name,
        'maximum_interpolation_gap',
        minimum=0.0,
    )
    historical_start = _manifest_date(
        manifest['historical_date_start'], manifest_path.name, 'historical_date_start'
    )
    historical_end = _manifest_date(
        manifest['historical_date_end'], manifest_path.name, 'historical_date_end'
    )
    if historical_start > historical_end:
        raise VrpInputError(
            file=manifest_path.name,
            column='historical_date_start',
            reason='must be on or before historical_date_end',
        )
    zone_name = _string(
        manifest.get('time_zone', 'Europe/Zurich'), manifest_path.name, 'time_zone'
    )
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise VrpInputError(
            file=manifest_path.name,
            column='time_zone',
            reason=f'unknown IANA time zone {zone_name!r}',
        ) from error

    relative_files = {
        name: _string(manifest[field], manifest_path.name, field)
        for field, name in (
            ('arc_catalog_file', 'arc_catalog'),
            ('target_grid_file', 'target_grid'),
            ('observations_file', 'observations'),
        )
    }
    if 'block_weights_file' in manifest:
        relative_files['block_weights'] = _string(
            manifest['block_weights_file'], manifest_path.name, 'block_weights_file'
        )
    paths = {key: manifest_path.parent / value for key, value in relative_files.items()}
    arc_ids = _read_arc_ids(paths['arc_catalog'])
    target_dates, target_boundaries, target_rows = _read_target_grid(
        paths['target_grid'], zone
    )
    historical_dates = tuple(
        historical_start + timedelta(days=offset)
        for offset in range((historical_end - historical_start).days + 1)
    )
    date_selected_starts = _read_selected_starts(
        manifest.get('date_selection'),
        historical_dates,
        len(target_dates),
        manifest_path.name,
    )
    block_weights = None
    if 'block_weights' in paths:
        block_weights = _read_block_weights(
            paths['block_weights'], date_selected_starts
        )
    post_id = _string(
        manifest.get('post_horizon_rule_id', 'constant_last_boundary'),
        manifest_path.name,
        'post_horizon_rule_id',
    )
    configuration_id = _string(
        manifest.get('post_horizon_rule_configuration_id', 'builtin-v1'),
        manifest_path.name,
        'post_horizon_rule_configuration_id',
    )
    if not post_id.strip() or not configuration_id.strip():
        raise VrpInputError(
            file=manifest_path.name,
            reason='post-horizon rule identifiers must be nonempty strings',
        )
    if post_id == 'constant_last_boundary' and configuration_id != 'builtin-v1':
        raise VrpInputError(
            file=manifest_path.name,
            column='post_horizon_rule_configuration_id',
            reason="the built-in rule uses configuration ID 'builtin-v1'",
        )
    if (
        post_id != 'constant_last_boundary'
        and 'post_horizon_rule_configuration_id' not in manifest
    ):
        raise VrpInputError(
            file=manifest_path.name,
            column='post_horizon_rule_configuration_id',
            reason='is required for a custom post-horizon rule',
        )
    observations = _read_observations(
        paths['observations'], arc_ids, historical_dates, zone
    )
    target_boundary_times = MappingProxyType(
        {
            (day_index, boundary_index): value
            for day_index, day in enumerate(target_boundaries, start=1)
            for boundary_index, value in enumerate(day)
        }
    )
    return InputData(
        manifest_path=manifest_path,
        arc_ids=arc_ids,
        time_zone_name=zone_name,
        zone=zone,
        time_unit=time_unit,
        seconds_per_unit=_TIME_UNITS[time_unit],
        scenario_count=scenario_count,
        maximum_interpolation_gap=maximum_gap,
        random_seed=random_seed,
        historical_dates=historical_dates,
        target_dates=target_dates,
        target_boundaries=target_boundaries,
        target_boundary_times=target_boundary_times,
        target_grid_rows=target_rows,
        observations=MappingProxyType(observations),
        date_selected_starts=date_selected_starts,
        block_weights=MappingProxyType(block_weights)
        if block_weights is not None
        else None,
        post_horizon_rule_id=post_id,
        post_horizon_rule_configuration_id=configuration_id,
    )


def _read_arc_ids(path: Path) -> tuple[str, ...]:
    rows = read_csv_table(path, ('arc_id',))
    ids = tuple(
        parse_identifier(row['arc_id'], file=path.name, row=index, column='arc_id')
        for index, row in enumerate(rows, start=2)
    )
    if not ids:
        raise VrpInputError(file=path.name, reason='arc catalog must not be empty')
    if len(set(ids)) != len(ids):
        raise VrpInputError(
            file=path.name, column='arc_id', reason='IDs must be unique'
        )
    return ids


def _read_target_grid(
    path: Path, zone: ZoneInfo
) -> tuple[
    tuple[date, ...],
    tuple[tuple[datetime, ...], ...],
    tuple[tuple[int, date, int, str], ...],
]:
    rows = read_csv_table(
        path, ('day_index', 'planning_date', 'boundary_index', 'boundary_time')
    )
    grouped: dict[int, list[tuple[int, date, datetime, str, int]]] = defaultdict(list)
    for row_number, row in enumerate(rows, start=2):
        day_index = parse_integer(
            row['day_index'],
            file=path.name,
            row=row_number,
            column='day_index',
            minimum=1,
        )
        planning_date = parse_date(
            row['planning_date'], file=path.name, row=row_number, column='planning_date'
        )
        boundary_index = parse_integer(
            row['boundary_index'],
            file=path.name,
            row=row_number,
            column='boundary_index',
            minimum=0,
        )
        timestamp = parse_offset_datetime(
            row['boundary_time'],
            file=path.name,
            row=row_number,
            column='boundary_time',
        )
        _validate_zone_offset(timestamp, zone, path.name, row_number, 'boundary_time')
        grouped[day_index].append(
            (boundary_index, planning_date, timestamp, row['boundary_time'], row_number)
        )
    if not grouped or sorted(grouped) != list(range(1, len(grouped) + 1)):
        raise VrpInputError(
            file=path.name, column='day_index', reason='days must be consecutive from 1'
        )
    dates: list[date] = []
    boundaries: list[tuple[datetime, ...]] = []
    flat_rows: list[tuple[int, date, int, str]] = []
    previous_date: date | None = None
    for day_index in range(1, len(grouped) + 1):
        ordered = sorted(grouped[day_index], key=lambda item: item[0])
        current_date = ordered[0][1]
        if previous_date is not None and current_date != previous_date + timedelta(
            days=1
        ):
            raise VrpInputError(
                file=path.name, reason='planning dates must be consecutive'
            )
        previous_date = current_date
        if any(item[1] != current_date for item in ordered):
            raise VrpInputError(
                file=path.name, reason='day rows must share planning_date'
            )
        if (
            tuple(item[0] for item in ordered) != tuple(range(len(ordered)))
            or len(ordered) < 2
        ):
            raise VrpInputError(
                file=path.name,
                column='boundary_index',
                reason='each day needs consecutive indices from 0 and at least one interval',
            )
        first_local = ordered[0][2].astimezone(zone)
        last_local = ordered[-1][2].astimezone(zone)
        if (
            first_local.date() != current_date
            or first_local.timetz().replace(tzinfo=None) != time.min
        ):
            raise VrpInputError(
                file=path.name, reason='boundary 0 must be local midnight'
            )
        if (
            last_local.date() != current_date + timedelta(days=1)
            or last_local.timetz().replace(tzinfo=None) != time.min
        ):
            raise VrpInputError(
                file=path.name, reason='final boundary must be next local midnight'
            )
        instants = tuple(_utc(item[2]) for item in ordered)
        if any(left >= right for left, right in zip(instants, instants[1:])):
            raise VrpInputError(
                file=path.name, reason='boundary instants must increase'
            )
        dates.append(current_date)
        boundaries.append(tuple(item[2].astimezone(zone) for item in ordered))
        flat_rows.extend(
            (day_index, current_date, boundary_index, raw)
            for boundary_index, _, _, raw, _ in ordered
        )
    for left, right in zip(boundaries, boundaries[1:]):
        if _utc(left[-1]) != _utc(right[0]):
            raise VrpInputError(
                file=path.name, reason='adjacent days must share midnight'
            )
    return tuple(dates), tuple(boundaries), tuple(flat_rows)


def _read_observations(
    path: Path,
    arc_ids: tuple[str, ...],
    historical_dates: tuple[date, ...],
    zone: ZoneInfo,
) -> dict[tuple[date, str], tuple[Observation, ...]]:
    rows = read_csv_table(path, ('arc_id', 'departure_time', 'travel_time'))
    arc_set = set(arc_ids)
    date_set = set(historical_dates)
    collected: dict[tuple[date, str], list[Observation]] = defaultdict(list)
    seen: set[tuple[str, datetime]] = set()
    for row_number, row in enumerate(rows, start=2):
        arc_id = parse_identifier(
            row['arc_id'], file=path.name, row=row_number, column='arc_id'
        )
        if arc_id not in arc_set:
            raise VrpInputError(
                file=path.name,
                row=row_number,
                column='arc_id',
                reason=f'unknown arc ID {arc_id!r}',
            )
        departure = parse_offset_datetime(
            row['departure_time'],
            file=path.name,
            row=row_number,
            column='departure_time',
        )
        _validate_zone_offset(departure, zone, path.name, row_number, 'departure_time')
        local = departure.astimezone(zone)
        source_date = local.date()
        if source_date not in date_set:
            raise VrpInputError(
                file=path.name,
                row=row_number,
                column='departure_time',
                reason='observation date falls outside the declared historical span',
            )
        value = parse_finite_float(
            row['travel_time'],
            file=path.name,
            row=row_number,
            column='travel_time',
            minimum=0.0,
        )
        instant = _utc(departure)
        duplicate_key = arc_id, instant
        if duplicate_key in seen:
            raise VrpInputError(
                file=path.name,
                row=row_number,
                reason='duplicate arc-instant observation',
            )
        seen.add(duplicate_key)
        collected[(source_date, arc_id)].append(Observation(instant, local, value))
    return {
        key: tuple(sorted(values, key=lambda item: item.instant_utc))
        for key, values in collected.items()
    }


def _read_selected_starts(
    selection: object,
    historical_dates: tuple[date, ...],
    horizon_days: int,
    file: str,
) -> tuple[date, ...]:
    if selection is None:
        return tuple(
            historical_dates[index]
            for index in range(max(0, len(historical_dates) - horizon_days + 1))
        )
    if type(selection) is not dict or set(selection) != {'eligible_block_start_dates'}:
        raise VrpInputError(
            file=file,
            column='date_selection',
            reason="must contain only 'eligible_block_start_dates'",
        )
    raw_dates = selection['eligible_block_start_dates']
    if type(raw_dates) is not list or not raw_dates:
        raise VrpInputError(
            file=file,
            column='eligible_block_start_dates',
            reason='must be a nonempty array',
        )
    starts = tuple(
        _manifest_date(value, file, 'eligible_block_start_dates') for value in raw_dates
    )
    if len(set(starts)) != len(starts):
        raise VrpInputError(
            file=file,
            column='eligible_block_start_dates',
            reason='dates must be unique',
        )
    return tuple(sorted(starts))


def _read_block_weights(path: Path, starts: tuple[date, ...]) -> dict[date, float]:
    rows = read_csv_table(path, ('block_start_date', 'weight'))
    result: dict[date, float] = {}
    for row_number, row in enumerate(rows, start=2):
        start = parse_date(
            row['block_start_date'],
            file=path.name,
            row=row_number,
            column='block_start_date',
        )
        if start in result:
            raise VrpInputError(
                file=path.name,
                row=row_number,
                column='block_start_date',
                reason='duplicate block start',
            )
        result[start] = parse_finite_float(
            row['weight'],
            file=path.name,
            row=row_number,
            column='weight',
            minimum=0.0,
            strict_minimum=True,
        )
    if set(result) != set(starts):
        raise VrpInputError(
            file=path.name,
            reason='block weights must contain exactly one row for every date-selected start',
        )
    return result


def _validate_zone_offset(
    timestamp: datetime, zone: ZoneInfo, file: str, row: int, column: str
) -> None:
    if timestamp.astimezone(zone).utcoffset() != timestamp.utcoffset():
        raise VrpInputError(
            file=file,
            row=row,
            column=column,
            reason='numeric UTC offset does not match the declared time zone',
        )


def _integer(value: object, file: str, column: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise VrpInputError(
            file=file,
            column=column,
            reason=f'must be a built-in integer of at least {minimum}, excluding bool',
        )
    return value


def _finite_number(value: object, file: str, column: str, minimum: float) -> float:
    if type(value) not in (int, float):
        raise VrpInputError(
            file=file, column=column, reason='must be an int or float, excluding bool'
        )
    try:
        converted = float(cast(int | float, value))
    except (OverflowError, ValueError) as error:
        raise VrpInputError(
            file=file, column=column, reason='must convert to a finite number'
        ) from error
    if not isfinite(converted) or converted < minimum:
        raise VrpInputError(
            file=file, column=column, reason=f'must be finite and at least {minimum}'
        )
    return converted


def _string(value: object, file: str, column: str) -> str:
    if type(value) is not str or not value.strip() or value.strip() != value:
        raise VrpInputError(
            file=file,
            column=column,
            reason='must be a nonempty string without surrounding whitespace',
        )
    return value


def _manifest_date(value: object, file: str, column: str) -> date:
    if type(value) is date:
        return value
    if type(value) is str:
        try:
            result = date.fromisoformat(value)
        except ValueError as error:
            raise VrpInputError(
                file=file, column=column, reason='must be an ISO calendar date'
            ) from error
        if result.isoformat() == value:
            return result
    raise VrpInputError(file=file, column=column, reason='must be an ISO calendar date')


def _utc(timestamp: datetime) -> datetime:
    return timestamp.astimezone(UTC)
