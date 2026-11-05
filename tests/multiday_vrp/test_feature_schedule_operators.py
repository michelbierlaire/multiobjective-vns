"""Tests for feature-specific schedule and depot Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import VehicleOvernightDepotRecord
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.schedule_features import (
    MultiDayDepotReassignmentOperator,
    OvernightDepotChangeOperator,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


class _QuarterRandomSource:
    def random(self) -> float:
        return 0.25


def _context(base: VrpSolution, *, k: int = 1) -> CandidateContext:
    return CandidateContext(
        base,
        k,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )


def test_overnight_depot_change_rebuilds_endpoint_path(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    records = tuple(
        VehicleOvernightDepotRecord(record.vehicle_id, depot.depot_id)
        for record in instance.fleet_data.vehicles[:1]
        for depot in instance.network_data.depots
    )
    fleet = instance.fleet_data._replace(vehicle_overnight_depots=records)
    instance = instance._replace(fleet_data=fleet)
    operator = OvernightDepotChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
        max_candidate_alternatives=8,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert changed.routes[0].overnight_depot_id != base.routes[0].overnight_depot_id


def test_multi_day_depot_reassignment_changes_a_contiguous_route_block(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    records = tuple(
        VehicleOvernightDepotRecord(vehicle.vehicle_id, depot.depot_id)
        for vehicle in instance.fleet_data.vehicles[:1]
        for depot in instance.network_data.depots
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicle_overnight_depots=records)
    )
    second_route = replace(base.routes[0], day_index=2)
    base = replace(base, routes=(base.routes[0], second_route))
    operator = MultiDayDepotReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
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
            cast(RandomSource, _QuarterRandomSource()),
        )
    )

    assert result.m >= 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert any(
        changed.routes[index].overnight_depot_id
        != base.routes[index].overnight_depot_id
        for index in range(2)
    )
