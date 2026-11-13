"""Tests for sampling coordinate strip-sweep partitions.

Created: 2026-11-12T16:00:00+01:00
Author: Michel Bierlaire, EPFL
"""

from collections.abc import Iterator
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.operators import spatial_strip_geometry
from multiobjective_vns.multiday_vrp.operators.spatial_strip_geometry import (
    SpatialDeliveryPoint,
    sample_sweep_partitions,
)
from multiobjective_vns.random_source import RandomSource


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 10000)

    def random(self) -> float:
        return next(self._draws)


def test_positive_area_sweeps_partition_every_package_across_routes() -> None:
    points = (
        SpatialDeliveryPoint('p1', 'n1', 0.0, 0.0),
        SpatialDeliveryPoint('p2', 'n2', 1.0, 0.0),
        SpatialDeliveryPoint('p3', 'n3', 0.0, 1.0),
        SpatialDeliveryPoint('p4', 'n4', 1.0, 1.0),
    )

    alternatives = sample_sweep_partitions(
        points,
        route_count=2,
        max_alternatives=12,
        random_source=cast(RandomSource, _ZeroRandomSource()),
    )

    assert alternatives
    assert len(set(alternatives)) == len(alternatives)
    assert all(
        set(alternative.package_order) == {'p1', 'p2', 'p3', 'p4'}
        for alternative in alternatives
    )
    assert all(len(alternative.cut_positions) == 1 for alternative in alternatives)
    assert all(
        1 <= alternative.cut_positions[0] < len(points) for alternative in alternatives
    )
    assert all(
        sorted(alternative.route_assignment) == [0, 1] for alternative in alternatives
    )


def test_collinear_sweep_uses_farthest_pair_order_and_reverse() -> None:
    points = (
        SpatialDeliveryPoint('p1', 'n1', 0.0, 0.0),
        SpatialDeliveryPoint('p2', 'n2', 1.0, 0.0),
        SpatialDeliveryPoint('p3', 'n3', 2.0, 0.0),
    )

    alternatives = sample_sweep_partitions(
        points,
        route_count=1,
        max_alternatives=2,
        random_source=cast(RandomSource, _ZeroRandomSource()),
    )

    assert tuple(item.package_order for item in alternatives) == (
        ('p1', 'p2', 'p3'),
        ('p3', 'p2', 'p1'),
    )


def test_positive_area_geometry_preserves_parameterized_sweep_choices() -> None:
    points = (
        SpatialDeliveryPoint('p1', 'n1', 0.0, 0.0),
        SpatialDeliveryPoint('p2', 'n2', 1.0, 0.0),
        SpatialDeliveryPoint('p3', 'n3', 0.0, 1.0),
        SpatialDeliveryPoint('p4', 'n4', 1.0, 1.0),
    )

    alternatives = sample_sweep_partitions(
        points,
        route_count=1,
        max_alternatives=1000,
        random_source=cast(RandomSource, _ZeroRandomSource()),
    )

    configurations = tuple(
        (
            item.sweep_order.orientation_index,
            item.sweep_order.width_multiplier,
            item.sweep_order.offset_multiplier,
            item.sweep_order.reverse,
        )
        for item in alternatives
    )
    assert len(alternatives) == 160
    assert len(set(configurations)) == len(configurations)
    assert len({item.package_order for item in alternatives}) < len(alternatives)


def test_repeated_geometry_reuses_sweep_order_calculation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points = (
        SpatialDeliveryPoint('cache-a', 'cache-node-a', -13.0, 2.0),
        SpatialDeliveryPoint('cache-b', 'cache-node-b', -7.0, 5.0),
        SpatialDeliveryPoint('cache-c', 'cache-node-c', -4.0, 16.0),
        SpatialDeliveryPoint('cache-d', 'cache-node-d', -19.0, 11.0),
    )
    original_cos = spatial_strip_geometry.cos
    calls = 0

    def counted_cos(angle: float) -> float:
        nonlocal calls
        calls += 1
        return original_cos(angle)

    monkeypatch.setattr(spatial_strip_geometry, 'cos', counted_cos)
    random_source = cast(RandomSource, _ZeroRandomSource())
    sample_sweep_partitions(
        points,
        route_count=1,
        max_alternatives=1,
        random_source=random_source,
    )
    calls_after_first_sample = calls
    sample_sweep_partitions(
        points,
        route_count=1,
        max_alternatives=1,
        random_source=random_source,
    )

    assert calls_after_first_sample > 0
    assert calls == calls_after_first_sample


def test_coincident_points_use_stable_identifier_order_and_reverse() -> None:
    points = (
        SpatialDeliveryPoint('p2', 'n2', 7.0, -4.0),
        SpatialDeliveryPoint('p1', 'n1', 7.0, -4.0),
        SpatialDeliveryPoint('p3', 'n3', 7.0, -4.0),
    )

    alternatives = sample_sweep_partitions(
        points,
        route_count=1,
        max_alternatives=2,
        random_source=cast(RandomSource, _ZeroRandomSource()),
    )

    assert tuple(item.package_order for item in alternatives) == (
        ('p1', 'p2', 'p3'),
        ('p3', 'p2', 'p1'),
    )
