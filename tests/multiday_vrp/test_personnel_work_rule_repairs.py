"""Tests for personnel and work-rule repair Operators.

Created: 2026-11-04T09:56:38+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.records import (
    HardFeasibilityFinding,
    ResourceFinding,
    VrpOperatorAnalysis,
)
from multiobjective_vns.multiday_vrp.extensions import VrpScenarioEvaluation
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    PersonnelAvailabilityRecord,
    PersonnelDrivingSkillRecord,
    PersonRecord,
    VehicleDrivingSkillRecord,
    VrpInterventionData,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.personnel_repair import (
    PersonnelRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.personnel_repair_targets import (
    select_targets,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    VrpSolution,
)
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _test_plan


def test_target_selection_identifies_the_driver_missing_a_vehicle_skill(
    tmp_path: Path,
) -> None:
    instance, _objectives, base = _test_plan(tmp_path)
    fleet = instance.fleet_data._replace(
        vehicle_driving_skills=(VehicleDrivingSkillRecord('vehicle-1', 'heavy'),)
    )
    instance = instance._replace(fleet_data=fleet)

    targets = select_targets(instance, 'skill_guided', base, None, None)

    assert tuple(item.duty.route_index for item in targets) == (0,)


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


def _context(
    base: VrpSolution,
    *,
    k: int = 1,
    ensure_feasible: bool = False,
) -> CandidateContext:
    return CandidateContext(
        base=base,
        k=k,
        ensure_feasible=ensure_feasible,
        operator_state=None,
        random_source=cast(RandomSource, _ZeroRandomSource()),
    )


def test_skill_guided_repair_can_restore_an_infeasible_driver_assignment(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    fleet = instance.fleet_data._replace(
        vehicle_driving_skills=(VehicleDrivingSkillRecord('vehicle-1', 'heavy'),)
    )
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
        ),
        personnel_driving_skills=(PersonnelDrivingSkillRecord('driver-2', 'heavy'),),
    )
    instance = instance._replace(fleet_data=fleet, personnel_data=personnel)

    def assess(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        valid = all(
            route.vehicle_id != 'vehicle-1' or route.driver_id == 'driver-2'
            for route in solution.routes
        )
        return ApplicationEvaluation(True, valid, (0.0,))

    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='skill_guided',
        max_neighborhood_size=2,
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert candidate.routes[0].driver_id == 'driver-2'


def test_personnel_availability_repair_replaces_a_restricted_team_member(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('person-2', False, None),
            PersonRecord('person-3', False, None),
        ),
        personnel_availability=(
            PersonnelAvailabilityRecord('person-2', 5.0, 20.0),
            PersonnelAvailabilityRecord('person-3', 0.0, 1440.0),
        ),
    )
    intervention = VrpInterventionData(
        (InterventionRecord('repair', 'b', 10.0, 1),), (), (), (), ()
    )
    routes = list(base.routes)
    stops = list(routes[0].stops)
    stops[0] = ActivityStop(
        ActivityRef('intervention', 'repair'),
        frozenset({'person-2'}),
        0.0,
        0.0,
        0.0,
        False,
    )
    routes[0] = replace(routes[0], stops=tuple(stops))
    base = replace(base, routes=tuple(routes))
    instance = instance._replace(
        personnel_data=personnel,
        intervention_data=intervention,
    )
    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_feasible),
        mode='availability',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    changed_stop = cast(ActivityStop, candidate.routes[0].stops[0])
    assert 'person-2' not in changed_stop.team_member_ids


def test_staff_continuity_repair_reassigns_a_route_to_a_compatible_person(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            type(instance.personnel_data.personnel[0])('driver-1', True, 'depot-b'),
            PersonRecord('driver-2', True, 'depot-a'),
        )
    )
    routes = list(base.routes)
    routes[1] = replace(routes[1], driver_id='driver-2')
    base = replace(base, routes=tuple(routes))
    instance = instance._replace(
        settings=instance.settings._replace(staff_depot_mode='depot_continuity'),
        personnel_data=personnel,
    )
    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_feasible),
        mode='staff_continuity',
        max_neighborhood_size=1,
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert candidate.routes[0].driver_id == 'driver-2'


def test_work_rule_repair_uses_the_failing_person_finding_and_restores_feasibility(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
        )
    )
    instance = instance._replace(
        settings=instance.settings._replace(rule_treatment='compliance'),
        personnel_data=personnel,
    )
    objective_id = str(objectives.objectives[0].objective_id)

    def assess(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        failing = tuple(
            route for route in solution.routes if route.driver_id == 'driver-1'
        )
        if not failing:
            return ApplicationEvaluation(
                True, True, (0.0,), VrpOperatorAnalysis(True, (), (), ())
            )
        finding = ResourceFinding(
            'driver_work',
            'driver-1:daily_work:1',
            'scenario-000001',
            None,
            1,
            None,
            float(len(failing)),
            ((objective_id, float(len(failing))),),
        )
        analysis = VrpOperatorAnalysis(
            False,
            (VrpScenarioEvaluation('scenario-000001', 1.0, ()),),
            (finding,),
            (),
            # The evaluator's incomplete analysis boundary is exercised here.
            HardFeasibilityFinding('compliance_rule', 'work limit', 'scenario-000001'),
        )
        return ApplicationEvaluation(True, False, (float(len(failing)),), analysis)

    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='work_rule',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=2,
    )

    result = operator.call(_context(base, k=2, ensure_feasible=True))

    assert result.m == 2
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert all(route.driver_id == 'driver-2' for route in candidate.routes)


def _always_feasible(
    _instance: object, _objectives: object, _solution: VrpSolution
) -> ApplicationEvaluation:
    return ApplicationEvaluation(True, True, (0.0,))
