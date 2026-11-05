"""Tests for time-window regret-based service reinsertion.

Created: 2026-11-05T00:59:12+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    VrpObjectiveFunction,
)
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionTimeWindowRecord,
    LoadedVrpInstance,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.service_window_regret import (
    TimeWindowRegretBasedReinsertionOperator,
)
from multiobjective_vns.multiday_vrp.solution import ActivityStop, VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_vehicle_route_assignment import (
    _assert_route_legs_connect,
    _context,
    _service_plan,
)


def test_regret_reinsertion_moves_windowed_service_to_best_route(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_plan(tmp_path)
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            intervention_time_windows=(
                InterventionTimeWindowRecord('job-1', 1, 1, 0.0, 10.0),
            )
        )
    )
    second_objective = VrpObjectiveFunction(
        'duration',
        'test:duration-objective',
        'test:duration-objective-v1',
    )
    objectives = ObjectiveDeclaration((*objectives.objectives, second_objective))

    def assess_route_choice(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        job_one_route = next(
            route
            for route in solution.routes
            if any(
                isinstance(stop, ActivityStop)
                and stop.activity_ref.activity_id == 'job-1'
                for stop in route.stops
            )
        )
        target_value = 0.0 if job_one_route.vehicle_id == 'vehicle-2' else 10.0
        return ApplicationEvaluation(
            True,
            True,
            (100.0 - target_value, target_value)
            + tuple(0.0 for _ in declaration.objectives[2:]),
        )

    operator = TimeWindowRegretBasedReinsertionOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess_route_choice),
        objective_id=second_objective.objective_id,
        max_neighborhood_size=2,
        max_candidate_alternatives=12,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert candidate != base
    job_one_route = next(
        route
        for route in candidate.routes
        if any(
            isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'job-1'
            for stop in route.stops
        )
    )
    assert job_one_route.vehicle_id == 'vehicle-2'
    assert {
        stop.activity_ref.activity_id
        for route in candidate.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop)
    } == {'job-1', 'job-2', 'job-3', 'job-4'}
    _assert_route_legs_connect(instance, candidate)
