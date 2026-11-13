"""Tests for feature-specific intervention team Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    InterventionQualificationRequiredRecord,
    InterventionRecord,
    LoadedVrpInstance,
    PersonnelInterventionQualificationRecord,
    PersonRecord,
    VrpInterventionData,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.personnel_features import (
    InterventionTeamReassignmentOperator,
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

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


class _PrefixThenZeroRandomSource:
    def __init__(self, first_draw: float) -> None:
        self._first_draw = first_draw

    def random(self) -> float:
        first_draw = self._first_draw
        self._first_draw = 0.0
        return first_draw


class _PatternRandomSource:
    def __init__(self, pattern: tuple[float, ...]) -> None:
        self._pattern = pattern
        self._position = 0

    def random(self) -> float:
        value = self._pattern[self._position % len(self._pattern)]
        self._position += 1
        return value


def _context(
    base: VrpSolution,
    *,
    random_source: RandomSource | None = None,
    max_candidates: int = 1,
) -> CandidateContext:
    return CandidateContext(
        base=base,
        base_evaluation=None,
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, random_source or _ZeroRandomSource()),
        max_candidates=max_candidates,
    )


def test_general_team_reassignment_enumerates_all_small_eligible_team_sets(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=tuple(
                vehicle._replace(seat_capacity=3)
                for vehicle in instance.fleet_data.vehicles
            )
        ),
        personnel_data=instance.personnel_data._replace(
            personnel=(
                PersonRecord('driver-1', True, None),
                PersonRecord('person-2', False, None),
                PersonRecord('person-3', False, None),
            )
        ),
        intervention_data=VrpInterventionData(
            (InterventionRecord('repair', 'b', 10.0, 1),), (), (), (), ()
        ),
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'repair'),
                frozenset({'person-2'}),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
    )
    base = replace(base, routes=tuple(routes))
    assessed_teams: list[frozenset[str]] = []

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        stop = next(
            stop
            for route in solution.routes
            for stop in route.stops
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('intervention', 'repair')
        )
        assessed_teams.append(stop.team_member_ids)
        # Treat every proposed team as unavailable in this fixture. The
        # operator must still assess and return them when feasibility is not
        # requested, leaving the availability penalty to the evaluator.
        return ApplicationEvaluation(True, False, (0.0,))

    operator = InterventionTeamReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='general',
        max_neighborhood_size=1,
    )

    result = operator.call(
        _context(
            base,
            random_source=cast(RandomSource, _ZeroRandomSource()),
            max_candidates=10,
        )
    )

    teams = {
        next(
            stop.team_member_ids
            for route in cast(VrpSolution, proposal.candidate.solution).routes
            for stop in route.stops
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('intervention', 'repair')
        )
        for proposal in result.candidates
    }
    expected = {
        frozenset(team)
        for team in (
            ('driver-1',),
            ('person-3',),
            ('driver-1', 'person-2'),
            ('driver-1', 'person-3'),
            ('person-2', 'person-3'),
            ('driver-1', 'person-2', 'person-3'),
        )
        if frozenset(team) != frozenset({'person-2'})
    }
    assert len(teams) == 6
    assert teams == expected
    assert set(assessed_teams) == expected


def test_seat_aware_team_reassignment_changes_full_capacity_crew(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    vehicles = tuple(
        vehicle._replace(seat_capacity=2)
        if vehicle.vehicle_id == 'vehicle-1'
        else vehicle
        for vehicle in instance.fleet_data.vehicles
    )
    personnel = instance.personnel_data
    persons = (
        PersonRecord('driver-1', True, None),
        PersonRecord('person-2', False, None),
        PersonRecord('person-3', False, None),
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicles=vehicles),
        personnel_data=personnel._replace(personnel=persons),
        intervention_data=VrpInterventionData(
            (InterventionRecord('repair', 'b', 10.0, 1),),
            (),
            (),
            (),
            (),
        ),
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
    operator = InterventionTeamReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='seat_aware',
        max_neighborhood_size=1,
    )
    context = _context(base)

    result = operator.call(context)

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    stop = changed.routes[0].stops[0]
    assert isinstance(stop, ActivityStop)
    assert stop.team_member_ids != frozenset({'person-2'})


def test_seat_aware_target_sampling_selects_route_before_intervention(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    vehicles = tuple(
        vehicle._replace(seat_capacity=2) for vehicle in instance.fleet_data.vehicles
    )
    personnel = instance.personnel_data._replace(
        personnel=(
            PersonRecord('driver-1', True, None),
            PersonRecord('driver-2', True, None),
            PersonRecord('person-2', False, None),
            PersonRecord('person-3', False, None),
        )
    )
    interventions = (
        InterventionRecord('repair-a', 'b', 10.0, 1),
        InterventionRecord('repair-b', 'c', 10.0, 1),
        InterventionRecord('repair-c', 'd', 10.0, 1),
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicles=vehicles),
        personnel_data=personnel,
        intervention_data=VrpInterventionData(interventions, (), (), (), ()),
    )
    first_route, second_route = base.routes
    first_route = replace(
        first_route,
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'repair-a'),
                frozenset({'person-2'}),
                0.0,
                0.0,
                0.0,
                False,
            ),
            ActivityStop(
                ActivityRef('intervention', 'repair-b'),
                frozenset({'person-2'}),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
    )
    second_route = replace(
        second_route,
        driver_id='driver-2',
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'repair-c'),
                frozenset({'person-3'}),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
    )
    base = replace(base, routes=(first_route, second_route))
    operator = InterventionTeamReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='seat_aware',
        max_neighborhood_size=1,
    )

    result = operator.call(
        _context(
            base, random_source=cast(RandomSource, _PrefixThenZeroRandomSource(0.6))
        )
    )

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    changed_interventions = {
        stop.activity_ref.activity_id
        for route in changed.routes
        for stop in route.stops
        if type(stop) is ActivityStop
        and stop.activity_ref.kind == 'intervention'
        and stop.team_member_ids
        != next(
            original_stop.team_member_ids
            for original_route in base.routes
            for original_stop in original_route.stops
            if type(original_stop) is ActivityStop
            and original_stop.activity_ref == stop.activity_ref
        )
    }
    assert changed_interventions == {'repair-c'}


def test_seat_aware_targets_fullest_crew_when_no_route_is_saturated(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    vehicles = tuple(
        vehicle._replace(seat_capacity=4) for vehicle in instance.fleet_data.vehicles
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicles=vehicles),
        personnel_data=instance.personnel_data._replace(
            personnel=(
                PersonRecord('driver-1', True, None),
                PersonRecord('driver-2', True, None),
                PersonRecord('person-2', False, None),
                PersonRecord('person-3', False, None),
            )
        ),
        intervention_data=VrpInterventionData(
            (
                InterventionRecord('repair-a', 'b', 10.0, 1),
                InterventionRecord('repair-b', 'c', 10.0, 1),
                InterventionRecord('repair-c', 'd', 10.0, 1),
            ),
            (),
            (),
            (),
            (),
        ),
    )
    first_route, second_route = base.routes
    first_route = replace(
        first_route,
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'repair-a'),
                frozenset({'person-2'}),
                0.0,
                0.0,
                0.0,
                False,
            ),
            ActivityStop(
                ActivityRef('intervention', 'repair-b'),
                frozenset({'person-3'}),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
    )
    second_route = replace(
        second_route,
        driver_id='driver-2',
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'repair-c'),
                frozenset({'person-3'}),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
    )
    base = replace(base, routes=(first_route, second_route))
    operator = InterventionTeamReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='seat_aware',
        max_neighborhood_size=1,
    )

    result = operator.call(
        _context(
            base, random_source=cast(RandomSource, _PrefixThenZeroRandomSource(0.9))
        )
    )

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    changed_interventions = {
        stop.activity_ref.activity_id
        for route in changed.routes
        for stop in route.stops
        if type(stop) is ActivityStop
        and stop.activity_ref.kind == 'intervention'
        and stop.team_member_ids
        != next(
            original_stop.team_member_ids
            for original_route in base.routes
            for original_stop in original_route.stops
            if type(original_stop) is ActivityStop
            and original_stop.activity_ref == stop.activity_ref
        )
    }
    assert changed_interventions == {'repair-b'}


def test_seat_aware_reassignment_returns_objective_ranked_candidate_batch(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    vehicles = tuple(
        vehicle._replace(seat_capacity=2) for vehicle in instance.fleet_data.vehicles
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicles=vehicles),
        personnel_data=instance.personnel_data._replace(
            personnel=(
                PersonRecord('driver-1', True, None),
                PersonRecord('driver-2', True, None),
                PersonRecord('person-2', False, None),
                PersonRecord('person-3', False, None),
            )
        ),
        intervention_data=VrpInterventionData(
            (InterventionRecord('repair', 'b', 10.0, 1),), (), (), (), ()
        ),
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'repair'),
                frozenset({'person-2'}),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
    )
    base = replace(base, routes=tuple(routes))

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        stop = next(
            stop
            for route in solution.routes
            for stop in route.stops
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('intervention', 'repair')
        )
        member_scores = {
            'driver-1': 5.0,
            'driver-2': 4.0,
            'person-2': 3.0,
            'person-3': 1.0,
        }
        score = sum(member_scores[person_id] for person_id in stop.team_member_ids)
        return ApplicationEvaluation(True, True, (score,))

    operator = InterventionTeamReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='seat_aware',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
    )
    context = _context(
        base,
        random_source=cast(
            RandomSource,
            _PatternRandomSource((0.0, 0.0, 0.0, 0.3, 0.0, 0.6, 0.0, 0.9)),
        ),
        max_candidates=2,
    )

    result = operator.call(context)

    assert len(result.candidates) == 2
    assert all(proposal.m == 1 for proposal in result.candidates)
    teams = tuple(
        next(
            stop.team_member_ids
            for route in cast(VrpSolution, proposal.candidate.solution).routes
            for stop in route.stops
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('intervention', 'repair')
        )
        for proposal in result.candidates
    )
    scores = tuple(
        sum(
            {
                'driver-1': 5.0,
                'driver-2': 4.0,
                'person-2': 3.0,
                'person-3': 1.0,
            }[person_id]
            for person_id in team
        )
        for team in teams
    )
    assert scores == (1.0, 4.0)

    unranked = InterventionTeamReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='seat_aware',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
    ).call(
        _context(
            base,
            random_source=cast(
                RandomSource,
                _PatternRandomSource((0.0, 0.0, 0.0, 0.3, 0.0, 0.6, 0.0, 0.9)),
            ),
            max_candidates=2,
        )
    )
    unranked_teams = tuple(
        next(
            stop.team_member_ids
            for route in cast(VrpSolution, proposal.candidate.solution).routes
            for stop in route.stops
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('intervention', 'repair')
        )
        for proposal in unranked.candidates
    )
    assert len(unranked.candidates) == 2
    assert len(set(unranked_teams)) == 2


def test_seat_aware_reassignment_preserves_team_size_and_qualification(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    vehicles = tuple(
        vehicle._replace(seat_capacity=2) for vehicle in instance.fleet_data.vehicles
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicles=vehicles),
        personnel_data=instance.personnel_data._replace(
            personnel=(
                PersonRecord('driver-1', True, None),
                PersonRecord('driver-2', True, None),
                PersonRecord('person-2', False, None),
                PersonRecord('person-3', False, None),
            ),
            personnel_intervention_qualifications=(
                PersonnelInterventionQualificationRecord('driver-1', 'qualified'),
            ),
        ),
        intervention_data=VrpInterventionData(
            (InterventionRecord('repair', 'b', 10.0, 2),),
            (),
            (),
            (),
            (InterventionQualificationRequiredRecord('repair', 'qualified'),),
        ),
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'repair'),
                frozenset({'person-2'}),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
    )
    base = replace(base, routes=tuple(routes))
    operator = InterventionTeamReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='seat_aware',
        max_neighborhood_size=1,
    )

    result = operator.call(_context(base))

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    stop = next(
        stop
        for route in changed.routes
        for stop in route.stops
        if type(stop) is ActivityStop
        and stop.activity_ref == ActivityRef('intervention', 'repair')
    )
    assert len(stop.team_member_ids) >= 2
    assert 'driver-1' in stop.team_member_ids
