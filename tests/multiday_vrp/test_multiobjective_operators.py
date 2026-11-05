"""Tests for Pareto trade-off VRP Operators.

Created: 2026-11-04T09:03:56+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    VrpInterventionData,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.pareto_tradeoff import (
    ParetoTradeoffOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _test_plan


@dataclass(frozen=True, slots=True)
class _Objective:
    objective_id: str
    component_id: str
    configuration_id: str

    def compare(self, left: object, right: object) -> ObjectiveComparison:
        left_value = cast(float, left)
        right_value = cast(float, right)
        if left_value < right_value:
            return ObjectiveComparison.LESS
        if left_value > right_value:
            return ObjectiveComparison.GREATER
        return ObjectiveComparison.EQUAL

    def format(self, value: object) -> str:
        return str(value)

    def __str__(self) -> str:
        return self.objective_id


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 20000)

    def random(self) -> float:
        return next(self._draws)


def _two_objectives() -> ObjectiveDeclaration:
    return ObjectiveDeclaration(
        (
            _Objective('time', 'test:time', 'test:time-v1'),
            _Objective('risk', 'test:risk', 'test:risk-v1'),
        )
    )


def _context(solution: VrpSolution, *, k: int = 1) -> CandidateContext:
    return CandidateContext(
        solution,
        k,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )


def test_pareto_timing_move_selects_from_nondominated_alternatives(
    tmp_path: Path,
) -> None:
    instance, _objectives, base = _test_plan(tmp_path)
    objectives = _two_objectives()
    routes = list(base.routes)
    routes[0] = replace(routes[0], start_time=50.0)
    base = replace(base, routes=tuple(routes))

    def assessor(
        _instance: object,
        _objectives: object,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        start_time = solution.routes[0].start_time
        if start_time < 50.0:
            values = (0.0, 2.0)
        elif start_time > 50.0:
            values = (2.0, 0.0)
        else:
            values = (1.0, 1.0)
        return ApplicationEvaluation(True, True, values)

    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='timing',
        max_neighborhood_size=2,
        time_step=1.0,
        max_candidate_alternatives=8,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert candidate.routes[0].start_time in {49.0, 51.0}


def test_pareto_path_move_returns_a_complete_changed_plan(tmp_path: Path) -> None:
    instance, _single_objective, base = _test_plan(tmp_path)
    objectives = _two_objectives()

    def assessor(
        _instance: object,
        _objectives: object,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        length = float(len(solution.routes[0].legs[0].arc_ids))
        return ApplicationEvaluation(True, True, (length, -length))

    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='path',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert candidate.routes[0].legs[0] != base.routes[0].legs[0]


def test_pareto_activity_move_relocates_an_activity(tmp_path: Path) -> None:
    instance, _single_objective, base = _test_plan(tmp_path)
    objectives = _two_objectives()
    empty_interventions = VrpInterventionData((), (), (), (), ())
    instance = instance._replace(
        intervention_data=empty_interventions._replace(
            interventions=(InterventionRecord('repair', 'b', 10.0, 1),)
        )
    )
    routes = list(base.routes)
    stops = list(routes[0].stops)
    stops[0] = ActivityStop(
        ActivityRef('intervention', 'repair'),
        frozenset(),
        0.0,
        0.0,
        0.0,
        False,
    )
    routes[0] = replace(routes[0], stops=tuple(stops))
    base = replace(base, routes=tuple(routes))

    def assessor(
        _instance: object,
        _objectives: object,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (0.0, 0.0))

    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='activity',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    location = next(
        (route_index, stop_index)
        for route_index, route in enumerate(candidate.routes)
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('intervention', 'repair')
    )
    assert location != (0, 0)
