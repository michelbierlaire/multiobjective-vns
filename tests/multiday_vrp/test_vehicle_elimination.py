"""Tests for the vehicle-elimination destroy-and-repair Operator.

Created: 2026-11-05T00:59:12+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    PackageRecord,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.vehicle_elimination import (
    VehicleEliminationDestroyRepairOperator,
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

from .test_vehicle_route_assignment import (
    _assert_route_legs_connect,
    _service_plan,
    _ZeroRandomSource,
)


def _context(solution: VrpSolution, *, k: int = 1) -> CandidateContext:
    return CandidateContext(
        solution,
        k,
        True,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )


def _always_assess(
    _instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    _solution: VrpSolution,
) -> ApplicationEvaluation:
    return ApplicationEvaluation(
        True,
        True,
        tuple(0.0 for _ in objectives.objectives),
    )


def test_vehicle_elimination_reassigns_all_services_and_removes_a_vehicle(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_plan(tmp_path)
    operator = VehicleEliminationDestroyRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=12,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert len(candidate.routes) == 1
    assert candidate.routes[0].vehicle_id == 'vehicle-2'
    assert {
        stop.activity_ref.activity_id
        for route in candidate.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop)
    } == {'job-1', 'job-2', 'job-3', 'job-4'}
    _assert_route_legs_connect(instance, candidate)


def test_vehicle_elimination_returns_zero_when_no_receiver_exists(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_plan(tmp_path)
    base = replace(base, routes=base.routes[:1])
    operator = VehicleEliminationDestroyRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=4,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.m == 0
    assert result.candidate is None


def test_vehicle_elimination_preserves_package_chain_order(tmp_path: Path) -> None:
    instance, objectives, base = _service_plan(tmp_path)
    package = PackageRecord('parcel', 'b', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0)
    instance = instance._replace(
        package_data=instance.package_data._replace(packages=(package,))
    )
    source = base.routes[0]
    source = replace(
        source,
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'parcel'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'parcel'), frozenset(), 0.0, 0.0, 0.0, False
            ),
        ),
    )
    base = replace(base, routes=(source, base.routes[1]))
    operator = VehicleEliminationDestroyRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert len(candidate.routes) == 1
    package_stops = tuple(
        stop
        for stop in candidate.routes[0].stops
        if isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'parcel'
    )
    assert tuple(stop.activity_ref.kind for stop in package_stops) == (
        'pickup',
        'delivery',
    )
