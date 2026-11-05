"""Tests for generic route timing adjustments.

Created: 2026-11-03T21:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.timing import TimingAdjustmentOperator
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _always_assess, _test_plan


class _SequenceRandomSource:
    def __init__(self, values: tuple[float, ...]) -> None:
        self._values: Iterator[float] = iter(values)

    def random(self) -> float:
        return next(self._values)


def test_timing_adjustment_changes_one_selected_timing_field(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = TimingAdjustmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        time_step=5.0,
        max_neighborhood_size=2,
    )
    context = CandidateContext(
        base=base,
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, _SequenceRandomSource((0.0, 0.75))),
    )

    result = operator.call(context)

    assert result.m == 1
    assert result.candidate is not None
    changed = result.candidate.solution
    assert isinstance(changed, VrpSolution)
    assert changed.routes[0].start_time == base.routes[0].start_time + 5.0


def test_timing_adjustment_skips_field_values_outside_their_domain(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = TimingAdjustmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        time_step=5.0,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base=base,
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, _SequenceRandomSource((0.0, 0.0))),
    )

    result = operator.call(context)

    assert result.m == 0
    assert result.candidate is None


def test_restoration_timing_adjustment_accepts_an_infeasible_base(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        feasible = solution.routes[0].start_time > base.routes[0].start_time
        return ApplicationEvaluation(True, feasible, (0.0,))

    operator = TimingAdjustmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        time_step=5.0,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base=base,
        base_evaluation=None,
        k=1,
        ensure_feasible=True,
        operator_state=None,
        random_source=cast(RandomSource, _SequenceRandomSource((0.0, 0.75))),
    )

    result = operator.call(context)

    assert result.m == 1
    assert result.candidate is not None
    changed = result.candidate.solution
    assert isinstance(changed, VrpSolution)
    assert changed.routes[0].start_time > base.routes[0].start_time
