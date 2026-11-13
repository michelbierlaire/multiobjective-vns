"""Tests for time-window regret-based service reinsertion.

Created: 2026-11-05T00:59:12+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from random import Random
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    VrpObjectiveFunction,
)
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionTimeWindowRecord,
    LoadedVrpInstance,
    PackageRecord,
    PackageTimeWindowRecord,
    PackageTransferFacilityRecord,
    TransferFacilityRecord,
    VehiclePackageCompatibilityRecord,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.service_window_regret import (
    TimeWindowRegretBasedReinsertionOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
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
from .test_vehicle_route_assignment import (
    _assert_route_legs_connect,
    _service_plan,
)


def _window_objective(
    instance: LoadedVrpInstance,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, str]:
    objective_id = 'window-penalty'
    instance = instance._replace(time_window_violation_objective_id=objective_id)
    objective = VrpObjectiveFunction(
        objective_id,
        'test:window-objective',
        'test:window-objective-v1',
    )
    return instance, ObjectiveDeclaration((objective,)), objective_id


def _context(
    solution: VrpSolution,
    *,
    k: int = 1,
    max_candidates: int = 1,
    ensure_feasible: bool = True,
    seed: int = 0,
) -> CandidateContext:
    return CandidateContext(
        solution,
        k,
        ensure_feasible,
        None,
        cast(RandomSource, Random(seed)),
        max_candidates=max_candidates,
    )


def test_regret_reinsertion_returns_distinct_ranked_trials(
    tmp_path: Path,
) -> None:
    instance, _objectives, base = _service_plan(tmp_path)
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            intervention_time_windows=(
                InterventionTimeWindowRecord('job-1', 1, 1, 0.0, 10.0),
                InterventionTimeWindowRecord('job-2', 1, 1, 0.0, 10.0),
            )
        )
    )
    instance, objectives, objective_id = _window_objective(instance)
    objectives = ObjectiveDeclaration(
        (
            *objectives.objectives,
            VrpObjectiveFunction(
                'secondary-a', 'test:secondary-a', 'test:secondary-a-v1'
            ),
            VrpObjectiveFunction(
                'secondary-b', 'test:secondary-b', 'test:secondary-b-v1'
            ),
        )
    )

    def assess_route_choice(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        assert solution != base
        job_one_route = next(
            route
            for route in solution.routes
            if any(
                isinstance(stop, ActivityStop)
                and stop.activity_ref.activity_id == 'job-1'
                for stop in route.stops
            )
        )
        job_two_route = next(
            route
            for route in solution.routes
            if any(
                isinstance(stop, ActivityStop)
                and stop.activity_ref.activity_id == 'job-2'
                for stop in route.stops
            )
        )
        job_two_position = next(
            index
            for index, stop in enumerate(job_two_route.stops)
            if isinstance(stop, ActivityStop)
            and stop.activity_ref.activity_id == 'job-2'
        )
        if job_two_route.vehicle_id != 'vehicle-2':
            secondary_values = (3.0, 3.0)
        elif job_two_position == 0:
            secondary_values = (0.0, 2.0)
        elif job_two_position == 1:
            secondary_values = (1.0, 1.0)
        else:
            secondary_values = (2.0, 0.0)
        value = 0.0 if job_one_route.vehicle_id == 'vehicle-1' else 10.0
        feasible = job_one_route.vehicle_id == 'vehicle-2'
        return ApplicationEvaluation(True, feasible, (value, *secondary_values))

    operator = TimeWindowRegretBasedReinsertionOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess_route_choice),
        objective_id=objective_id,
        max_neighborhood_size=2,
        max_candidate_alternatives=12,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, k=2, max_candidates=3, seed=41))

    assert len(result.candidates) == 3
    candidates = tuple(
        cast(VrpSolution, item.candidate.solution) for item in result.candidates
    )
    assert len(set(candidates)) == len(candidates)
    assert all(item.m == 2 for item in result.candidates)
    assert all(
        next(
            route
            for route in candidate.routes
            if any(
                isinstance(stop, ActivityStop)
                and stop.activity_ref.activity_id == 'job-1'
                for stop in route.stops
            )
        ).vehicle_id
        == 'vehicle-2'
        for candidate in candidates
    )
    assert all(
        next(
            route
            for route in candidate.routes
            if any(
                isinstance(stop, ActivityStop)
                and stop.activity_ref.activity_id == 'job-2'
                for stop in route.stops
            )
        ).vehicle_id
        == 'vehicle-2'
        for candidate in candidates
    )
    for candidate in candidates:
        _assert_route_legs_connect(instance, candidate)


def test_regret_reinsertion_prioritizes_a_unit_with_one_feasible_placement(
    tmp_path: Path,
) -> None:
    instance, _objectives, base = _service_plan(tmp_path)
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            intervention_time_windows=(
                InterventionTimeWindowRecord('job-1', 1, 1, 0.0, 10.0),
                InterventionTimeWindowRecord('job-2', 1, 1, 0.0, 10.0),
            )
        )
    )
    instance, objectives, objective_id = _window_objective(instance)

    def assess(
        _instance: LoadedVrpInstance,
        _declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        locations: dict[str, tuple[str, int]] = {}
        for route in solution.routes:
            for stop_index, stop in enumerate(route.stops):
                if isinstance(stop, ActivityStop):
                    locations[stop.activity_ref.activity_id] = (
                        route.vehicle_id,
                        stop_index,
                    )
        if locations['job-1'][0] == 'vehicle-2':
            feasible = locations['job-1'] == ('vehicle-2', 0)
            value = 10.0
        elif locations['job-2'][0] == 'vehicle-2':
            feasible = locations['job-2'] in {
                ('vehicle-2', 0),
                ('vehicle-2', 1),
            }
            value = 0.0 if locations['job-2'][1] == 0 else 5.0
        else:
            feasible = False
            value = 100.0
        return ApplicationEvaluation(True, feasible, (value,))

    operator = TimeWindowRegretBasedReinsertionOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        objective_id=objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=32,
        max_paths_per_leg=1,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, seed=7))

    assert len(result.candidates) == 1
    assert result.candidates[0].m == 1
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    job_one_route = next(
        route
        for route in candidate.routes
        if any(
            isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'job-1'
            for stop in route.stops
        )
    )
    job_two_route = next(
        route
        for route in candidate.routes
        if any(
            isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'job-2'
            for stop in route.stops
        )
    )
    assert job_one_route.vehicle_id == 'vehicle-2'
    assert job_two_route.vehicle_id == 'vehicle-1'


def test_regret_reinsertion_reassigns_windowed_package_transfer_chain(
    tmp_path: Path,
) -> None:
    instance, _objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 1, 0.0, 0.0)
    instance = instance._replace(
        network_data=instance.network_data._replace(
            transfer_facilities=(TransferFacilityRecord('c', 100.0),)
        ),
        fleet_data=instance.fleet_data._replace(
            vehicles=(
                *instance.fleet_data.vehicles,
                instance.fleet_data.vehicles[0]._replace(vehicle_id='vehicle-3'),
            )
        ),
        package_data=instance.package_data._replace(
            packages=(package,),
            package_time_windows=(
                PackageTimeWindowRecord('pkg', 'pickup', 1, 0.0, 10.0),
            ),
            package_transfer_facilities=(PackageTransferFacilityRecord('pkg', 'c'),),
            vehicle_package_compatibility=(
                VehiclePackageCompatibilityRecord('vehicle-1', 'pkg', False),
                VehiclePackageCompatibilityRecord('vehicle-2', 'pkg', True),
                VehiclePackageCompatibilityRecord('vehicle-3', 'pkg', True),
            ),
        ),
    )
    instance, objectives, objective_id = _window_objective(instance)
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            TransferOperationStop(
                TransferStepRef('pkg', 1), 'out', 0.0, 0.0, 0.0, False
            ),
        ),
        legs=(RouteLeg(('a-b',)), RouteLeg(('b-c',)), RouteLeg(('c-a',))),
    )
    routes.append(
        replace(
            base.routes[1],
            day_index=2,
            stops=(
                TransferOperationStop(
                    TransferStepRef('pkg', 1), 'in', 0.0, 0.0, 0.0, False
                ),
                ActivityStop(
                    ActivityRef('delivery', 'pkg'),
                    frozenset(),
                    0.0,
                    0.0,
                    0.0,
                    False,
                ),
            ),
            legs=(RouteLeg(('a-c',)), RouteLeg(()), RouteLeg(('c-a',))),
        )
    )
    routes.append(replace(base.routes[1], vehicle_id='vehicle-3'))
    base = replace(
        base,
        routes=tuple(routes),
        transfer_steps=(TransferStep('pkg', 1, 'c'),),
    )

    def assess(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(
            True, True, tuple(0.0 for _ in declaration.objectives)
        )

    operator = TimeWindowRegretBasedReinsertionOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        objective_id=objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=2,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base, seed=0))

    assert result.candidates
    assert result.candidates[0].m == 1
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.transfer_steps == base.transfer_steps
    package_positions: dict[tuple[str, int], tuple[str, int]] = {}
    for route in changed.routes:
        for stop_index, stop in enumerate(route.stops):
            if (
                isinstance(stop, ActivityStop)
                and stop.activity_ref.activity_id == 'pkg'
            ):
                package_positions[(stop.activity_ref.kind, 0)] = (
                    route.vehicle_id,
                    route.day_index,
                )
            elif (
                isinstance(stop, TransferOperationStop)
                and stop.transfer_step_ref.package_id == 'pkg'
            ):
                package_positions[(stop.role, stop.transfer_step_ref.step_number)] = (
                    route.vehicle_id,
                    route.day_index,
                )
    assert package_positions[('pickup', 0)] == package_positions[('out', 1)]
    assert package_positions[('delivery', 0)] == package_positions[('in', 1)]
    assert package_positions[('pickup', 0)][0] != package_positions[('in', 1)][0]
    _assert_route_legs_connect(instance, changed)
