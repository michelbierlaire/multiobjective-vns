"""Tests for feature-specific package-chain repair Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    PackageRecord,
    VehiclePackageCompatibilityRecord,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.package_chain import (
    PackageChainRelocationOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    VrpSolution,
)
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


def test_compatibility_repair_moves_package_chain_to_compatible_route(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0)
    package_data = instance.package_data._replace(
        packages=(package,),
        vehicle_package_compatibility=(
            VehiclePackageCompatibilityRecord('vehicle-1', 'pkg', False),
            VehiclePackageCompatibilityRecord('vehicle-2', 'pkg', True),
        ),
    )
    instance = instance._replace(package_data=package_data)
    routes = list(base.routes)
    first_stops = list(routes[0].stops)
    first_stops[0] = ActivityStop(
        ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    first_stops[1] = ActivityStop(
        ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    routes[0] = replace(routes[0], stops=tuple(first_stops))
    base = replace(base, routes=tuple(routes))
    operator = PackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='compatibility',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )

    result = operator.call(context)

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert not any(
        isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'pkg'
        for stop in changed.routes[0].stops
    )
    package_stops = tuple(
        stop
        for stop in changed.routes[1].stops
        if isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'pkg'
    )
    assert tuple(stop.activity_ref.kind for stop in package_stops) == (
        'pickup',
        'delivery',
    )
