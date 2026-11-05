"""Tests for exact structural VRP solution identity.

Created: 2026-11-03T23:52:10+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import inf, nan

import pytest

from multiobjective_vns.multiday_vrp import VrpSolutionIdentity
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)


def _solution(*, start_time: float = 0.0, reverse: bool = False) -> VrpSolution:
    stops: tuple[ActivityStop, ...] = (
        ActivityStop(
            ActivityRef('intervention', 'one'), frozenset(), 0.0, 0.0, 0.0, False
        ),
        ActivityStop(
            ActivityRef('intervention', 'two'), frozenset(), 0.0, 0.0, 0.0, False
        ),
    )
    if reverse:
        stops = tuple(reversed(stops))
    route = VehicleDayRoute(
        'vehicle-1',
        1,
        'depot-1',
        start_time,
        'driver-1',
        stops,
        (RouteLeg(('arc-a',)), RouteLeg(('arc-b',)), RouteLeg(('arc-c',))),
    )
    return VrpSolution((route,), ())


def test_identity_uses_exact_structural_equality_and_order() -> None:
    identity = VrpSolutionIdentity()

    assert identity.equivalent(_solution(), _solution())
    assert not identity.equivalent(_solution(), _solution(reverse=True))
    assert not identity.equivalent(_solution(), _solution(start_time=0.5))


@pytest.mark.parametrize(
    ('left', 'right', 'expected'),
    [
        (nan, nan, True),
        (inf, inf, True),
        (inf, -inf, False),
        (0.0, -0.0, True),
    ],
)
def test_identity_has_deterministic_float_special_value_rules(
    left: float, right: float, expected: bool
) -> None:
    identity = VrpSolutionIdentity()

    assert (
        identity.equivalent(_solution(start_time=left), _solution(start_time=right))
        is expected
    )


def test_identity_requires_exact_vrp_solution_root_type() -> None:
    with pytest.raises(TypeError, match='VrpSolution'):
        VrpSolutionIdentity().equivalent(_solution(), ())
