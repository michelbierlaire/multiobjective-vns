"""Tests for normalized input validation helpers.

Created: 2026-11-03T16:34:50+01:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.multiday_vrp.input_validation import (
    _intervals_cover_horizon,
    _numeric_tolerance,
)


def test_interval_coverage_accepts_adjacent_and_overlapping_intervals() -> None:
    assert _intervals_cover_horizon(((0.0, 4.0), (4.0, 8.0)), 0.0, 8.0)
    assert _intervals_cover_horizon(((0.0, 5.0), (3.0, 8.0)), 0.0, 8.0)


def test_interval_coverage_rejects_a_gap_larger_than_numeric_tolerance() -> None:
    assert not _intervals_cover_horizon(((0.0, 3.0), (3.1, 8.0)), 0.0, 8.0)


def test_interval_coverage_treats_roundoff_sized_gap_as_contiguous() -> None:
    assert _intervals_cover_horizon(((0.0, 4.0), (4.0 + 1e-10, 8.0)), 0.0, 8.0)


def test_numeric_tolerance_scales_with_compared_values() -> None:
    assert _numeric_tolerance(1.0, 2.0) < _numeric_tolerance(1e9, 2e9)
    assert _numeric_tolerance(1.0, 2.0) > 0.0
