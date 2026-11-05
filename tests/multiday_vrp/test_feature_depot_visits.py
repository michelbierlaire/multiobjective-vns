"""Tests for depot-visit insertion and removal Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.depot_visit import (
    DepotVisitOperator,
)
from multiobjective_vns.multiday_vrp.solution import DepotOnlyStop, VrpSolution
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


def _context(base: VrpSolution) -> CandidateContext:
    return CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )


def test_depot_visit_insertion_adds_depot_stop_and_paths(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='insert',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert (
        sum(
            type(stop) is DepotOnlyStop
            for route in changed.routes
            for stop in route.stops
        )
        == sum(
            type(stop) is DepotOnlyStop for route in base.routes for stop in route.stops
        )
        + 1
    )


def test_depot_visit_removal_removes_intermediate_depot_stop(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='remove',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert (
        sum(
            type(stop) is DepotOnlyStop
            for route in changed.routes
            for stop in route.stops
        )
        == sum(
            type(stop) is DepotOnlyStop for route in base.routes for stop in route.stops
        )
        - 1
    )
