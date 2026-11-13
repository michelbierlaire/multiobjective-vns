"""Tests for window-compatible package-cluster reassignment.

Created: 2026-11-10T15:42:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    VrpObjectiveFunction,
)
from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    PackageRecord,
    PackageTimeWindowRecord,
    VrpFeatureProfile,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.service_window_cluster import (
    WindowCompatiblePackageClusterReassignmentOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityKind,
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
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


def _windowed_package_plan(
    root: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, base_objectives, base = _test_plan(root)
    packages = (
        PackageRecord('package-1', 'b', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
        PackageRecord('package-2', 'd', 'e', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
    )
    package_windows = tuple(
        PackageTimeWindowRecord(package_id, activity, 1, 0.0, 100.0)
        for package_id in ('package-1', 'package-2')
        for activity in ('pickup', 'delivery')
    )
    instance = instance._replace(
        package_data=instance.package_data._replace(
            packages=packages,
            package_time_windows=package_windows,
        ),
        time_window_violation_objective_id='window-penalty',
        feature_profile=VrpFeatureProfile(
            frozenset(
                {
                    *instance.feature_profile.active_features,
                    'restrictive_service_windows',
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

    def package_stop(kind: ActivityKind, package_id: str) -> ActivityStop:
        return ActivityStop(
            ActivityRef(kind, package_id),
            frozenset(),
            0.0,
            0.0,
            0.0,
            False,
        )

    first, second = base.routes
    first = replace(
        first,
        stops=(
            package_stop('pickup', 'package-1'),
            package_stop('delivery', 'package-1'),
            DepotOnlyStop('f', 0.0, 0.0, 0.0, False),
        ),
        legs=(
            RouteLeg(('a-b',)),
            RouteLeg(('b-c',)),
            RouteLeg(('c-f',)),
            RouteLeg(('f-a',)),
        ),
    )
    second = replace(
        second,
        stops=(
            package_stop('pickup', 'package-2'),
            package_stop('delivery', 'package-2'),
            DepotOnlyStop('g', 0.0, 0.0, 0.0, False),
        ),
        legs=(
            RouteLeg(('a-d',)),
            RouteLeg(('d-e',)),
            RouteLeg(('e-g',)),
            RouteLeg(('g-a',)),
        ),
    )
    return instance, objectives, replace(base, routes=(first, second))


def _assess_with_assignment_target(
    _instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    solution: VrpSolution,
) -> ApplicationEvaluation:
    package_vehicle: dict[str, str] = {}
    for route in solution.routes:
        for stop in route.stops:
            if isinstance(stop, ActivityStop) and stop.activity_ref.kind == 'pickup':
                package_vehicle[stop.activity_ref.activity_id] = route.vehicle_id
    window_penalty = float(
        not (
            package_vehicle.get('package-1') == 'vehicle-2'
            and package_vehicle.get('package-2') == 'vehicle-1'
        )
    )
    values = tuple(
        window_penalty if item.objective_id == 'window-penalty' else 0.0
        for item in objectives.objectives
    )
    return ApplicationEvaluation(True, True, values)


def _context(
    solution: VrpSolution,
    *,
    k: int = 1,
    max_candidates: int = 4,
) -> CandidateContext:
    return CandidateContext(
        base=solution,
        base_evaluation=None,
        k=k,
        ensure_feasible=True,
        operator_state=None,
        random_source=cast(RandomSource, _ZeroRandomSource()),
        max_candidates=max_candidates,
    )


def test_reassignment_returns_distinct_candidates_and_preserves_active_fleet(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_package_plan(tmp_path)
    operator = WindowCompatiblePackageClusterReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess_with_assignment_target),
        max_neighborhood_size=2,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=12,
        max_paths_per_leg=8,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, k=2, max_candidates=4))

    assert 2 <= len(result.candidates) <= 4
    plans = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(plans)) == len(plans)
    assert {proposal.m for proposal in result.candidates} == {2}
    active_vehicles = {route.vehicle_id for route in base.routes}
    for plan in plans:
        assert {route.vehicle_id for route in plan.routes} == active_vehicles
        package_vehicles: dict[str, set[str]] = {}
        for route in plan.routes:
            for stop in route.stops:
                if isinstance(stop, ActivityStop) and stop.activity_ref.kind in {
                    'pickup',
                    'delivery',
                }:
                    package_vehicles.setdefault(
                        stop.activity_ref.activity_id, set()
                    ).add(route.vehicle_id)
        assert package_vehicles == {
            'package-1': {'vehicle-2'},
            'package-2': {'vehicle-1'},
        }


def test_window_objective_filters_to_least_violating_cluster_candidates(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_package_plan(tmp_path)
    operator = WindowCompatiblePackageClusterReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess_with_assignment_target),
        max_neighborhood_size=2,
        max_candidate_alternatives=12,
        max_paths_per_leg=8,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, k=2, max_candidates=8))

    assert result.candidates
    assert {proposal.m for proposal in result.candidates} == {2}


def test_reassignment_returns_no_candidate_without_two_active_vehicles(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_package_plan(tmp_path)
    base = replace(base, routes=(base.routes[0],))
    operator = WindowCompatiblePackageClusterReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess_with_assignment_target),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.candidates == ()
