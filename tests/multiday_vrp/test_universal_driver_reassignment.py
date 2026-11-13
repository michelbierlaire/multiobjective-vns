"""Tests for generic driver reassignment.

Created: 2026-11-03T21:30:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    PersonRecord,
    VehicleDrivingSkillRecord,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.driver_reassignment import (
    DriverReassignmentOperator,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _always_assess, _context, _test_plan


def test_driver_reassignment_selects_an_authorized_alternative(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = (
        *instance.personnel_data.personnel,
        PersonRecord('driver-2', True, None),
    )
    instance = instance._replace(
        personnel_data=instance.personnel_data._replace(personnel=personnel)
    )
    operator = DriverReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=3,
    )

    result = operator.call(_context(base))

    assert result.candidates
    assert result.candidates[0].m == 1
    selected = result.candidates[0].candidate.solution
    assert isinstance(selected, VrpSolution)
    assert selected.routes[0].driver_id == 'driver-2'


def test_driver_reassignment_respects_required_vehicle_skills(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = (
        *instance.personnel_data.personnel,
        PersonRecord('driver-2', True, None),
    )
    fleet = instance.fleet_data._replace(
        vehicle_driving_skills=(
            VehicleDrivingSkillRecord('vehicle-1', 'license-x'),
            VehicleDrivingSkillRecord('vehicle-2', 'license-x'),
        )
    )
    instance = instance._replace(
        fleet_data=fleet,
        personnel_data=instance.personnel_data._replace(personnel=personnel),
    )
    operator = DriverReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
    )

    result = operator.call(_context(base))

    assert result.candidates == ()
    assert not result.candidates


def test_driver_reassignment_excludes_unauthorized_people(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = (
        *instance.personnel_data.personnel,
        PersonRecord('driver-2', False, None),
    )
    instance = instance._replace(
        personnel_data=instance.personnel_data._replace(personnel=personnel)
    )
    operator = DriverReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
    )

    result = operator.call(_context(base))

    assert result.candidates == ()


def test_driver_reassignment_returns_distinct_plans_up_to_context_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = (
        *instance.personnel_data.personnel,
        PersonRecord('driver-2', True, None),
        PersonRecord('driver-3', True, None),
    )
    instance = instance._replace(
        personnel_data=instance.personnel_data._replace(personnel=personnel)
    )
    operator = DriverReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
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


def test_driver_reassignment_checks_feasibility_after_sequential_changes(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = (
        *instance.personnel_data.personnel,
        PersonRecord('driver-2', True, None),
    )
    instance = instance._replace(
        personnel_data=instance.personnel_data._replace(personnel=personnel)
    )
    assessed: list[VrpSolution] = []

    def assessor(
        _instance,
        _objectives,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        assessed.append(solution)
        feasible = all(route.driver_id == 'driver-2' for route in solution.routes)
        return ApplicationEvaluation(True, feasible, (0.0,))

    operator = DriverReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        max_neighborhood_size=2,
        max_candidate_alternatives=1,
    )
    context = CandidateContext(
        base=base,
        k=2,
        ensure_feasible=True,
        operator_state=None,
        random_source=cast(RandomSource, random.Random(4)),
        max_candidates=1,
    )

    result = operator.call(context)

    assert result.candidates
    proposal = result.candidates[0]
    assert proposal.m == 2
    candidate = proposal.candidate.solution
    assert isinstance(candidate, VrpSolution)
    assert all(route.driver_id == 'driver-2' for route in candidate.routes)
    assert assessed == [candidate]
