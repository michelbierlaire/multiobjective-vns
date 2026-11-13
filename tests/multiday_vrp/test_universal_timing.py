"""Tests for generic route timing adjustments.

Created: 2026-11-03T21:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import replace
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
        max_candidate_alternatives=1,
    )
    context = CandidateContext(
        base=base,
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, _SequenceRandomSource((0.0, 0.75))),
    )

    result = operator.call(context)

    assert result.candidates
    assert result.candidates[0].m == 1
    changed = result.candidates[0].candidate.solution
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
        max_candidate_alternatives=1,
    )
    context = CandidateContext(
        base=base,
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, _SequenceRandomSource((0.0, 0.0))),
    )

    result = operator.call(context)

    assert result.candidates == ()
    assert not result.candidates


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
        max_candidate_alternatives=1,
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

    assert result.candidates
    assert result.candidates[0].m == 1
    changed = result.candidates[0].candidate.solution
    assert isinstance(changed, VrpSolution)
    assert changed.routes[0].start_time > base.routes[0].start_time


def test_timing_adjustment_returns_distinct_trials_up_to_context_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = TimingAdjustmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        time_step=5.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=16,
    )
    context = CandidateContext(
        base=base,
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, random.Random(17)),
        max_candidates=4,
    )

    result = operator.call(context)

    assert len(result.candidates) == 4
    solutions = tuple(proposal.candidate.solution for proposal in result.candidates)
    assert len(set(solutions)) == 4
    assert all(proposal.m == 1 for proposal in result.candidates)
    assert all(solution != base for solution in solutions)


def test_timing_adjustment_assesses_the_final_sequential_trial(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    assessed_start_times: list[float] = []

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        start_time = solution.routes[0].start_time
        assessed_start_times.append(start_time)
        return ApplicationEvaluation(
            True,
            start_time >= base.routes[0].start_time + 10.0,
            (0.0,),
        )

    operator = TimingAdjustmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        time_step=5.0,
        max_neighborhood_size=2,
        max_candidate_alternatives=1,
    )
    context = CandidateContext(
        base=base,
        k=2,
        ensure_feasible=True,
        operator_state=None,
        random_source=cast(RandomSource, _SequenceRandomSource((0.0, 0.75, 0.0, 0.75))),
        max_candidates=1,
    )

    result = operator.call(context)

    assert result.candidates
    proposal = result.candidates[0]
    assert proposal.m == 2
    changed = proposal.candidate.solution
    assert isinstance(changed, VrpSolution)
    assert changed.routes[0].start_time == base.routes[0].start_time + 10.0
    assert assessed_start_times == [base.routes[0].start_time + 10.0]


def test_timing_adjustment_preserves_break_duration_within_wait(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    route = base.routes[0]
    stops = list(route.stops)
    stops[0] = replace(stops[0], wait=5.0, break_duration=5.0)
    base = replace(base, routes=(replace(route, stops=tuple(stops)), *base.routes[1:]))
    operator = TimingAdjustmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        time_step=5.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
    )
    context = CandidateContext(
        base=base,
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, _SequenceRandomSource((0.3, 0.75))),
    )

    result = operator.call(context)

    assert result.candidates == ()
