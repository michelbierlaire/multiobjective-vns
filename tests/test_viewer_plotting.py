"""Tests for Pareto-view plot calculations.

Created: 2026-11-12T12:44:55+00:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.pareto_viewer.plotting import nearest_point


def test_nearest_point_returns_closest_id_within_tolerance() -> None:
    points = {'first': (10.0, 10.0), 'second': (30.0, 30.0)}

    assert nearest_point(points, 28.0, 31.0, tolerance=5.0) == 'second'
    assert nearest_point(points, 28.0, 31.0, tolerance=2.0) is None


def test_nearest_point_ties_keep_mapping_order() -> None:
    points = {'first': (8.0, 10.0), 'second': (12.0, 10.0)}

    assert nearest_point(points, 10.0, 10.0, tolerance=3.0) == 'first'
