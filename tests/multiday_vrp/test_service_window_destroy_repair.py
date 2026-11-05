"""Tests for the time-window-focused activity destroy-and-repair Operator.

Created: 2026-11-04T09:43:07+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    StopScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
    WindowScenarioAssessment,
)
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    InterventionTimeWindowRecord,
    LoadedVrpInstance,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.service_window_repair import (
    TimeWindowFocusedDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _test_plan


class _LateRandomSource:
    def random(self) -> float:
        return (2**52 + 1) / 2**53


def _windowed_plan(
    root: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _test_plan(root)
    interventions = (
        InterventionRecord('high-penalty', 'b', 0.0, 1),
        InterventionRecord('low-penalty', 'c', 0.0, 1),
    )
    windows = (
        InterventionTimeWindowRecord('high-penalty', 1, 0, 10.0, 20.0),
        InterventionTimeWindowRecord('low-penalty', 1, 0, 10.0, 20.0),
    )
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            interventions=interventions,
            intervention_time_windows=windows,
        )
    )
    route = base.routes[0]
    changed_route = replace(
        route,
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'high-penalty'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
            ActivityStop(
                ActivityRef('intervention', 'low-penalty'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
    )
    return instance, objectives, replace(base, routes=(changed_route, *base.routes[1:]))


def _analysis(solution: VrpSolution) -> VrpOperatorAnalysis:
    route = solution.routes[0]
    guidance = (100.0, 1.0)
    stops = tuple(
        StopScenarioEvaluation(
            stop_index=index,
            stop_kind='activity',
            node_id=node_id,
            arrival_time=5.0,
            service_start=10.0,
            service_finish=10.0,
            departure_time=10.0,
            break_start=None,
            break_finish=None,
            activity_ref=cast(ActivityStop, stop).activity_ref,
            transfer_step_ref=None,
            window_assessments_by_objective=(
                WindowScenarioAssessment('vehicles-used', 0, 0.0, 0.0, guidance[index]),
            ),
        )
        for index, (node_id, stop) in enumerate(
            zip(('b', 'c'), route.stops, strict=True)
        )
    )
    scenario = VrpScenarioEvaluation(
        'scenario-1',
        1.0,
        (
            VehicleDayScenarioEvaluation(
                route.vehicle_id,
                route.day_index,
                0.0,
                100.0,
                stops,
                (),
            ),
        ),
    )
    return VrpOperatorAnalysis(True, (scenario,), (), ())


def test_destroy_repair_targets_worst_windowed_activity_and_returns_candidate(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(tmp_path)

    def assessor(*_args: object) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (0.0,), _analysis(base))

    operator = TimeWindowFocusedDestroyRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=3,
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
            cast(RandomSource, _LateRandomSource()),
        )
    )

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    base_position = next(
        (route_index, stop_index)
        for route_index, route in enumerate(base.routes)
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('intervention', 'high-penalty')
    )
    changed_position = next(
        (route_index, stop_index)
        for route_index, route in enumerate(changed.routes)
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('intervention', 'high-penalty')
    )
    assert changed_position != base_position


def test_destroy_repair_removes_multiple_targets_by_original_positions(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(tmp_path)

    def assessor(*_args: object) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (0.0,), _analysis(base))

    operator = TimeWindowFocusedDestroyRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=3,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _LateRandomSource()),
        )
    )

    assert result.m == 2
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert changed != base


def test_destroy_repair_returns_no_candidate_when_no_windowed_service_exists(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = TimeWindowFocusedDestroyRepairOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda *_args: ApplicationEvaluation(True, True, (0.0,)),
        ),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=2,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _LateRandomSource()),
        )
    )

    assert result.m == 0
    assert result.candidate is None
