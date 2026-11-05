"""Tests for vehicle-route consolidation and split Operators.

Created: 2026-11-04T17:20:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    LoadedVrpInstance,
    PackageRecord,
    PersonRecord,
    VehicleOvernightDepotRecord,
    VehicleRecord,
    VrpInterventionData,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.path_search import route_node_sequence
from multiobjective_vns.multiday_vrp.operators.vehicle_route_consolidation import (
    VehicleRouteConsolidationOperator,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_split import (
    VehicleRouteSplitOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
    TransferOperationStop,
    TransferStep,
    TransferStepRef,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 50000)

    def random(self) -> float:
        return next(self._draws)


def _service_plan(
    root: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _test_plan(root)
    interventions = tuple(
        InterventionRecord(f'job-{index}', node, 0.0, 1)
        for index, node in enumerate(('b', 'c', 'd', 'e'), start=1)
    )
    instance = instance._replace(
        intervention_data=VrpInterventionData(interventions, (), (), (), ()),
        personnel_data=instance.personnel_data._replace(
            personnel=(
                *instance.personnel_data.personnel,
                PersonRecord('driver-2', True, None),
            )
        ),
    )
    routes = tuple(
        replace(
            route,
            stops=tuple(
                ActivityStop(
                    ActivityRef('intervention', f'job-{job_index}'),
                    frozenset(),
                    0.0,
                    0.0,
                    0.0,
                    False,
                )
                for job_index in job_indices
            ),
        )
        for route, job_indices in zip(base.routes, ((1, 2), (3, 4)), strict=True)
    )
    return instance, objectives, replace(base, routes=routes)


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


def _context(
    solution: VrpSolution,
    *,
    k: int = 1,
    ensure_feasible: bool = False,
) -> CandidateContext:
    return CandidateContext(
        solution,
        k,
        ensure_feasible,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )


def _assert_route_legs_connect(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> None:
    arc_by_id = {arc.arc_id: arc for arc in instance.network_data.arcs}
    for route in solution.routes:
        nodes = route_node_sequence(instance, solution, route)
        assert len(route.legs) == len(nodes) - 1
        for leg_index, leg in enumerate(route.legs):
            current = nodes[leg_index]
            for arc_id in leg.arc_ids:
                arc = arc_by_id[arc_id]
                assert arc.tail_node_id == current
                current = arc.head_node_id
            assert current == nodes[leg_index + 1]


def test_consolidation_removes_source_route_and_preserves_all_services(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_plan(tmp_path)
    operator = VehicleRouteConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=3,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert len(candidate.routes) == 1
    assert candidate.routes[0].vehicle_id == 'vehicle-2'
    assert {
        stop.activity_ref.activity_id
        for stop in candidate.routes[0].stops
        if isinstance(stop, ActivityStop)
    } == {'job-1', 'job-2', 'job-3', 'job-4'}
    _assert_route_legs_connect(instance, candidate)


def test_targeted_consolidation_ranks_alternatives_across_route_pairs(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_plan(tmp_path)

    def prefer_vehicle_one(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        value = 0.0 if solution.routes[0].vehicle_id == 'vehicle-1' else 10.0
        return ApplicationEvaluation(
            True,
            True,
            (value,) + tuple(0.0 for _ in declaration.objectives[1:]),
        )

    operator = VehicleRouteConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, prefer_vehicle_one),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
        objective_id=objectives.objectives[0].objective_id,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert tuple(route.vehicle_id for route in candidate.routes) == ('vehicle-1',)


def test_consolidation_places_depot_pickups_before_deliveries(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_plan(tmp_path)
    packages = (
        PackageRecord('package-1', 'a', 'b', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
        PackageRecord('package-2', 'a', 'd', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
    )
    instance = instance._replace(
        package_data=instance.package_data._replace(packages=packages)
    )
    first, second = base.routes
    first = replace(
        first,
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'package-1'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'package-1'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
        legs=(RouteLeg(()), RouteLeg(('a-b',)), RouteLeg(('b-a',))),
    )
    second = replace(
        second,
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'package-2'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'package-2'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
        legs=(RouteLeg(()), RouteLeg(('a-d',)), RouteLeg(('d-a',))),
    )
    base = replace(base, routes=(first, second))
    operator = VehicleRouteConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert tuple(
        stop.activity_ref.kind
        for stop in candidate.routes[0].stops
        if isinstance(stop, ActivityStop)
    ) == ('pickup', 'pickup', 'delivery', 'delivery')


def test_split_adds_sorted_vehicle_route_and_preserves_services(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_plan(tmp_path)
    base = replace(base, routes=(base.routes[0],))

    def distinct_driver_assessor(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        feasible = len({route.driver_id for route in solution.routes}) == len(
            solution.routes
        )
        return ApplicationEvaluation(
            True,
            feasible,
            tuple(0.0 for _ in declaration.objectives),
        )

    operator = VehicleRouteSplitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, distinct_driver_assessor),
        max_neighborhood_size=3,
        max_candidate_alternatives=32,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, k=2, ensure_feasible=True))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert tuple(route.vehicle_id for route in candidate.routes) == (
        'vehicle-1',
        'vehicle-2',
    )
    assert len({route.driver_id for route in candidate.routes}) == 2
    assert {
        stop.activity_ref.activity_id
        for route in candidate.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop)
    } == {'job-1', 'job-2'}
    _assert_route_legs_connect(instance, candidate)


def test_split_keeps_complete_package_chain_together(tmp_path: Path) -> None:
    instance, objectives, base = _service_plan(tmp_path)
    package = PackageRecord('package-1', 'a', 'b', 1.0, 0.0, 0.0, 0, 0.0, 0.0)
    instance = instance._replace(
        package_data=instance.package_data._replace(packages=(package,))
    )
    source = base.routes[0]
    stops = (
        ActivityStop(
            ActivityRef('pickup', 'package-1'), frozenset(), 0.0, 0.0, 0.0, False
        ),
        ActivityStop(
            ActivityRef('delivery', 'package-1'), frozenset(), 0.0, 0.0, 0.0, False
        ),
        *source.stops,
    )
    source = replace(
        source,
        stops=stops,
        legs=(
            RouteLeg(()),
            RouteLeg(('a-b',)),
            RouteLeg(()),
            RouteLeg(('b-c',)),
            RouteLeg(('c-a',)),
        ),
    )
    base = replace(base, routes=(source,))
    operator = VehicleRouteSplitOperator(
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
    package_routes = {
        route.vehicle_id
        for route in candidate.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop)
        and stop.activity_ref.activity_id == 'package-1'
    }
    assert len(package_routes) == 1
    _assert_route_legs_connect(instance, candidate)


def test_split_returns_no_candidate_when_all_vehicles_are_active(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_plan(tmp_path)
    operator = VehicleRouteSplitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.m == 0
    assert result.candidate is None


def test_split_keeps_package_chain_spanning_routes_together(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_plan(tmp_path)
    package = PackageRecord('package-1', 'a', 'e', 1.0, 0.0, 0.0, 1, 0.0, 0.0)
    fleet = instance.fleet_data
    instance = instance._replace(
        package_data=instance.package_data._replace(packages=(package,)),
        fleet_data=fleet._replace(
            vehicles=(
                *fleet.vehicles,
                VehicleRecord('vehicle-3', 100.0, 1, 'depot-a', 1),
            ),
            vehicle_overnight_depots=(
                *fleet.vehicle_overnight_depots,
                VehicleOvernightDepotRecord('vehicle-3', 'depot-a'),
            ),
        ),
    )
    transfer_ref = TransferStepRef('package-1', 1)
    first = replace(
        base.routes[0],
        stops=(
            TransferOperationStop(transfer_ref, 'out', 0.0, 0.0, 0.0, False),
            base.routes[0].stops[0],
        ),
    )
    second = replace(
        base.routes[1],
        stops=(
            TransferOperationStop(transfer_ref, 'in', 0.0, 0.0, 0.0, False),
            DepotOnlyStop('e', 0.0, 0.0, 0.0, False),
        ),
    )
    base = replace(
        base,
        routes=(first, second),
        transfer_steps=(TransferStep('package-1', 1, 'c'),),
    )
    operator = VehicleRouteSplitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
    )

    result = operator.call(_context(base))

    assert result.m == 0
    assert result.candidate is None


def test_route_assignment_operators_enforce_registered_size_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_plan(tmp_path)
    operator = VehicleRouteConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
    )

    with pytest.raises(ValueError, match='maximum neighborhood size'):
        operator.call(_context(base, k=2))
