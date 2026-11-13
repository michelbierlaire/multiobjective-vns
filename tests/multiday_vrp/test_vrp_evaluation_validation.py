"""Tests for instance-aware VRP representation validation.

Created: 2026-11-03T18:15:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import nan
from pathlib import Path

from multiobjective_vns.multiday_vrp import load_vrp_instance
from multiobjective_vns.multiday_vrp.evaluation.evaluator import (
    VrpApplicationEvaluator,
)
from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    build_vrp_objective_declaration,
)
from multiobjective_vns.multiday_vrp.evaluation.validation import (
    representation_error,
)
from multiobjective_vns.multiday_vrp.solution import (
    DepotOnlyStop,
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)

from .input_fixtures import write_minimal_vrp_bundle


def test_empty_plan_is_representation_valid_when_instance_has_no_required_tasks(
    tmp_path: Path,
) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))

    assert representation_error(instance, VrpSolution((), ())) is None


def test_exact_active_vehicle_count_is_a_hard_plan_constraint(
    tmp_path: Path,
) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))
    solution = VrpSolution((), ())
    objectives = build_vrp_objective_declaration(instance)

    zero_vehicle_instance = instance._replace(
        settings=instance.settings._replace(exact_active_vehicle_count=0)
    )
    one_vehicle_instance = instance._replace(
        settings=instance.settings._replace(exact_active_vehicle_count=1)
    )
    zero_vehicle_result = VrpApplicationEvaluator(zero_vehicle_instance).evaluate(
        solution, build_vrp_objective_declaration(zero_vehicle_instance)
    )
    one_vehicle_result = VrpApplicationEvaluator(one_vehicle_instance).evaluate(
        solution, objectives
    )

    assert zero_vehicle_result.feasible is True
    assert one_vehicle_result.feasible is False


def test_unknown_vehicle_reference_is_representation_invalid(tmp_path: Path) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))
    route = VehicleDayRoute(
        vehicle_id='missing-vehicle',
        day_index=1,
        overnight_depot_id='depot-1',
        start_time=0.0,
        driver_id='driver-1',
        stops=(),
        legs=(RouteLeg(()),),
    )

    error = representation_error(instance, VrpSolution((route,), ()))

    assert error is not None
    assert 'vehicle' in error


def test_route_path_must_connect_start_and_stop_endpoints(tmp_path: Path) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))
    route = VehicleDayRoute(
        vehicle_id='vehicle-1',
        day_index=1,
        overnight_depot_id='depot-1',
        start_time=0.0,
        driver_id='driver-1',
        stops=(),
        legs=(RouteLeg(('arc-1',)),),
    )

    error = representation_error(instance, VrpSolution((route,), ()))

    assert error is not None
    assert 'endpoint' in error or 'destination' in error


def test_type_correct_nonfinite_stop_values_are_representation_invalid(
    tmp_path: Path,
) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))
    route = VehicleDayRoute(
        vehicle_id='vehicle-1',
        day_index=1,
        overnight_depot_id='depot-1',
        start_time=0.0,
        driver_id='driver-1',
        stops=(DepotOnlyStop('node-a', 0.0, nan, 0.0, False),),
        legs=(RouteLeg(()), RouteLeg(())),
    )

    error = representation_error(instance, VrpSolution((route,), ()))

    assert error is not None
    assert 'finite' in error or 'nonnegative' in error


def test_out_of_order_routes_are_rejected_before_scenario_simulation(
    tmp_path: Path,
) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    scenario_bank = tmp_path / 'scenario-bank'
    (scenario_bank / 'target-grid.csv').write_text(
        'day_index,planning_date,boundary_index,boundary_time\n'
        '1,2025-01-02,0,2025-01-02T00:00:00+01:00\n'
        '1,2025-01-02,1,2025-01-02T00:05:00+01:00\n'
        '1,2025-01-02,2,2025-01-03T00:00:00+01:00\n'
        '2,2025-01-03,0,2025-01-03T00:00:00+01:00\n'
        '2,2025-01-03,1,2025-01-03T00:05:00+01:00\n'
        '2,2025-01-03,2,2025-01-04T00:00:00+01:00\n',
        encoding='utf-8',
    )
    arc_ids = ('arc-a-b', 'arc-b-a')
    (scenario_bank / 'arc-catalog.csv').write_text(
        'arc_id\n' + ''.join(f'{arc_id}\n' for arc_id in arc_ids),
        encoding='utf-8',
    )
    (scenario_bank / 'travel-times.csv').write_text(
        'scenario_id,arc_id,day_index,boundary_index,travel_time\n'
        + ''.join(
            f'scenario-000001,{arc_id},{day_index},{boundary_index},5.0\n'
            for arc_id in arc_ids
            for day_index in (1, 2)
            for boundary_index in (0, 1, 2)
        ),
        encoding='utf-8',
    )
    instance_directory = manifest.parent
    (instance_directory / 'arcs.csv').write_text(
        'arc_id,tail_node_id,head_node_id\n'
        'arc-a-b,node-a,node-b\n'
        'arc-b-a,node-b,node-a\n',
        encoding='utf-8',
    )
    (instance_directory / 'depots.csv').write_text(
        'depot_id,node_id\ndepot-a,node-a\ndepot-b,node-b\n',
        encoding='utf-8',
    )
    (instance_directory / 'vehicles.csv').write_text(
        'vehicle_id,cargo_capacity,seat_capacity,initial_depot_id,max_tours_per_day\n'
        'vehicle-1,100.0,1,depot-a,1\n',
        encoding='utf-8',
    )
    (instance_directory / 'vehicle-overnight-depots.csv').write_text(
        'vehicle_id,depot_id\nvehicle-1,depot-a\nvehicle-1,depot-b\n',
        encoding='utf-8',
    )
    instance = load_vrp_instance(manifest)
    first_day = VehicleDayRoute(
        'vehicle-1',
        1,
        'depot-b',
        0.0,
        'driver-1',
        (),
        (RouteLeg(('arc-a-b',)),),
    )
    valid_second_day = VehicleDayRoute(
        'vehicle-1',
        2,
        'depot-b',
        1440.0,
        'driver-1',
        (),
        (RouteLeg(('arc-b-a', 'arc-a-b')),),
    )
    out_of_order_second_day = VehicleDayRoute(
        'vehicle-1',
        2,
        'depot-b',
        1440.0,
        'driver-1',
        (),
        (RouteLeg(('arc-a-b',)),),
    )
    objectives = build_vrp_objective_declaration(instance)
    evaluator = VrpApplicationEvaluator(instance)

    assert (
        representation_error(instance, VrpSolution((first_day, valid_second_day), ()))
        is None
    )
    evaluation = evaluator.evaluate(
        VrpSolution((out_of_order_second_day, first_day), ()), objectives
    )

    assert evaluation.representation_valid is False
    assert evaluation.feasible is None
    assert evaluation.objective_values is None
