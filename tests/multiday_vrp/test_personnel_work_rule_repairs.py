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
from multiobjective_vns.multiday_vrp.extensions import (
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionQualificationRequiredRecord,
    InterventionRecord,
    PersonnelAvailabilityRecord,
    PersonnelDrivingSkillRecord,
    PersonnelInterventionQualificationRecord,
    PersonRecord,
    VehicleDrivingSkillRecord,
    VrpInterventionData,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.personnel_repair import (
    PersonnelRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.personnel_repair_support import (
    DutyTarget,
    RepairTarget,
    repair_score,
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


def test_skill_target_fallback_selects_routes_with_fewest_qualified_drivers(
    tmp_path: Path,
) -> None:
    instance, _objectives, base = _test_plan(tmp_path)
    fleet = instance.fleet_data._replace(
        vehicle_driving_skills=(VehicleDrivingSkillRecord('vehicle-1', 'heavy'),)
    )
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
            PersonRecord('driver-3', True, 'depot-a'),
        ),
        personnel_driving_skills=(PersonnelDrivingSkillRecord('driver-1', 'heavy'),),
    )
    instance = instance._replace(fleet_data=fleet, personnel_data=personnel)

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
    max_candidates: int = 1,
) -> CandidateContext:
    return CandidateContext(
        base=base,
        k=k,
        ensure_feasible=ensure_feasible,
        operator_state=None,
        random_source=cast(RandomSource, _ZeroRandomSource()),
        max_candidates=max_candidates,
    )


class _AlternatingRandomSource:
    def __init__(self) -> None:
        self._position = 0

    def random(self) -> float:
        value = (0.0, 0.99)[self._position % 2]
        self._position += 1
        return value


class _HighRandomSource:
    def random(self) -> float:
        return 0.99


class _ContinuityChoiceRandomSource:
    def __init__(self) -> None:
        self._draws = (0.0, 0.0, 0.25, 0.5, 0.75)
        self._position = 0

    def random(self) -> float:
        value = self._draws[self._position % len(self._draws)]
        self._position += 1
        return value


class _ContinuityTeamRandomSource:
    def __init__(self) -> None:
        self._draws = (0.0, 0.5)
        self._position = 0

    def random(self) -> float:
        value = self._draws[self._position % len(self._draws)]
        self._position += 1
        return value


class _ReverseContinuityRandomSource:
    def __init__(self) -> None:
        self._draws = (0.0, 0.25, 0.5, 0.0)
        self._position = 0

    def random(self) -> float:
        value = self._draws[self._position % len(self._draws)]
        self._position += 1
        return value


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

    assert result.candidates
    assert result.candidates[0].m == 1
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert candidate.routes[0].driver_id == 'driver-2'


def test_skill_guided_repair_changes_distinct_routes_once_per_call(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
            PersonRecord('driver-3', True, 'depot-a'),
        )
    )
    instance = instance._replace(personnel_data=personnel)
    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_feasible),
        mode='skill_guided',
        max_neighborhood_size=2,
    )

    result = operator.call(_context(base, k=2, ensure_feasible=True))

    assert result.candidates
    proposal = result.candidates[0]
    candidate = cast(VrpSolution, proposal.candidate.solution)
    changed_routes = tuple(
        index
        for index, (before, after) in enumerate(
            zip(base.routes, candidate.routes, strict=True)
        )
        if before.driver_id != after.driver_id
    )
    assert proposal.m == 2
    assert changed_routes == (0, 1)


def test_skill_guided_repair_uses_the_registered_objective_to_choose_driver(
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
            PersonRecord('driver-3', True, 'depot-a'),
        ),
        personnel_driving_skills=(
            PersonnelDrivingSkillRecord('driver-2', 'heavy'),
            PersonnelDrivingSkillRecord('driver-3', 'heavy'),
        ),
    )
    instance = instance._replace(fleet_data=fleet, personnel_data=personnel)
    objective_id = objectives.objectives[0].objective_id

    def assess(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        driver = solution.routes[0].driver_id
        value = {'driver-2': 5.0, 'driver-3': 2.0}.get(driver, 0.0)
        return ApplicationEvaluation(True, True, (value,))

    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='skill_guided',
        objective_id=objective_id,
        max_neighborhood_size=1,
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert candidate.routes[0].driver_id == 'driver-3'


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
        personnel_intervention_qualifications=(
            PersonnelInterventionQualificationRecord('person-3', 'qualified'),
        ),
        personnel_availability=(
            PersonnelAvailabilityRecord('person-2', 5.0, 20.0),
            PersonnelAvailabilityRecord('person-3', 0.0, 1440.0),
        ),
    )
    intervention = VrpInterventionData(
        (InterventionRecord('repair', 'b', 10.0, 1),),
        (),
        (),
        (),
        (InterventionQualificationRequiredRecord('repair', 'qualified'),),
    )
    fleet = instance.fleet_data._replace(
        vehicles=tuple(
            vehicle._replace(seat_capacity=2)
            if vehicle.vehicle_id == 'vehicle-1'
            else vehicle
            for vehicle in instance.fleet_data.vehicles
        )
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
        fleet_data=fleet,
    )
    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_feasible),
        mode='availability',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _HighRandomSource()),
        )
    )

    assert result.candidates[0].m == 1
    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    changed_stop = cast(ActivityStop, candidate.routes[0].stops[0])
    assert len(changed_stop.team_member_ids) >= 1
    assert 'person-2' not in changed_stop.team_member_ids
    assert 'person-3' in changed_stop.team_member_ids


def test_driver_availability_repair_preserves_the_intervention_team(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
        ),
        personnel_availability=(PersonnelAvailabilityRecord('driver-1', 5.0, 20.0),),
    )
    fleet = instance.fleet_data._replace(
        vehicles=tuple(
            vehicle._replace(seat_capacity=2)
            if vehicle.vehicle_id == 'vehicle-1'
            else vehicle
            for vehicle in instance.fleet_data.vehicles
        )
    )
    intervention = VrpInterventionData(
        (InterventionRecord('repair', 'b', 10.0, 1),), (), (), (), ()
    )
    routes = list(base.routes)
    stops = list(routes[0].stops)
    stops[0] = ActivityStop(
        ActivityRef('intervention', 'repair'),
        frozenset({'driver-1'}),
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
        fleet_data=fleet,
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

    assert result.candidates
    proposal = result.candidates[0]
    candidate = cast(VrpSolution, proposal.candidate.solution)
    candidate_stop = cast(ActivityStop, candidate.routes[0].stops[0])
    assert proposal.m == 1
    assert candidate.routes[0].driver_id == 'driver-2'
    assert candidate_stop.team_member_ids == frozenset({'driver-1'})


def test_availability_target_uses_positive_findings_from_partial_analysis(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    objective_id = str(objectives.objectives[0].objective_id)
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
        ),
        personnel_availability=(
            PersonnelAvailabilityRecord('driver-1', 0.0, 10.0),
            PersonnelAvailabilityRecord('driver-2', 0.0, 10.0),
        ),
    )
    routes = (base.routes[0], replace(base.routes[1], driver_id='driver-2'))
    base = replace(base, routes=routes)
    instance = instance._replace(personnel_data=personnel)
    finding = ResourceFinding(
        'personnel_availability',
        'driver-2',
        'scenario-000001',
        None,
        None,
        None,
        5.0,
        ((objective_id, 5.0),),
    )
    analysis = VrpOperatorAnalysis(
        False,
        (),
        (finding,),
        (),
        HardFeasibilityFinding('scenario_constraint', 'partial', 'scenario-000001'),
    )
    evaluation = ApplicationEvaluation(True, False, (5.0,), analysis)

    targets = select_targets(
        instance,
        'availability',
        base,
        evaluation,
        objectives.objectives[0].objective_id,
    )

    assert tuple(
        (target.duty.route_index, target.duty.person_id, target.duty.role)
        for target in targets
    ) == ((1, 'driver-2', 'driver'),)


def test_availability_target_does_not_fallback_after_complete_zero_finding(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel_availability=(PersonnelAvailabilityRecord('driver-1', 0.0, 10.0),)
    )
    instance = instance._replace(personnel_data=personnel)
    evaluation = ApplicationEvaluation(
        True, True, (0.0,), VrpOperatorAnalysis(True, (), (), ())
    )

    targets = select_targets(
        instance,
        'availability',
        base,
        evaluation,
        objectives.objectives[0].objective_id,
    )

    assert targets == ()


def test_personnel_availability_repair_returns_objective_ranked_batch(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    objective_id = str(objectives.objectives[0].objective_id)
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
            PersonRecord('driver-3', True, 'depot-a'),
        ),
        personnel_availability=(PersonnelAvailabilityRecord('driver-1', 0.0, 10.0),),
    )
    instance = instance._replace(personnel_data=personnel)
    analysis = VrpOperatorAnalysis(
        True,
        (),
        (
            ResourceFinding(
                'personnel_availability',
                'driver-1',
                'scenario-000001',
                None,
                None,
                None,
                5.0,
                ((objective_id, 5.0),),
            ),
        ),
        (),
    )

    def assess(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        value = {'driver-2': 5.0, 'driver-3': 2.0}.get(
            solution.routes[0].driver_id, 0.0
        )
        return ApplicationEvaluation(True, True, (value,), analysis)

    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='availability',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _AlternatingRandomSource()),
        max_candidates=2,
    )

    result = operator.call(context)

    assert len(result.candidates) == 2
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert tuple(solution.routes[0].driver_id for solution in solutions) == (
        'driver-3',
        'driver-2',
    )


def test_availability_target_uses_largest_scenario_duty_exposure(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel_availability=(PersonnelAvailabilityRecord('driver-1', 0.0, 10.0),)
    )
    instance = instance._replace(personnel_data=personnel)
    finding = ResourceFinding(
        'personnel_availability',
        'driver-1',
        'scenario-000001',
        None,
        None,
        None,
        10.0,
        ((str(objectives.objectives[0].objective_id), 10.0),),
    )
    scenario = VrpScenarioEvaluation(
        'scenario-000001',
        1.0,
        (
            VehicleDayScenarioEvaluation('vehicle-1', 1, 0.0, 10.0, (), ()),
            VehicleDayScenarioEvaluation('vehicle-2', 1, 20.0, 30.0, (), ()),
        ),
    )
    analysis = VrpOperatorAnalysis(True, (scenario,), (finding,), ())
    evaluation = ApplicationEvaluation(True, True, (10.0,), analysis)

    targets = select_targets(
        instance,
        'availability',
        base,
        evaluation,
        objectives.objectives[0].objective_id,
    )

    assert tuple(item.duty.route_index for item in targets) == (1,)


def test_personnel_availability_repair_applies_requested_distinct_duties(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
        ),
        personnel_availability=(PersonnelAvailabilityRecord('driver-1', 0.0, 10.0),),
    )
    instance = instance._replace(personnel_data=personnel)
    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_feasible),
        mode='availability',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=2,
    )

    result = operator.call(_context(base, k=2, max_candidates=2))

    assert result.candidates
    for proposal in result.candidates:
        candidate = cast(VrpSolution, proposal.candidate.solution)
        assert proposal.m == 2
        assert tuple(route.driver_id for route in candidate.routes) == (
            'driver-2',
            'driver-2',
        )


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

    assert result.candidates[0].m == 1
    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert candidate.routes[0].driver_id == 'driver-2'


def test_staff_continuity_repair_rejects_a_replacement_with_broken_neighbor_links(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            PersonRecord('driver-1', True, 'depot-a'),
            PersonRecord('driver-2', True, 'depot-a'),
            PersonRecord('driver-3', True, 'depot-a'),
            PersonRecord('driver-4', True, 'depot-b'),
            PersonRecord('driver-5', True, 'depot-a'),
        )
    )
    routes = (
        replace(
            base.routes[0],
            day_index=1,
            overnight_depot_id='depot-b',
            start_time=0.0,
            driver_id='driver-3',
        ),
        replace(
            base.routes[0],
            day_index=2,
            overnight_depot_id='depot-c',
            start_time=10.0,
            driver_id='driver-1',
        ),
        replace(
            base.routes[1],
            day_index=1,
            overnight_depot_id='depot-a',
            start_time=5.0,
            driver_id='driver-2',
        ),
        replace(
            base.routes[1],
            day_index=2,
            overnight_depot_id='depot-c',
            start_time=15.0,
            driver_id='driver-4',
        ),
        replace(
            base.routes[1],
            day_index=3,
            overnight_depot_id='depot-c',
            start_time=20.0,
            driver_id='driver-2',
        ),
    )
    base = replace(base, routes=routes)
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
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ContinuityChoiceRandomSource()),
        max_candidates=4,
    )

    result = operator.call(context)

    assert result.candidates
    candidate_drivers = tuple(
        cast(VrpSolution, item.candidate.solution).routes[1].driver_id
        for item in result.candidates
    )
    assert candidate_drivers == ('driver-3',)


def test_staff_continuity_repair_returns_objective_ranked_candidate_batch(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            PersonRecord('driver-1', True, 'depot-b'),
            PersonRecord('driver-2', True, 'depot-a'),
            PersonRecord('driver-3', True, 'depot-a'),
        )
    )
    base = replace(
        base,
        routes=(base.routes[0], replace(base.routes[1], driver_id='driver-2')),
    )
    instance = instance._replace(
        settings=instance.settings._replace(staff_depot_mode='depot_continuity'),
        personnel_data=personnel,
    )
    objective_id = objectives.objectives[0].objective_id

    def assess(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        value = {'driver-2': 5.0, 'driver-3': 2.0}.get(
            solution.routes[0].driver_id, 0.0
        )
        return ApplicationEvaluation(True, True, (value,))

    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='staff_continuity',
        objective_id=objective_id,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ContinuityChoiceRandomSource()),
        max_candidates=2,
    )

    result = operator.call(context)

    candidate_drivers = tuple(
        cast(VrpSolution, item.candidate.solution).routes[0].driver_id
        for item in result.candidates
    )
    assert candidate_drivers == ('driver-3', 'driver-2')

    unranked_operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='staff_continuity',
        max_neighborhood_size=1,
    )
    unranked_context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, _ReverseContinuityRandomSource()),
        max_candidates=2,
    )
    unranked_result = unranked_operator.call(unranked_context)
    unranked_drivers = tuple(
        cast(VrpSolution, item.candidate.solution).routes[0].driver_id
        for item in unranked_result.candidates
    )
    assert unranked_drivers == ('driver-2', 'driver-3')


def test_staff_continuity_team_repair_checks_qualifications_and_endpoints(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            PersonRecord('driver-1', True, 'depot-a'),
            PersonRecord('person-1', False, 'depot-b'),
            PersonRecord('person-2', False, 'depot-a'),
            PersonRecord('person-3', False, 'depot-b'),
        ),
        personnel_intervention_qualifications=(
            PersonnelInterventionQualificationRecord('person-2', 'qualified'),
        ),
    )
    fleet = instance.fleet_data._replace(
        vehicles=tuple(
            vehicle._replace(seat_capacity=2)
            if vehicle.vehicle_id == 'vehicle-1'
            else vehicle
            for vehicle in instance.fleet_data.vehicles
        )
    )
    intervention = VrpInterventionData(
        (InterventionRecord('repair', 'b', 10.0, 1),),
        (),
        (),
        (),
        (InterventionQualificationRequiredRecord('repair', 'qualified'),),
    )
    routes = list(base.routes)
    stops = list(routes[0].stops)
    stops[0] = ActivityStop(
        ActivityRef('intervention', 'repair'),
        frozenset({'person-1'}),
        0.0,
        0.0,
        0.0,
        False,
    )
    routes[0] = replace(routes[0], stops=tuple(stops))
    base = replace(base, routes=tuple(routes))
    instance = instance._replace(
        settings=instance.settings._replace(staff_depot_mode='depot_continuity'),
        personnel_data=personnel,
        fleet_data=fleet,
        intervention_data=intervention,
    )
    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_feasible),
        mode='staff_continuity',
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ContinuityTeamRandomSource()),
    )

    result = operator.call(context)

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    changed_stop = cast(ActivityStop, candidate.routes[0].stops[0])
    assert result.candidates[0].m == 1
    assert changed_stop.team_member_ids == frozenset({'person-2'})


def test_staff_continuity_repair_applies_multiple_assignments_sequentially(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            PersonRecord('driver-1', True, 'depot-b'),
            PersonRecord('driver-2', True, 'depot-b'),
            PersonRecord('driver-3', True, 'depot-a'),
        )
    )
    base = replace(
        base,
        routes=(
            replace(base.routes[0], driver_id='driver-1'),
            replace(base.routes[1], driver_id='driver-2'),
        ),
    )
    instance = instance._replace(
        settings=instance.settings._replace(staff_depot_mode='depot_continuity'),
        personnel_data=personnel,
    )
    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_feasible),
        mode='staff_continuity',
        max_neighborhood_size=2,
    )
    context = CandidateContext(
        base,
        2,
        True,
        None,
        cast(RandomSource, _AlternatingRandomSource()),
    )

    result = operator.call(context)

    assert result.candidates
    proposal = result.candidates[0]
    candidate = cast(VrpSolution, proposal.candidate.solution)
    assert proposal.m == 2
    assert tuple(route.driver_id for route in candidate.routes) == (
        'driver-3',
        'driver-3',
    )


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
        settings=instance.settings._replace(
            rule_treatment='compliance',
            active_simple_rules=frozenset({'daily_work'}),
        ),
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

    assert result.candidates[0].m == 2
    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert all(route.driver_id == 'driver-2' for route in candidate.routes)


def test_work_rule_penalty_target_uses_largest_positive_objective_contribution(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    objective_id = str(objectives.objectives[0].objective_id)
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
        )
    )
    base = replace(
        base,
        routes=(base.routes[0], replace(base.routes[1], driver_id='driver-2')),
    )
    instance = instance._replace(
        settings=instance.settings._replace(rule_treatment='penalty'),
        personnel_data=personnel,
    )
    findings = (
        ResourceFinding(
            'driver_work',
            'driver-1:daily_work:1',
            'scenario-1',
            'vehicle-1',
            1,
            None,
            2.0,
            ((objective_id, 1.0),),
        ),
        ResourceFinding(
            'driver_work',
            'driver-2:daily_work:1',
            'scenario-1',
            'vehicle-2',
            1,
            None,
            1.0,
            ((objective_id, 4.0),),
        ),
    )
    evaluation = ApplicationEvaluation(
        True, True, (0.0,), VrpOperatorAnalysis(True, (), findings, ())
    )

    targets = select_targets(
        instance, 'work_rule', base, evaluation, objectives.objectives[0].objective_id
    )

    assert len(targets) == 1
    assert targets[0].duty.person_id == 'driver-2'
    assert targets[0].finding == findings[1]


def test_work_rule_compliance_selects_a_key_uniformly_then_uses_its_maximum_measure(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
        )
    )
    base = replace(
        base,
        routes=(base.routes[0], replace(base.routes[1], driver_id='driver-2')),
    )
    instance = instance._replace(
        settings=instance.settings._replace(
            rule_treatment='compliance',
            active_simple_rules=frozenset({'daily_work'}),
        ),
        personnel_data=personnel,
    )
    findings = (
        ResourceFinding(
            'driver_work',
            'driver-1:daily_work:1',
            'scenario-1',
            'vehicle-1',
            1,
            None,
            2.0,
            (),
        ),
        ResourceFinding(
            'driver_work',
            'driver-1:daily_work:1',
            'scenario-2',
            'vehicle-1',
            1,
            None,
            5.0,
            (),
        ),
        ResourceFinding(
            'driver_work',
            'driver-2:daily_work:1',
            'scenario-1',
            'vehicle-2',
            1,
            None,
            3.0,
            (),
        ),
    )
    evaluation = ApplicationEvaluation(
        True,
        False,
        (float('inf'),),
        VrpOperatorAnalysis(
            False,
            (VrpScenarioEvaluation('scenario-1', 1.0, ()),),
            findings,
            (),
            HardFeasibilityFinding('compliance_rule', 'work limit', 'scenario-1'),
        ),
    )

    targets = select_targets(
        instance,
        'work_rule',
        base,
        evaluation,
        objectives.objectives[0].objective_id,
        random_source=cast(RandomSource, _HighRandomSource()),
    )

    assert len(targets) == 1
    assert targets[0].duty.person_id == 'driver-2'
    assert targets[0].finding == findings[2]

    inactive_instance = instance._replace(
        settings=instance.settings._replace(active_simple_rules=frozenset())
    )
    fallback_targets = select_targets(
        inactive_instance,
        'work_rule',
        base,
        evaluation,
        objectives.objectives[0].objective_id,
        random_source=cast(RandomSource, _ZeroRandomSource()),
    )
    assert fallback_targets
    assert all(target.finding is None for target in fallback_targets)


def test_work_rule_activity_finding_replaces_only_the_implicated_team_assignment(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    activity_one = ActivityRef('intervention', 'repair-one')
    activity_two = ActivityRef('intervention', 'repair-two')
    personnel = instance.personnel_data._replace(
        personnel=(
            PersonRecord('driver-1', True, 'depot-a'),
            PersonRecord('person-1', False, 'depot-a'),
            PersonRecord('person-2', False, 'depot-a'),
        ),
        personnel_intervention_qualifications=(
            PersonnelInterventionQualificationRecord('person-2', 'qualified'),
        ),
    )
    fleet = instance.fleet_data._replace(
        vehicles=tuple(
            vehicle._replace(seat_capacity=3)
            if vehicle.vehicle_id == 'vehicle-1'
            else vehicle
            for vehicle in instance.fleet_data.vehicles
        )
    )
    intervention = VrpInterventionData(
        (
            InterventionRecord('repair-one', 'b', 10.0, 1),
            InterventionRecord('repair-two', 'c', 10.0, 1),
        ),
        (),
        (),
        (),
        (
            InterventionQualificationRequiredRecord('repair-one', 'qualified'),
            InterventionQualificationRequiredRecord('repair-two', 'qualified'),
        ),
    )
    routes = list(base.routes)
    stops = list(routes[0].stops)
    stops[0] = ActivityStop(activity_one, frozenset({'person-1'}), 0.0, 0.0, 0.0, False)
    stops[1] = ActivityStop(activity_two, frozenset({'person-1'}), 0.0, 0.0, 0.0, False)
    routes[0] = replace(routes[0], stops=tuple(stops))
    base = replace(base, routes=tuple(routes))
    instance = instance._replace(
        settings=instance.settings._replace(
            rule_treatment='compliance',
            active_simple_rules=frozenset({'daily_work'}),
        ),
        personnel_data=personnel,
        fleet_data=fleet,
        intervention_data=intervention,
    )
    objective_id = str(objectives.objectives[0].objective_id)
    selected_finding = ResourceFinding(
        'driver_work',
        'person-1:daily_work:1',
        'scenario-1',
        'vehicle-1',
        1,
        activity_two,
        1.0,
        ((objective_id, 1.0),),
    )
    other_activity_finding = ResourceFinding(
        'driver_work',
        'person-1:daily_work:1',
        'scenario-1',
        'vehicle-1',
        1,
        activity_one,
        4.0,
        ((objective_id, 4.0),),
    )
    complete_other_activity = ApplicationEvaluation(
        True,
        True,
        (0.0,),
        VrpOperatorAnalysis(True, (), (other_activity_finding,), ()),
    )
    assert (
        repair_score(
            instance,
            'work_rule',
            base,
            complete_other_activity,
            RepairTarget(
                DutyTarget(0, 'person-1', 'team', activity_two), selected_finding
            ),
        )
        == 0.0
    )
    partial_other_activity = ApplicationEvaluation(
        True,
        False,
        (float('inf'),),
        VrpOperatorAnalysis(
            False,
            (),
            (other_activity_finding,),
            (),
            HardFeasibilityFinding('compliance_rule', 'work limit', 'scenario-1'),
        ),
    )
    assert repair_score(
        instance,
        'work_rule',
        base,
        partial_other_activity,
        RepairTarget(DutyTarget(0, 'person-1', 'team', activity_two), selected_finding),
    ) == float('inf')

    def assess(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        second_stop = cast(ActivityStop, solution.routes[0].stops[1])
        if 'person-1' not in second_stop.team_member_ids:
            return ApplicationEvaluation(True, True, (0.0,))
        finding = ResourceFinding(
            'driver_work',
            'person-1:daily_work:1',
            'scenario-1',
            'vehicle-1',
            1,
            activity_two,
            1.0,
            ((objective_id, 1.0),),
        )
        return ApplicationEvaluation(
            True,
            False,
            (float('inf'),),
            VrpOperatorAnalysis(
                False,
                (VrpScenarioEvaluation('scenario-1', 1.0, ()),),
                (finding,),
                (),
                HardFeasibilityFinding('compliance_rule', 'work limit', 'scenario-1'),
            ),
        )

    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='work_rule',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
    )
    result = operator.call(
        CandidateContext(
            base,
            1,
            True,
            None,
            cast(RandomSource, _HighRandomSource()),
        )
    )

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert candidate.routes[0].stops[0] == stops[0]
    changed_team = cast(ActivityStop, candidate.routes[0].stops[1]).team_member_ids
    assert 'person-1' not in changed_team
    assert 'person-2' in changed_team


def test_work_rule_returns_objective_ordered_feasible_candidates(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
            PersonRecord('driver-3', True, 'depot-a'),
        )
    )
    instance = instance._replace(personnel_data=personnel)
    base = replace(base, routes=(base.routes[0],))

    def assess(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        score = {'driver-1': 10.0, 'driver-2': 5.0, 'driver-3': 1.0}[
            solution.routes[0].driver_id
        ]
        return ApplicationEvaluation(True, True, (score,))

    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='work_rule',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, _AlternatingRandomSource()),
        max_candidates=2,
    )

    result = operator.call(context)

    assert len(result.candidates) == 2
    assert tuple(
        cast(VrpSolution, proposal.candidate.solution).routes[0].driver_id
        for proposal in result.candidates
    ) == ('driver-3', 'driver-2')


def test_work_rule_orders_infeasible_trials_only_by_the_selected_measure(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, 'depot-a'),
            PersonRecord('driver-3', True, 'depot-a'),
        )
    )
    instance = instance._replace(
        settings=instance.settings._replace(
            rule_treatment='compliance',
            active_simple_rules=frozenset({'daily_work'}),
        ),
        personnel_data=personnel,
    )

    def assess(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        driver = solution.routes[0].driver_id
        measure, objective = {
            'driver-1': (3.0, 0.0),
            'driver-2': (1.0, 100.0),
            'driver-3': (2.0, 0.0),
        }[driver]
        finding = ResourceFinding(
            'driver_work',
            'driver-1:daily_work:1',
            'scenario-1',
            None,
            1,
            None,
            measure,
            (),
        )
        analysis = VrpOperatorAnalysis(
            False,
            (VrpScenarioEvaluation('scenario-1', 1.0, ()),),
            (finding,),
            (),
            HardFeasibilityFinding('compliance_rule', 'work limit', 'scenario-1'),
        )
        return ApplicationEvaluation(True, False, (objective,), analysis)

    operator = PersonnelRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='work_rule',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _AlternatingRandomSource()),
        max_candidates=2,
    )

    result = operator.call(context)

    assert len(result.candidates) == 2
    assert tuple(
        cast(VrpSolution, proposal.candidate.solution).routes[0].driver_id
        for proposal in result.candidates
    ) == ('driver-2', 'driver-3')


def _always_feasible(
    _instance: object, _objectives: object, _solution: VrpSolution
) -> ApplicationEvaluation:
    return ApplicationEvaluation(True, True, (0.0,))
