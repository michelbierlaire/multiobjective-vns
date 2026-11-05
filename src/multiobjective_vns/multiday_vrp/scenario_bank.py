"""Loading and validation of standalone ScenarioBank bundles.

Created: 2026-11-03T16:35:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from multiobjective_vns.multiday_vrp.input_errors import VrpInputError
from multiobjective_vns.multiday_vrp.input_records import (
    LoadedScenarioBank,
    ScenarioRecord,
)
from multiobjective_vns.multiday_vrp.input_utils import (
    parse_date,
    parse_finite_float,
    parse_identifier,
    parse_integer,
    parse_offset_datetime,
    read_csv_table,
    read_toml_table,
)

_MANIFEST_KEYS = {
    'format_version',
    'arc_catalog_file',
    'target_grid_file',
    'time_zone',
    'time_unit',
    'scenario_count',
    'scenarios_file',
    'travel_times_file',
    'post_horizon_rule_id',
    'post_horizon_rule_configuration_id',
}
_TIME_UNITS = {'seconds': 1.0, 'minutes': 60.0, 'hours': 3600.0}

__all__ = ['load_scenario_bank']


def load_scenario_bank(manifest_path: Path) -> LoadedScenarioBank:
    """Read and validate a standalone ScenarioBank bundle.

    :param manifest_path: Path to ``scenario-bank.toml``.
    :returns: Immutable normalized scenario bank.
    :raises VrpInputError: If a file, table, value, reference, or profile is invalid.
    """
    manifest = read_toml_table(manifest_path)
    _require_exact_keys(manifest, _MANIFEST_KEYS, manifest_path.name)
    version = _toml_integer(manifest, 'format_version', manifest_path.name)
    if version != 1:
        raise VrpInputError(
            file=manifest_path.name,
            column='format_version',
            reason='supported format_version is 1',
        )
    time_zone_name = _toml_string(manifest, 'time_zone', manifest_path.name)
    try:
        zone = ZoneInfo(time_zone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise VrpInputError(
            file=manifest_path.name,
            column='time_zone',
            reason=f'unknown IANA time zone {time_zone_name!r}',
        ) from error
    time_unit = _toml_string(manifest, 'time_unit', manifest_path.name)
    if time_unit not in _TIME_UNITS:
        raise VrpInputError(
            file=manifest_path.name,
            column='time_unit',
            reason="must be 'seconds', 'minutes', or 'hours'",
        )
    scenario_count = _toml_integer(
        manifest, 'scenario_count', manifest_path.name, minimum=1
    )
    relative_names = {
        key: _toml_string(manifest, key, manifest_path.name)
        for key in (
            'arc_catalog_file',
            'target_grid_file',
            'scenarios_file',
            'travel_times_file',
            'post_horizon_rule_id',
            'post_horizon_rule_configuration_id',
        )
    }
    for key in ('post_horizon_rule_id', 'post_horizon_rule_configuration_id'):
        if (
            not relative_names[key].strip()
            or relative_names[key].strip() != relative_names[key]
        ):
            raise VrpInputError(
                file=manifest_path.name,
                column=key,
                reason='must be a nonempty identifier without surrounding whitespace',
            )
    base = manifest_path.parent
    files = {key: base / relative_names[key] for key in relative_names}
    arc_ids = _read_arc_catalog(files['arc_catalog_file'])
    planning_dates, day_boundaries = _read_target_grid(
        files['target_grid_file'], zone, time_unit
    )
    scenarios = _read_scenarios(files['scenarios_file'], scenario_count)
    travel_times = _read_travel_times(
        files['travel_times_file'],
        arc_ids,
        len(planning_dates),
        day_boundaries,
        scenarios,
    )
    _validate_fifo(arc_ids, planning_dates, day_boundaries, scenarios, travel_times)
    return LoadedScenarioBank(
        format_version=1,
        arc_ids=arc_ids,
        time_zone=time_zone_name,
        time_unit=time_unit,
        planning_dates=planning_dates,
        day_boundaries=day_boundaries,
        scenarios=scenarios,
        travel_times=MappingProxyType(travel_times),
        post_horizon_rule_id=relative_names['post_horizon_rule_id'],
        post_horizon_rule_configuration_id=relative_names[
            'post_horizon_rule_configuration_id'
        ],
    )


def _read_arc_catalog(path: Path) -> tuple[str, ...]:
    rows = read_csv_table(path, ('arc_id',))
    arc_ids = tuple(
        parse_identifier(row['arc_id'], file=path.name, row=index, column='arc_id')
        for index, row in enumerate(rows, start=2)
    )
    _require_unique(arc_ids, path.name, 'arc_id')
    if not arc_ids:
        raise VrpInputError(file=path.name, reason='arc catalog must not be empty')
    return arc_ids


def _read_target_grid(
    path: Path,
    zone: ZoneInfo,
    time_unit: str,
) -> tuple[tuple[date, ...], tuple[tuple[float, ...], ...]]:
    rows = read_csv_table(
        path, ('day_index', 'planning_date', 'boundary_index', 'boundary_time')
    )
    if not rows:
        raise VrpInputError(file=path.name, reason='target grid must not be empty')
    grouped: dict[int, list[tuple[int, date, datetime, int]]] = {}
    for row_number, row in enumerate(rows, start=2):
        day_index = parse_integer(
            row['day_index'],
            file=path.name,
            row=row_number,
            column='day_index',
            minimum=1,
        )
        planning_date = parse_date(
            row['planning_date'],
            file=path.name,
            row=row_number,
            column='planning_date',
        )
        boundary_index = parse_integer(
            row['boundary_index'],
            file=path.name,
            row=row_number,
            column='boundary_index',
            minimum=0,
        )
        boundary_time = parse_offset_datetime(
            row['boundary_time'],
            file=path.name,
            row=row_number,
            column='boundary_time',
        )
        _require_zone_offset(boundary_time, zone, path.name, row_number)
        grouped.setdefault(day_index, []).append(
            (boundary_index, planning_date, boundary_time, row_number)
        )
    indices = sorted(grouped)
    if indices != list(range(1, len(indices) + 1)):
        raise VrpInputError(
            file=path.name, column='day_index', reason='days must be consecutive from 1'
        )
    origin_row = next(item for item in grouped[1] if item[0] == 0)
    origin = _as_utc(origin_row[2])
    dates: list[date] = []
    boundaries: list[tuple[float, ...]] = []
    previous_date: date | None = None
    seconds_per_unit = _TIME_UNITS[time_unit]
    for expected_day in indices:
        values = grouped[expected_day]
        ordered = sorted(values, key=lambda item: item[0])
        day_date = ordered[0][1]
        if previous_date is not None and day_date != previous_date + timedelta(days=1):
            raise VrpInputError(
                file=path.name,
                row=ordered[0][3],
                column='planning_date',
                reason='planning dates must be consecutive',
            )
        previous_date = day_date
        dates.append(day_date)
        if any(item[1] != day_date for item in ordered):
            raise VrpInputError(
                file=path.name,
                row=ordered[0][3],
                column='planning_date',
                reason='all boundaries for a day must use its planning_date',
            )
        boundary_indices = tuple(item[0] for item in ordered)
        if boundary_indices != tuple(range(len(ordered))) or len(ordered) < 2:
            raise VrpInputError(
                file=path.name,
                row=ordered[0][3],
                column='boundary_index',
                reason='each day needs consecutive indices from 0 and at least one interval',
            )
        first_local = ordered[0][2].astimezone(zone)
        last_local = ordered[-1][2].astimezone(zone)
        if (
            first_local.date() != day_date
            or first_local.timetz().replace(tzinfo=None) != time.min
        ):
            raise VrpInputError(
                file=path.name,
                row=ordered[0][3],
                column='boundary_time',
                reason='boundary 0 must be the planning-date local midnight',
            )
        next_date = day_date + timedelta(days=1)
        if (
            last_local.date() != next_date
            or last_local.timetz().replace(tzinfo=None) != time.min
        ):
            raise VrpInputError(
                file=path.name,
                row=ordered[-1][3],
                column='boundary_time',
                reason='final boundary must be the following local midnight',
            )
        utc_instants = tuple(_as_utc(item[2]) for item in ordered)
        if any(left >= right for left, right in zip(utc_instants, utc_instants[1:])):
            raise VrpInputError(
                file=path.name,
                row=ordered[0][3],
                column='boundary_time',
                reason='boundary instants must increase strictly within each day',
            )
        coordinates = tuple(
            (_as_utc(item[2]) - origin).total_seconds() / seconds_per_unit
            for item in ordered
        )
        if not all(isfinite(value) for value in coordinates):
            raise VrpInputError(file=path.name, reason='time coordinate is not finite')
        boundaries.append(coordinates)
    if len(dates) != len(grouped):
        raise VrpInputError(file=path.name, reason='invalid planning calendar')
    for day_index in range(1, len(boundaries)):
        before = boundaries[day_index - 1][-1]
        after = boundaries[day_index][0]
        if abs(before - after) > _numeric_tolerance(before, after):
            raise VrpInputError(
                file=path.name,
                reason='adjacent planning days must share the same midnight instant',
            )
    return tuple(dates), tuple(boundaries)


def _read_scenarios(path: Path, expected_count: int) -> tuple[ScenarioRecord, ...]:
    rows = read_csv_table(path, ('scenario_id', 'probability'))
    if len(rows) != expected_count:
        raise VrpInputError(
            file=path.name,
            reason=f'scenario_count is {expected_count}, found {len(rows)} rows',
        )
    records: list[ScenarioRecord] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        scenario_id = parse_identifier(
            row['scenario_id'], file=path.name, row=row_number, column='scenario_id'
        )
        if scenario_id in seen:
            raise VrpInputError(
                file=path.name,
                row=row_number,
                column='scenario_id',
                reason=f'duplicate scenario ID {scenario_id!r}',
            )
        seen.add(scenario_id)
        probability = parse_finite_float(
            row['probability'],
            file=path.name,
            row=row_number,
            column='probability',
            minimum=0.0,
            strict_minimum=True,
        )
        records.append(ScenarioRecord(scenario_id, probability))
    total = sum(item.probability for item in records)
    if abs(total - 1.0) > 1e-12:
        raise VrpInputError(
            file=path.name,
            column='probability',
            reason=f'probabilities sum to {total!r}, outside the 1e-12 tolerance',
        )
    return tuple(
        ScenarioRecord(item.scenario_id, item.probability / total) for item in records
    )


def _read_travel_times(
    path: Path,
    arc_ids: tuple[str, ...],
    day_count: int,
    day_boundaries: tuple[tuple[float, ...], ...],
    scenarios: tuple[ScenarioRecord, ...],
) -> dict[tuple[str, str, int, int], float]:
    rows = read_csv_table(
        path,
        ('scenario_id', 'arc_id', 'day_index', 'boundary_index', 'travel_time'),
    )
    scenario_ids = {item.scenario_id for item in scenarios}
    arc_id_set = set(arc_ids)
    result: dict[tuple[str, str, int, int], float] = {}
    for row_number, row in enumerate(rows, start=2):
        scenario_id = parse_identifier(
            row['scenario_id'], file=path.name, row=row_number, column='scenario_id'
        )
        arc_id = parse_identifier(
            row['arc_id'], file=path.name, row=row_number, column='arc_id'
        )
        if scenario_id not in scenario_ids:
            raise VrpInputError(
                file=path.name,
                row=row_number,
                column='scenario_id',
                reason=f'unknown scenario ID {scenario_id!r}',
            )
        if arc_id not in arc_id_set:
            raise VrpInputError(
                file=path.name,
                row=row_number,
                column='arc_id',
                reason=f'unknown arc ID {arc_id!r}',
            )
        day_index = parse_integer(
            row['day_index'],
            file=path.name,
            row=row_number,
            column='day_index',
            minimum=1,
            maximum=day_count,
        )
        boundary_index = parse_integer(
            row['boundary_index'],
            file=path.name,
            row=row_number,
            column='boundary_index',
            minimum=0,
            maximum=len(day_boundaries[day_index - 1]) - 1,
        )
        travel_time = parse_finite_float(
            row['travel_time'],
            file=path.name,
            row=row_number,
            column='travel_time',
            minimum=0.0,
        )
        key = scenario_id, arc_id, day_index, boundary_index
        if key in result:
            raise VrpInputError(
                file=path.name,
                row=row_number,
                reason=f'duplicate travel-time key {key!r}',
            )
        result[key] = travel_time
    expected = {
        (scenario.scenario_id, arc_id, day_index, boundary_index)
        for scenario in scenarios
        for arc_id in arc_ids
        for day_index, values in enumerate(day_boundaries, start=1)
        for boundary_index in range(len(values))
    }
    missing = expected - result.keys()
    if missing:
        first = min(missing)
        raise VrpInputError(
            file=path.name,
            reason=f'missing travel-time profile value for {first!r}',
        )
    extra = result.keys() - expected
    if extra:
        raise VrpInputError(
            file=path.name, reason=f'unexpected travel-time key {min(extra)!r}'
        )
    return result


def _validate_fifo(
    arc_ids: tuple[str, ...],
    planning_dates: tuple[date, ...],
    day_boundaries: tuple[tuple[float, ...], ...],
    scenarios: tuple[ScenarioRecord, ...],
    travel_times: Mapping[tuple[str, str, int, int], float],
) -> None:
    for scenario in scenarios:
        for arc_id in arc_ids:
            arrivals: list[float] = []
            for day_index, boundaries in enumerate(day_boundaries, start=1):
                arrivals.extend(
                    boundary
                    + travel_times[
                        (scenario.scenario_id, arc_id, day_index, boundary_index)
                    ]
                    for boundary_index, boundary in enumerate(boundaries)
                )
            for left, right in zip(arrivals, arrivals[1:]):
                if left > right + _numeric_tolerance(left, right):
                    raise VrpInputError(
                        file='travel-times.csv',
                        reason=(
                            f'FIFO violation for scenario {scenario.scenario_id!r}, '
                            f'arc {arc_id!r}, dates {planning_dates[0]} through '
                            f'{planning_dates[-1]}'
                        ),
                    )


def _require_zone_offset(
    timestamp: datetime,
    zone: ZoneInfo,
    filename: str,
    row_number: int,
) -> None:
    local = timestamp.astimezone(zone)
    if (
        local.replace(tzinfo=None) != timestamp.replace(tzinfo=None)
        or local.utcoffset() != timestamp.utcoffset()
    ):
        raise VrpInputError(
            file=filename,
            row=row_number,
            column='boundary_time',
            reason=f'UTC offset does not match the {zone.key} zone at this instant',
        )


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _numeric_tolerance(first: float, second: float) -> float:
    return 1e-9 + 1e-12 * max(abs(first), abs(second))


def _require_unique(values: tuple[str, ...], filename: str, column: str) -> None:
    seen: set[str] = set()
    for row_number, value in enumerate(values, start=2):
        if value in seen:
            raise VrpInputError(
                file=filename,
                row=row_number,
                column=column,
                reason=f'duplicate identifier {value!r}',
            )
        seen.add(value)


def _require_exact_keys(
    values: dict[str, object], expected: set[str], filename: str
) -> None:
    missing = expected - values.keys()
    extra = values.keys() - expected
    if missing:
        key = min(missing)
        raise VrpInputError(file=filename, column=key, reason='required key is missing')
    if extra:
        key = min(extra)
        raise VrpInputError(file=filename, column=key, reason='unknown key')


def _toml_string(values: dict[str, object], key: str, filename: str) -> str:
    value = values.get(key)
    if type(value) is not str:
        raise VrpInputError(file=filename, column=key, reason='must be a string')
    return value


def _toml_integer(
    values: dict[str, object],
    key: str,
    filename: str,
    *,
    minimum: int | None = None,
) -> int:
    value = values.get(key)
    if type(value) is not int:
        raise VrpInputError(file=filename, column=key, reason='must be an integer')
    if minimum is not None and value < minimum:
        raise VrpInputError(
            file=filename, column=key, reason=f'must be at least {minimum}'
        )
    return value
