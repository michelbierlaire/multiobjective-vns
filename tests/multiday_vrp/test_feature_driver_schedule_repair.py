"""Tests for multi-route driver schedule destroy-and-repair.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import PersonRecord
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.driver_schedule_repair import (
    DriverScheduleDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext

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

    assert result.m == 2
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert tuple(route.driver_id for route in candidate.routes) == (
        'driver-2',
        'driver-2',
    )
