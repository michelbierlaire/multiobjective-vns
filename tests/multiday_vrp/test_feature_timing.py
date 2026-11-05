"""Tests for feature-guided timing Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.feature_timing import (
    FeatureTimingOperator,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_feature_path_operators import _analysis
from .test_universal_route_order import _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


def test_scenario_differential_timing_targets_route_with_variable_travel(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assessor(*_args: object) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (1.0,), _analysis(base))

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='scenario_differential',
        objective_id=objectives.objectives[0].objective_id,
        time_step=5.0,
        max_neighborhood_size=1,
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
    assert changed.routes[0] == base.routes[0]
    assert changed.routes[1].start_time != base.routes[1].start_time


def test_feature_timing_validates_supported_mode(tmp_path: Path) -> None:
    instance, objectives, _base = _test_plan(tmp_path)
    with pytest.raises(ValueError, match='unsupported feature timing mode'):
        FeatureTimingOperator(
            instance,
            objectives,
            cast(
                VrpPlanAssessor,
                lambda *_args: ApplicationEvaluation(True, True, (1.0,)),
            ),
            mode='unknown',  # type: ignore[arg-type]
            objective_id=objectives.objectives[0].objective_id,
            time_step=5.0,
            max_neighborhood_size=1,
        )
