"""Tests for Daganzo-inspired planar strip-sweep route rebuilding.

Created: 2026-11-12T16:00:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    build_vrp_objective_declaration,
)
from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    NodeCoordinateRecord,
    PackageRecord,
    VrpFeatureProfile,
    VrpSpatialData,
)
from multiobjective_vns.multiday_vrp.operators import path_search, spatial_strip_sweep
from multiobjective_vns.multiday_vrp.operators.catalog import (
    VrpOperatorCatalogSettings,
    VrpOperatorId,
    build_default_vrp_operator_catalog,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.spatial_strip_sweep import (
    DaganzoStripSweepRouteRebuildOperator,
)
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


class _CyclingRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0, 0.17, 0.39, 0.61, 0.83) * 10000)

    def random(self) -> float:
        return next(self._draws)


def _spatial_plan(
    root: Path,
    *,
    coordinates: tuple[NodeCoordinateRecord, ...] | None = None,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    base_instance, _objectives, _base = _test_plan(root)
    packages = (
        PackageRecord('package-a', 'a', 'b', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
        PackageRecord('package-b', 'a', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
        PackageRecord('package-c', 'a', 'd', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
        PackageRecord('package-d', 'a', 'e', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
    )
    if coordinates is None:
        coordinates = tuple(
            NodeCoordinateRecord(node_id, x, y)
            for node_id, x, y in (
                ('a', 0.0, 0.0),
                ('b', 1.0, 1.0),
                ('c', 2.0, 1.0),
                ('d', 10.0, 10.0),
                ('e', 11.0, 10.0),
                ('f', 2.0, 7.0),
                ('g', 9.0, 1.0),
            )
        )
    features = frozenset(
        {
            *base_instance.feature_profile.active_features,
            'planar_node_coordinates',
            'package_pickup_delivery',
        }
    )
    instance = base_instance._replace(
        package_data=base_instance.package_data._replace(packages=packages),
        spatial_data=VrpSpatialData('test-grid', 'km', coordinates),
        feature_profile=VrpFeatureProfile(features),
    )

    def package_stop(kind: str, package_id: str) -> ActivityStop:
        wait = 0.1 if kind == 'pickup' else 0.2
        return ActivityStop(
            ActivityRef(cast(ActivityKind, kind), package_id),
            frozenset(),
            wait,
            wait,
            0.0,
            False,
        )

    def make_route(vehicle_id: str, package_ids: tuple[str, ...]) -> VehicleDayRoute:
        pickup_stops = tuple(package_stop('pickup', item) for item in package_ids)
        delivery_stops = tuple(package_stop('delivery', item) for item in package_ids)
        stops = (*pickup_stops, *delivery_stops)
        delivery_nodes = tuple(
            next(
                item.delivery_node_id
                for item in packages
                if item.package_id == package_id
            )
            for package_id in package_ids
        )
        route_nodes = ('a', *('a' for _ in package_ids), *delivery_nodes, 'a')
        legs = tuple(
            RouteLeg(()) if left == right else RouteLeg((f'{left}-{right}',))
            for left, right in zip(route_nodes[:-1], route_nodes[1:], strict=True)
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

    solution = VrpSolution(
        (
            make_route('vehicle-1', ('package-a', 'package-c')),
            make_route('vehicle-2', ('package-b', 'package-d')),
        ),
        (),
    )
    return instance, build_vrp_objective_declaration(instance), solution


def _assess(
    _instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    _solution: VrpSolution,
) -> ApplicationEvaluation:
    return ApplicationEvaluation(
        representation_valid=True,
        feasible=True,
        objective_values=tuple(0.0 for _ in objectives.objectives),
    )


def _context(base: VrpSolution, *, k: int, max_candidates: int = 5) -> CandidateContext:
    return CandidateContext(
        base=base,
        k=k,
        ensure_feasible=True,
        operator_state=None,
        random_source=cast(RandomSource, _CyclingRandomSource()),
        max_candidates=max_candidates,
    )


def _delivery_ids(route: VehicleDayRoute) -> tuple[str, ...]:
    return tuple(
        stop.activity_ref.activity_id
        for stop in route.stops
        if type(stop) is ActivityStop and stop.activity_ref.kind == 'delivery'
    )


def _activity_metadata(solution: VrpSolution) -> tuple[tuple[object, ...], ...]:
    return tuple(
        sorted(
            (
                stop.activity_ref.kind,
                stop.activity_ref.activity_id,
                stop.team_member_ids,
                stop.pre_service_wait,
                stop.wait,
                stop.break_duration,
                stop.tour_end,
            )
            for route in solution.routes
            for stop in route.stops
            if type(stop) is ActivityStop
        )
    )


def _operator(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
) -> DaganzoStripSweepRouteRebuildOperator:
    return DaganzoStripSweepRouteRebuildOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        max_neighborhood_size=4,
        max_candidate_alternatives=12,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )


def test_strip_sweep_returns_bounded_distinct_complete_route_candidates(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _spatial_plan(tmp_path)
    operator = _operator(instance, objectives)

    result = operator.call(_context(base, k=2, max_candidates=4))

    candidates = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert 1 <= len(candidates) <= 4
    assert all(1 <= proposal.m <= 2 for proposal in result.candidates)
    assert len(set(candidates)) == len(candidates)
    assert all(
        tuple(
            (
                route.vehicle_id,
                route.day_index,
                route.driver_id,
                route.start_time,
                route.overnight_depot_id,
            )
            for route in candidate.routes
        )
        == tuple(
            (
                route.vehicle_id,
                route.day_index,
                route.driver_id,
                route.start_time,
                route.overnight_depot_id,
            )
            for route in base.routes
        )
        for candidate in candidates
    )
    for proposal, candidate in zip(result.candidates, candidates, strict=True):
        expected_m = sum(
            set(_delivery_ids(base.routes[index]))
            != set(_delivery_ids(candidate.routes[index]))
            or _delivery_ids(base.routes[index])
            != _delivery_ids(candidate.routes[index])
            or tuple(leg.arc_ids for leg in base.routes[index].legs)
            != tuple(leg.arc_ids for leg in candidate.routes[index].legs)
            for index in range(len(base.routes))
        )
        assert proposal.m == expected_m
    assert all(
        _activity_metadata(candidate) == _activity_metadata(base)
        for candidate in candidates
    )


def test_strip_sweep_supports_collinear_delivery_points(tmp_path: Path) -> None:
    coordinates = tuple(
        NodeCoordinateRecord(node_id, float(index), 0.0)
        for index, node_id in enumerate(('a', 'b', 'c', 'd', 'e', 'f', 'g'))
    )
    instance, objectives, base = _spatial_plan(tmp_path, coordinates=coordinates)

    result = _operator(instance, objectives).call(_context(base, k=1))

    assert result.candidates
    assert all(
        proposal.m
        == sum(
            original != updated
            for original, updated in zip(
                base.routes,
                cast(VrpSolution, proposal.candidate.solution).routes,
                strict=True,
            )
        )
        == 1
        for proposal in result.candidates
    )


def test_strip_sweep_samples_one_complete_path_assignment_per_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _spatial_plan(tmp_path)
    operator = _operator(instance, objectives)
    original_rebuild = spatial_strip_sweep.rebuild_changed_routes
    original_sample = path_search.sample_loopless_paths
    partition_signatures: list[tuple[tuple[str, ...], ...]] = []
    path_assignment_limits: list[int] = []
    sampled_pairs: list[tuple[str, str]] = []

    def record_rebuild(*args: object, **kwargs: object) -> tuple[VrpSolution, ...]:
        stops_by_route = args[2]
        assert isinstance(stops_by_route, dict)
        partition_signatures.append(
            tuple(
                tuple(
                    stop.activity_ref.activity_id
                    for stop in stops
                    if type(stop) is ActivityStop
                )
                for _route_index, stops in sorted(stops_by_route.items())
            )
        )
        candidate_limit = kwargs.get('max_candidate_alternatives')
        assert type(candidate_limit) is int
        path_assignment_limits.append(candidate_limit)
        return original_rebuild(*args, **kwargs)  # type: ignore[arg-type]

    def record_sample(
        instance: LoadedVrpInstance,
        origin_node_id: str,
        destination_node_id: str,
        **kwargs: object,
    ) -> tuple[tuple[str, ...], ...]:
        sampled_pairs.append((origin_node_id, destination_node_id))
        return original_sample(
            instance,
            origin_node_id,
            destination_node_id,
            **kwargs,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(spatial_strip_sweep, 'rebuild_changed_routes', record_rebuild)
    monkeypatch.setattr(path_search, 'sample_loopless_paths', record_sample)
    result = operator.call(_context(base, k=2, max_candidates=8))

    assert result.candidates
    assert len(partition_signatures) > 1
    assert len(set(partition_signatures)) > 1
    assert path_assignment_limits == [1] * len(path_assignment_limits)
    assert sampled_pairs
    assert len(sampled_pairs) == len(set(sampled_pairs))


def test_catalog_gates_strip_sweep_on_planar_coordinates(tmp_path: Path) -> None:
    instance, objectives, _base = _spatial_plan(tmp_path)

    catalog = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='test-instance',
        objective_declaration=objectives,
        assess_plan=cast(VrpPlanAssessor, _assess),
        settings=VrpOperatorCatalogSettings(max_candidate_alternatives=12),
    )

    spec_id = 'vrp.spatial.daganzo_strip_sweep_route_rebuild'
    assert (
        sum(
            isinstance(item.operator_id, VrpOperatorId)
            and item.operator_id.operator_spec_id == spec_id
            for item in catalog.operators
        )
        == 2
    )
    assert all(
        item.max_neighborhood_size == 5
        for item in catalog.operators
        if isinstance(item.operator_id, VrpOperatorId)
        and item.operator_id.operator_spec_id == spec_id
    )
    assert all(
        isinstance(item.operator, DaganzoStripSweepRouteRebuildOperator)
        for item in catalog.operators
        if isinstance(item.operator_id, VrpOperatorId)
        and item.operator_id.operator_spec_id == spec_id
    )


def test_catalog_omits_strip_sweep_without_planar_feature(tmp_path: Path) -> None:
    instance, objectives, _base = _spatial_plan(tmp_path)
    instance = instance._replace(
        feature_profile=VrpFeatureProfile(
            frozenset(
                feature
                for feature in instance.feature_profile.active_features
                if feature != 'planar_node_coordinates'
            )
        )
    )

    catalog = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='test-instance',
        objective_declaration=objectives,
        assess_plan=cast(VrpPlanAssessor, _assess),
    )

    assert all(
        not (
            isinstance(item.operator_id, VrpOperatorId)
            and item.operator_id.operator_spec_id
            == 'vrp.spatial.daganzo_strip_sweep_route_rebuild'
        )
        for item in catalog.operators
    )
