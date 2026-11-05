"""Tests for optional service coverage Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import PackageRecord
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.service_coverage import (
    ServiceCoverageChangeOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteLeg,
    TransferOperationStop,
    TransferStep,
    TransferStepRef,
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


def test_removing_package_pickup_removes_dependent_service_chain(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0)
    instance = instance._replace(
        settings=instance.settings._replace(service_coverage_mode='penalized_omission'),
        package_data=instance.package_data._replace(packages=(package,)),
    )
    route = base.routes[0]
    stops = list(route.stops)
    stops[0] = ActivityStop(
        ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    stops[1] = ActivityStop(
        ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    routes = list(base.routes)
    routes[0] = replace(route, stops=tuple(stops))
    base = replace(base, routes=tuple(routes))
    operator = ServiceCoverageChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
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
        for route in changed.routes
        for stop in route.stops
    )


def test_removing_package_pickup_also_removes_transfer_operations(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 1, 0.0, 0.0)
    instance = instance._replace(
        settings=instance.settings._replace(service_coverage_mode='penalized_omission'),
        package_data=instance.package_data._replace(packages=(package,)),
    )
    first_route, second_route = base.routes
    out_stop = TransferOperationStop(
        TransferStepRef('pkg', 1), 'out', 0.0, 0.0, 0.0, False
    )
    in_stop = TransferOperationStop(
        TransferStepRef('pkg', 1), 'in', 0.0, 0.0, 0.0, False
    )
    routes = (
        replace(
            first_route,
            stops=(
                ActivityStop(
                    ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
                ),
                out_stop,
            ),
            legs=(RouteLeg(('a-b',)), RouteLeg(('b-c',)), RouteLeg(('c-a',))),
        ),
        replace(
            second_route,
            stops=(
                in_stop,
                ActivityStop(
                    ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
                ),
            ),
            legs=(RouteLeg(('a-c',)), RouteLeg(()), RouteLeg(('c-a',))),
        ),
    )
    base = VrpSolution(routes, (TransferStep('pkg', 1, 'c'),))
    operator = ServiceCoverageChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
        )
    )

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert changed.transfer_steps == ()
    assert not any(
        isinstance(stop, TransferOperationStop)
        for route in changed.routes
        for stop in route.stops
    )
