"""Tests for feature-specific path Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    ArcTraversalEvaluation,
    LegScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import (
    EventRecord,
    EventTypeRecord,
    ObjectiveEventTypeRecord,
    VrpEventData,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.feature_path import (
    EventObjectiveRouteRerouteOperator,
    ScenarioDifferentialRerouteOperator,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


def _analysis(solution: VrpSolution) -> VrpOperatorAnalysis:
    scenarios = []
    for scenario_number, multiplier in ((1, 1.0), (2, 1.0)):
        vehicle_days = []
        for route_index, route in enumerate(solution.routes):
            legs = []
            for leg_index, leg in enumerate(route.legs):
                duration = multiplier
                if scenario_number == 2 and route_index == 1 and leg_index == 0:
                    duration = 8.0
                traversals = tuple(
                    ArcTraversalEvaluation(arc_id, 0.0, duration, duration)
                    for arc_id in leg.arc_ids
                )
                legs.append(LegScenarioEvaluation(leg_index, traversals))
            vehicle_days.append(
                VehicleDayScenarioEvaluation(
                    route.vehicle_id,
                    route.day_index,
                    0.0,
                    100.0,
                    (),
                    tuple(legs),
                )
            )
        scenarios.append(
            VrpScenarioEvaluation(
                f'scenario-{scenario_number}',
                0.5,
                tuple(vehicle_days),
            )
        )
    return VrpOperatorAnalysis(True, tuple(scenarios), (), ())


def _assessor(
    _instance: object,
    _objectives: object,
    solution: VrpSolution,
) -> ApplicationEvaluation:
    return ApplicationEvaluation(
        True, True, (float(len(solution.routes)),), _analysis(solution)
    )


def _context(solution: VrpSolution) -> CandidateContext:
    return CandidateContext(
        solution,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
        None,
    )


def test_scenario_differential_reroute_targets_variable_leg(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = ScenarioDifferentialRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assessor),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert changed.routes[0] == base.routes[0]
    assert changed.routes[1].legs[0] != base.routes[1].legs[0]


def test_event_objective_reroute_targets_event_leg(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    target_arc = base.routes[0].legs[0].arc_ids[0]
    event_data = VrpEventData(
        (EventTypeRecord('signal', 'arc'),),
        (EventRecord('signal-1', 'signal', target_arc),),
        (
            ObjectiveEventTypeRecord(
                str(objectives.objectives[0].objective_id), 'signal'
            ),
        ),
    )
    instance = instance._replace(event_data=event_data)
    operator = EventObjectiveRouteRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assessor),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert changed.routes[0].legs[0] != base.routes[0].legs[0]
    assert changed.routes[1] == base.routes[1]
