"""Tests for scenario-specific VRP route timeline simulation.

Created: 2026-11-03T18:32:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path

from multiobjective_vns.multiday_vrp import load_vrp_instance
from multiobjective_vns.multiday_vrp.evaluation.simulation import simulate_scenario
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.solution import (
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)

from .input_fixtures import write_minimal_vrp_bundle


def _instance_with_second_depot(root: Path) -> LoadedVrpInstance:
    manifest = write_minimal_vrp_bundle(root)
    instance_dir = manifest.parent
    (instance_dir / 'depots.csv').write_text(
        'depot_id,node_id\ndepot-1,node-a\ndepot-2,node-b\n', encoding='utf-8'
    )
    (instance_dir / 'vehicle-overnight-depots.csv').write_text(
        'vehicle_id,depot_id\nvehicle-1,depot-1\nvehicle-1,depot-2\n',
        encoding='utf-8',
    )
    return load_vrp_instance(manifest)


def test_simulation_uses_actual_entry_time_and_returns_an_ordered_trace(
    tmp_path: Path,
) -> None:
    instance = _instance_with_second_depot(tmp_path)
    solution = VrpSolution(
        routes=(
            VehicleDayRoute(
                vehicle_id='vehicle-1',
                day_index=1,
                overnight_depot_id='depot-2',
                start_time=2.5,
                driver_id='driver-1',
                stops=(),
                legs=(RouteLeg(('arc-1',)),),
            ),
        ),
        transfer_steps=(),
    )

    result = simulate_scenario(instance, solution, instance.scenario_bank.scenarios[0])

    assert result.scenario_id == 'scenario-000001'
    assert result.probability == 1.0
    assert len(result.vehicle_days) == 1
    vehicle_day = result.vehicle_days[0]
    assert vehicle_day.duty_start_time == 2.5
    assert vehicle_day.duty_end_time == 7.5
    traversal = vehicle_day.legs[0].arc_traversals[0]
    assert traversal.entry_time == 2.5
    assert traversal.travel_time == 5.0
    assert traversal.arrival_time == 7.5


def test_simulation_interpolates_travel_time_between_grid_boundaries(
    tmp_path: Path,
) -> None:
    instance = _instance_with_second_depot(tmp_path)
    bank_path = tmp_path / 'scenario-bank' / 'travel-times.csv'
    bank_path.write_text(
        'scenario_id,arc_id,day_index,boundary_index,travel_time\n'
        'scenario-000001,arc-1,1,0,0.0\n'
        'scenario-000001,arc-1,1,1,10.0\n'
        'scenario-000001,arc-1,1,2,10.0\n',
        encoding='utf-8',
    )
    instance = load_vrp_instance(tmp_path / 'instance' / 'instance.toml')
    solution = VrpSolution(
        routes=(
            VehicleDayRoute(
                vehicle_id='vehicle-1',
                day_index=1,
                overnight_depot_id='depot-2',
                start_time=2.5,
                driver_id='driver-1',
                stops=(),
                legs=(RouteLeg(('arc-1',)),),
            ),
        ),
        transfer_steps=(),
    )

    result = simulate_scenario(instance, solution, instance.scenario_bank.scenarios[0])

    traversal = result.vehicle_days[0].legs[0].arc_traversals[0]
    assert traversal.travel_time == 5.0
    assert traversal.arrival_time == 7.5
