"""Tests for spatial/time-window route-cluster rebuilding.

Created: 2026-11-11T09:30:18+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import MappingProxyType
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.objectives import VrpObjectiveFunction
from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    PackageRecord,
    PackageTimeWindowRecord,
    VrpFeatureProfile,
)
from multiobjective_vns.multiday_vrp.operators.catalog import (
    VrpOperatorId,
    build_default_vrp_operator_catalog,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.solution import (
    ActivityKind,
    ActivityRef,
    ActivityStop,
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 100000)

    def random(self) -> float:
        return next(self._draws)


def _spatial_package_plan(
    root: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, base_objectives, _base = _test_plan(root)
    packages = tuple(
        PackageRecord(package_id, 'a', node_id, 1.0, 0.0, 0.0, 0, 0.0, 0.0)
        for package_id, node_id in (
            ('package-1', 'b'),
            ('package-2', 'c'),
            ('package-3', 'd'),
            ('package-4', 'e'),
        )
    )
    windows = tuple(
        PackageTimeWindowRecord(package.package_id, activity, 1, 0.0, 1000.0)
        for package in packages
        for activity in ('pickup', 'delivery')
    )
    distances = {
        frozenset((left, right)): distance
        for left, right, distance in (
            ('a', 'b', 5.0),
            ('a', 'c', 5.0),
            ('a', 'd', 5.0),
            ('a', 'e', 5.0),
            ('b', 'c', 1.0),
            ('d', 'e', 1.0),
        )
    }
    travel_times: dict[tuple[str, str, int, int], float] = {}
    for key in instance.scenario_bank.travel_times:
        _scenario_id, arc_id, _day_index, _boundary_index = key
        arc = next(item for item in instance.network_data.arcs if item.arc_id == arc_id)
        pair = frozenset((arc.tail_node_id, arc.head_node_id))
        travel_times[key] = distances.get(pair, 30.0)
    scenario_bank = instance.scenario_bank._replace(
        travel_times=MappingProxyType(travel_times)
    )
    instance = instance._replace(
        scenario_bank=scenario_bank,
        package_data=instance.package_data._replace(
            packages=packages,
            package_time_windows=windows,
        ),
        time_window_violation_objective_id='window-penalty',
        feature_profile=VrpFeatureProfile(
            frozenset(
                {
                    *instance.feature_profile.active_features,
                    'restrictive_service_windows',
                    'package_pickup_delivery',
                }
            )
        ),
    )
    objectives = ObjectiveDeclaration(
        (
            *base_objectives.objectives,
            VrpObjectiveFunction(
                'window-penalty', 'test-window-objective', 'test-window-v1'
            ),
        )
    )

    def stop(kind: ActivityKind, package_id: str) -> ActivityStop:
        return ActivityStop(
            ActivityRef(kind, package_id), frozenset(), 0.0, 0.0, 0.0, False
        )

    def route(vehicle_id: str, package_ids: tuple[str, ...]) -> VehicleDayRoute:
        deliveries = tuple(
            next(
                package.delivery_node_id
                for package in packages
                if package.package_id == package_id
            )
            for package_id in package_ids
        )
        stops = tuple(stop('pickup', package_id) for package_id in package_ids) + tuple(
            stop('delivery', package_id) for package_id in package_ids
        )
        nodes = ('a', *('a' for _ in package_ids), *deliveries, 'a')
        legs = tuple(
            RouteLeg(()) if left == right else RouteLeg((f'{left}-{right}',))
            for left, right in zip(nodes[:-1], nodes[1:], strict=True)
        )
        return VehicleDayRoute(
            vehicle_id,
            1,
            'depot-a',
            0.0,
            'driver-1',
            stops,
            legs,
        )

    base = VrpSolution(
        (
            route('vehicle-1', ('package-1', 'package-3')),
            route('vehicle-2', ('package-2', 'package-4')),
        ),
        (),
    )
    return instance, objectives, base


def _assess(
    _instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    _solution: VrpSolution,
) -> ApplicationEvaluation:
    return ApplicationEvaluation(True, True, tuple(0.0 for _ in objectives.objectives))


def _context(base: VrpSolution, *, k: int) -> CandidateContext:
    return CandidateContext(
        base=base,
        base_evaluation=None,
        k=k,
        ensure_feasible=True,
        operator_state=None,
        random_source=cast(RandomSource, _ZeroRandomSource()),
        max_candidates=32,
    )


def _package_vehicle_map(solution: VrpSolution) -> dict[str, str]:
    return {
        stop.activity_ref.activity_id: route.vehicle_id
        for route in solution.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop) and stop.activity_ref.kind == 'delivery'
    }


def test_route_cluster_rebuild_consolidates_spatially_close_packages(
    tmp_path: Path,
) -> None:
    from multiobjective_vns.multiday_vrp.operators.service_window_route_cluster import (
        SpatialTimeWindowRouteClusterRebuildOperator,
    )

    instance, objectives, base = _spatial_package_plan(tmp_path)
    operator = SpatialTimeWindowRouteClusterRebuildOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        max_neighborhood_size=3,
        max_candidate_alternatives=32,
    )

    result = operator.call(_context(base, k=2))

    assert result.candidates
    assert all(1 <= proposal.m <= 2 for proposal in result.candidates)
    assert any(
        _package_vehicle_map(cast(VrpSolution, proposal.candidate.solution))[
            'package-1'
        ]
        == _package_vehicle_map(cast(VrpSolution, proposal.candidate.solution))[
            'package-2'
        ]
        and _package_vehicle_map(cast(VrpSolution, proposal.candidate.solution))[
            'package-3'
        ]
        == _package_vehicle_map(cast(VrpSolution, proposal.candidate.solution))[
            'package-4'
        ]
        and _package_vehicle_map(cast(VrpSolution, proposal.candidate.solution))[
            'package-1'
        ]
        != _package_vehicle_map(cast(VrpSolution, proposal.candidate.solution))[
            'package-3'
        ]
        for proposal in result.candidates
    )
    for proposal in result.candidates:
        candidate = cast(VrpSolution, proposal.candidate.solution)
        assert {route.vehicle_id for route in candidate.routes} == {
            route.vehicle_id for route in base.routes
        }
        assert set(_package_vehicle_map(candidate)) == {
            'package-1',
            'package-2',
            'package-3',
            'package-4',
        }


def test_catalog_registers_route_cluster_and_two_opt_composition(
    tmp_path: Path,
) -> None:
    instance, objectives, _base = _spatial_package_plan(tmp_path)
    catalog = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='test-instance',
        objective_declaration=objectives,
        assess_plan=cast(VrpPlanAssessor, _assess),
    )

    cluster_id = VrpOperatorId(
        'vrp.service_windows.spatial_time_window_route_cluster_rebuild', None
    )
    cluster_registration = next(
        item for item in catalog.operators if item.operator_id == cluster_id
    )
    assert cluster_registration.max_neighborhood_size >= 2
    unranked_composition = next(
        composition
        for composition in catalog.pre_registered_compositions
        if composition.first_operator_id == cluster_id
    )
    assert unranked_composition.second_operator_id == VrpOperatorId(
        'vrp.generic.two_opt', None
    )
    for objective in objectives.objectives:
        targeted_id = VrpOperatorId(
            'vrp.service_windows.spatial_time_window_route_cluster_rebuild',
            objective.objective_id,
        )
        assert any(
            composition.first_operator_id == targeted_id
            and composition.second_operator_id
            == VrpOperatorId('vrp.generic.two_opt', None)
            for composition in catalog.pre_registered_compositions
        )
    assert unranked_composition.operator_id == VrpOperatorId(
        'vrp.composite.spatial_time_window_route_cluster_rebuild_then_two_opt',
        None,
    )
