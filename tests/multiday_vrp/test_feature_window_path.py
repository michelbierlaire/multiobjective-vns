"""Tests for service-window-guided route-path Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.feature_path import (
    TimeWindowGuidedRouteRerouteOperator,
)
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


def test_window_guided_reroute_uses_any_leg_without_positive_guidance(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = TimeWindowGuidedRouteRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        objective_id=objectives.objectives[0].objective_id,
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
