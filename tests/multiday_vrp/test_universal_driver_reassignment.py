"""Tests for generic driver reassignment.

Created: 2026-11-03T21:30:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

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

    assert result.m == 1
    assert result.candidate is not None
    selected = result.candidate.solution
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

    assert result.m == 0
    assert result.candidate is None
