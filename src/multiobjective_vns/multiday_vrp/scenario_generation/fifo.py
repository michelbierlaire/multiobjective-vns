"""Weighted FIFO projection for complete candidate profiles.

Created: 2026-11-03T17:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC
from math import isfinite, ulp
from typing import NamedTuple

from multiobjective_vns.multiday_vrp.scenario_generation.errors import (
    ScenarioGenerationError,
)
from multiobjective_vns.multiday_vrp.scenario_generation.input_data import InputData
from multiobjective_vns.multiday_vrp.scenario_generation.records import (
    FifoAdjustment,
    FifoWeightKey,
    ProfileCoordinate,
)

__all__ = ['project_fifo']


class _PavaBlock(NamedTuple):
    start: int
    end: int
    weight: float
    mean: float
    lower_bound: float

    @property
    def level(self) -> float:
        return max(self.mean, self.lower_bound)


def project_fifo(
    data: InputData,
    block_start_date: str,
    profile: Mapping[ProfileCoordinate, float],
    supplied_weights: Mapping[FifoWeightKey, float],
) -> tuple[
    dict[ProfileCoordinate, float],
    dict[FifoWeightKey, float],
    tuple[FifoAdjustment, ...],
]:
    """Project each arc's full-horizon arrival sequence onto FIFO profiles."""
    projected = dict(profile)
    effective_weights: dict[FifoWeightKey, float] = {}
    adjustments: list[FifoAdjustment] = []
    origin = data.target_boundaries[0][0].astimezone(UTC)
    for arc_id in data.arc_ids:
        coordinates: list[ProfileCoordinate] = []
        theta: list[float] = []
        y: list[float] = []
        weights: list[float] = []
        for day_index, boundaries in enumerate(data.target_boundaries, start=1):
            for boundary_index, timestamp in enumerate(boundaries):
                coordinate = arc_id, day_index, boundary_index
                key = block_start_date, arc_id, day_index, boundary_index
                value = profile[coordinate]
                time_value = (
                    timestamp.astimezone(UTC) - origin
                ).total_seconds() / data.seconds_per_unit
                coordinates.append(coordinate)
                theta.append(time_value)
                y.append(time_value + value)
                weight = supplied_weights.get(key, 1.0)
                effective_weights[key] = weight
                weights.append(weight)
        if not all(isfinite(value) for value in y):
            raise ScenarioGenerationError(
                reason='arrival-time construction produced a non-finite value',
                operation='fifo_projection',
            )
        arrivals = _isotonic_with_lower_bounds(y, theta, weights)
        for coordinate, lower, original_arrival, adjusted_arrival in zip(
            coordinates, theta, y, arrivals, strict=True
        ):
            value = adjusted_arrival - lower
            if not isfinite(value) or value < 0.0:
                raise ScenarioGenerationError(
                    reason='FIFO projection produced an invalid travel time',
                    operation='fifo_projection',
                )
            projected[coordinate] = value
            original_value = original_arrival - lower
            change = abs(value - original_value)
            if change > 0.0:
                adjustments.append(
                    FifoAdjustment(
                        block_start_date=block_start_date,
                        arc_id=coordinate[0],
                        day_index=coordinate[1],
                        boundary_index=coordinate[2],
                        original_travel_time=original_value,
                        projected_travel_time=value,
                        absolute_change=change,
                    )
                )
    return projected, effective_weights, tuple(adjustments)


def _isotonic_with_lower_bounds(
    values: list[float], lower_bounds: list[float], weights: list[float]
) -> list[float]:
    """Solve weighted isotonic regression with nondecreasing lower bounds."""
    weight_scale = max(weights)
    scaled_weights = [max(weight / weight_scale, ulp(0.0)) for weight in weights]
    blocks: list[_PavaBlock] = []
    for index, (value, lower_bound, weight) in enumerate(
        zip(values, lower_bounds, scaled_weights, strict=True)
    ):
        blocks.append(_PavaBlock(index, index + 1, weight, value, lower_bound))
        while len(blocks) >= 2:
            previous = blocks[-2]
            current = blocks[-1]
            if previous.level <= current.level:
                break
            total_weight = previous.weight + current.weight
            mean = previous.mean + (current.mean - previous.mean) * (
                current.weight / total_weight
            )
            blocks[-2:] = [
                _PavaBlock(
                    previous.start,
                    current.end,
                    total_weight,
                    mean,
                    max(previous.lower_bound, current.lower_bound),
                )
            ]
    result = [0.0] * len(values)
    for block in blocks:
        for index in range(block.start, block.end):
            result[index] = block.level
    return result
