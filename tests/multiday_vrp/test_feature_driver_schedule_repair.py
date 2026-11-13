"""Tests for multi-route driver schedule destroy-and-repair.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    PersonRecord,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.driver_schedule_repair import (
    DriverScheduleDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _always_assess, _test_plan


class _HighRandomSource:
    def random(self) -> float:
        return 0.99


def test_driver_schedule_repair_reassigns_selected_routes_jointly(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = instance._replace(
        personnel_data=instance.personnel_data._replace(
            personnel=(
                *instance.personnel_data.personnel,
                PersonRecord('driver-2', True, None),
            )
        )
    )
    operator = DriverScheduleDestroyRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, _HighRandomSource()),
    )

    result = operator.call(context)

    assert result.candidates
    assert result.candidates[0].m == 2
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert tuple(route.driver_id for route in candidate.routes) == (
        'driver-2',
        'driver-2',
    )


def test_driver_schedule_repair_returns_objective_ranked_candidates(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = instance._replace(
        personnel_data=instance.personnel_data._replace(
            personnel=(
                *instance.personnel_data.personnel,
                PersonRecord('driver-2', True, None),
                PersonRecord('driver-3', True, None),
            )
        )
    )
    driver_scores = {'driver-1': 0.0, 'driver-2': 2.0, 'driver-3': 1.0}

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        score = sum(
            (position + 1) * driver_scores[route.driver_id]
            for position, route in enumerate(solution.routes)
        )
        return ApplicationEvaluation(True, True, (score,))

    operator = DriverScheduleDestroyRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=32,
        objective_id=objectives.objectives[0].objective_id,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, random.Random(19)),
        max_candidates=8,
    )

    result = operator.call(context)

    assert len(result.candidates) == 8
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(solutions)) == len(solutions)
    assert all(solution != base for solution in solutions)
    assert all(proposal.m in (1, 2) for proposal in result.candidates)
    scores = tuple(
        sum(
            (position + 1) * driver_scores[route.driver_id]
            for position, route in enumerate(solution.routes)
        )
        for solution in solutions
    )
    assert scores == tuple(sorted(scores))


def test_driver_schedule_repair_returns_randomized_candidates_without_objective(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = instance._replace(
        personnel_data=instance.personnel_data._replace(
            personnel=(
                *instance.personnel_data.personnel,
                PersonRecord('driver-2', True, None),
                PersonRecord('driver-3', True, None),
            )
        )
    )
    operator = DriverScheduleDestroyRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=32,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, random.Random(21)),
        max_candidates=5,
    )

    result = operator.call(context)

    assert len(result.candidates) == 5
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(solutions)) == len(solutions)
    assert all(solution != base for solution in solutions)
    assert all(proposal.m in (1, 2) for proposal in result.candidates)


def test_driver_schedule_repair_caps_distinct_assignment_trials(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = instance._replace(
        personnel_data=instance.personnel_data._replace(
            personnel=(
                *instance.personnel_data.personnel,
                PersonRecord('driver-2', True, None),
                PersonRecord('driver-3', True, None),
            )
        )
    )
    assessments = 0

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return ApplicationEvaluation(True, True, (0.0,))

    operator = DriverScheduleDestroyRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=3,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, random.Random(23)),
        max_candidates=8,
    )

    result = operator.call(context)

    assert len(result.candidates) <= 3
    assert assessments <= 3
    assert all(proposal.m in (1, 2) for proposal in result.candidates)


def test_driver_schedule_repair_respects_feasibility_request(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = instance._replace(
        personnel_data=instance.personnel_data._replace(
            personnel=(
                *instance.personnel_data.personnel,
                PersonRecord('driver-2', True, None),
            )
        )
    )

    def infeasible_assessment(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, False, (0.0,))

    operator = DriverScheduleDestroyRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, infeasible_assessment),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
    )
    context = CandidateContext(
        base,
        2,
        True,
        None,
        cast(RandomSource, random.Random(29)),
        max_candidates=8,
    )

    result = operator.call(context)

    assert result.candidates == ()
