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
    InterventionRecord,
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
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


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
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )

    result = operator.call(context)

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    stop = changed.routes[0].stops[0]
    assert isinstance(stop, ActivityStop)
    assert stop.team_member_ids != frozenset({'person-2'})
