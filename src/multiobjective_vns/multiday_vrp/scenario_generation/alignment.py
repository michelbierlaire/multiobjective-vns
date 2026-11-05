"""Align historical observations and interpolate short internal gaps.

Created: 2026-11-03T17:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from types import MappingProxyType
from zoneinfo import ZoneInfo

from multiobjective_vns.multiday_vrp.scenario_generation.errors import (
    ScenarioGenerationError,
)
from multiobjective_vns.multiday_vrp.scenario_generation.input_data import (
    InputData,
    Observation,
)
from multiobjective_vns.multiday_vrp.scenario_generation.records import (
    AlignedHistoricalData,
    HistoricalCoordinate,
    HistoricalDateCoverage,
    InterpolationRecord,
    ProfileCoordinate,
    SourceDataCoverage,
    SourceLabel,
)

__all__ = ['AlignmentResult', 'BlockData', 'align_history', 'extract_block']


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    aligned: AlignedHistoricalData
    coverage: SourceDataCoverage
    interpolations: tuple[InterpolationRecord, ...]


@dataclass(frozen=True, slots=True)
class BlockData:
    start_date: date
    historical_dates: tuple[date, ...]
    values: Mapping[ProfileCoordinate, float | None]
    source: Mapping[ProfileCoordinate, SourceLabel]
    missing_history_coordinates: tuple[HistoricalCoordinate, ...]


def align_history(data: InputData) -> AlignmentResult:
    """Create the dense historical-date by target-grid value array."""
    values: dict[HistoricalCoordinate, float | None] = {}
    sources: dict[HistoricalCoordinate, SourceLabel] = {}
    interpolation_records: list[InterpolationRecord] = []
    by_date_counts: dict[date, list[int]] = {
        day: [0, 0, 0, 0] for day in data.historical_dates
    }
    boundary_items = tuple(
        (day_index, boundary_index, timestamp)
        for day_index, day in enumerate(data.target_boundaries, start=1)
        for boundary_index, timestamp in enumerate(day)
    )
    for source_date in data.historical_dates:
        counts = by_date_counts[source_date]
        for arc_id in data.arc_ids:
            observations = data.observations.get((source_date, arc_id), ())
            observation_instants = tuple(item.instant_utc for item in observations)
            observations_by_instant = {item.instant_utc: item for item in observations}
            for day_index, boundary_index, target_time in boundary_items:
                coordinate = source_date, arc_id, day_index, boundary_index
                counts[0] += 1
                target_local = target_time.astimezone(data.zone)
                wall_clock = target_local.timetz().replace(tzinfo=None)
                source_instants = _valid_local_instants(
                    source_date, wall_clock, data.zone
                )
                selected_instant = _select_source_instant(
                    source_instants, target_local.utcoffset()
                )
                if selected_instant is None:
                    values[coordinate] = None
                    sources[coordinate] = 'missing'
                    counts[3] += 1
                    continue
                exact = observations_by_instant.get(selected_instant)
                if exact is not None:
                    values[coordinate] = exact.travel_time
                    sources[coordinate] = 'observed'
                    counts[1] += 1
                    continue
                interpolation = _interpolate(
                    selected_instant,
                    observations,
                    observation_instants,
                    data.maximum_interpolation_gap,
                    data.seconds_per_unit,
                )
                if interpolation is None:
                    values[coordinate] = None
                    sources[coordinate] = 'missing'
                    counts[3] += 1
                    continue
                interpolated, left, right = interpolation
                values[coordinate] = interpolated
                sources[coordinate] = 'interpolated'
                counts[2] += 1
                local_left = left.local_time
                local_right = right.local_time
                interpolation_records.append(
                    InterpolationRecord(
                        coordinate=coordinate,
                        interpolated_value=interpolated,
                        left_observation_time=local_left,
                        left_observation_value=left.travel_time,
                        right_observation_time=local_right,
                        right_observation_value=right.travel_time,
                    )
                )
    expected = sum(counts[0] for counts in by_date_counts.values())
    observed_count = sum(counts[1] for counts in by_date_counts.values())
    interpolated_count = sum(counts[2] for counts in by_date_counts.values())
    missing_count = sum(counts[3] for counts in by_date_counts.values())
    date_coverage = tuple(
        HistoricalDateCoverage(day, *by_date_counts[day])
        for day in data.historical_dates
    )
    aligned = AlignedHistoricalData(
        time_zone=data.time_zone_name,
        time_unit=data.time_unit,
        historical_dates=data.historical_dates,
        arc_ids=data.arc_ids,
        target_boundary_times=data.target_boundary_times,
        values=MappingProxyType(values),
        source=MappingProxyType(sources),
    )
    return AlignmentResult(
        aligned=aligned,
        coverage=SourceDataCoverage(
            expected,
            observed_count,
            interpolated_count,
            missing_count,
            date_coverage,
        ),
        interpolations=tuple(interpolation_records),
    )


def extract_block(
    data: InputData,
    alignment: AlignmentResult,
    start_date: date,
) -> BlockData:
    """Extract a consecutive historical block in target-day position order."""
    horizon = len(data.target_dates)
    block_dates = tuple(
        start_date + timedelta(days=offset) for offset in range(horizon)
    )
    values: dict[ProfileCoordinate, float | None] = {}
    sources: dict[ProfileCoordinate, SourceLabel] = {}
    missing_coordinates: list[HistoricalCoordinate] = []
    for day_index, historical_date in enumerate(block_dates, start=1):
        for arc_id in data.arc_ids:
            for boundary_index in range(len(data.target_boundaries[day_index - 1])):
                history_key = historical_date, arc_id, day_index, boundary_index
                profile_key = arc_id, day_index, boundary_index
                values[profile_key] = alignment.aligned.values[history_key]
                sources[profile_key] = alignment.aligned.source[history_key]
                if values[profile_key] is None:
                    missing_coordinates.append(history_key)
    return BlockData(
        start_date=start_date,
        historical_dates=block_dates,
        values=MappingProxyType(values),
        source=MappingProxyType(sources),
        missing_history_coordinates=tuple(missing_coordinates),
    )


def _valid_local_instants(
    local_date: date, wall_clock: time, zone: ZoneInfo
) -> tuple[tuple[datetime, timedelta | None], ...]:
    naive = datetime.combine(local_date, wall_clock)
    result: dict[datetime, timedelta | None] = {}
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        utc_candidate = candidate.astimezone(UTC)
        round_trip = utc_candidate.astimezone(zone)
        if round_trip.replace(tzinfo=None) == naive:
            result[utc_candidate] = candidate.utcoffset()
    return tuple(sorted(result.items()))


def _select_source_instant(
    instants: tuple[tuple[datetime, timedelta | None], ...],
    target_offset: timedelta | None,
) -> datetime | None:
    if len(instants) == 1:
        return instants[0][0]
    if len(instants) > 1:
        for instant, offset in instants:
            if offset == target_offset:
                return instant
    return None


def _interpolate(
    target: datetime,
    observations: tuple[Observation, ...],
    observation_instants: tuple[datetime, ...],
    maximum_gap: float,
    seconds_per_unit: float,
) -> tuple[float, Observation, Observation] | None:
    right_index = bisect_left(observation_instants, target)
    if (
        right_index == 0
        or right_index == len(observations)
        or observation_instants[right_index] == target
    ):
        return None
    left = observations[right_index - 1]
    right = observations[right_index]
    elapsed = (right.instant_utc - left.instant_utc).total_seconds() / seconds_per_unit
    if elapsed > maximum_gap:
        return None
    fraction = (target - left.instant_utc).total_seconds() / (
        right.instant_utc - left.instant_utc
    ).total_seconds()
    value = left.travel_time + fraction * (right.travel_time - left.travel_time)
    if not isfinite(value) or value < 0.0:
        raise ScenarioGenerationError(
            reason='interpolation produced a non-finite or negative travel time',
            operation='interpolate',
        )
    return value, left, right
