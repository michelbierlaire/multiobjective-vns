"""Helpers for multi-day VRP input-loading tests.

Created: 2026-11-03T16:11:47+01:00
Author: Michel Bierlaire, EPFL
"""

from pathlib import Path


def write_minimal_vrp_bundle(root: Path) -> Path:
    """Write the smallest valid deterministic instance bundle.

    :param root: Temporary directory receiving the bundle.
    :returns: Path to the written instance manifest.
    """
    bank = root / 'scenario-bank'
    bank.mkdir(parents=True)
    (bank / 'scenario-bank.toml').write_text(
        """format_version = 1
arc_catalog_file = 'arc-catalog.csv'
target_grid_file = 'target-grid.csv'
time_zone = 'Europe/Zurich'
time_unit = 'minutes'
scenario_count = 1
scenarios_file = 'scenarios.csv'
travel_times_file = 'travel-times.csv'
post_horizon_rule_id = 'constant_last_boundary'
post_horizon_rule_configuration_id = 'builtin-v1'
""",
        encoding='utf-8',
    )
    (bank / 'arc-catalog.csv').write_text('arc_id\narc-1\n', encoding='utf-8')
    (bank / 'target-grid.csv').write_text(
        'day_index,planning_date,boundary_index,boundary_time\n'
        '1,2025-01-02,0,2025-01-02T00:00:00+01:00\n'
        '1,2025-01-02,1,2025-01-02T00:05:00+01:00\n'
        '1,2025-01-02,2,2025-01-03T00:00:00+01:00\n',
        encoding='utf-8',
    )
    (bank / 'scenarios.csv').write_text(
        'scenario_id,probability\nscenario-000001,1.0\n', encoding='utf-8'
    )
    (bank / 'travel-times.csv').write_text(
        'scenario_id,arc_id,day_index,boundary_index,travel_time\n'
        'scenario-000001,arc-1,1,0,5.0\n'
        'scenario-000001,arc-1,1,1,5.0\n'
        'scenario-000001,arc-1,1,2,5.0\n',
        encoding='utf-8',
    )

    instance = root / 'instance'
    instance.mkdir()
    (instance / 'instance.toml').write_text(
        """format_version = 1
instance_id = 'test-instance'
scenario_bank_file = '../scenario-bank/scenario-bank.toml'
load_unit = 'kg'
service_coverage_mode = 'mandatory'
staff_depot_mode = 'available_at_assignment'
rule_treatment = 'penalty'
active_simple_rules = []
hos_regime_id = 'none'
hos_regime_version = 'none'

[files]
nodes = 'nodes.csv'
arcs = 'arcs.csv'
depots = 'depots.csv'
vehicles = 'vehicles.csv'
vehicle_overnight_depots = 'vehicle-overnight-depots.csv'
personnel = 'personnel.csv'
objectives = 'objectives.toml'
""",
        encoding='utf-8',
    )
    (instance / 'nodes.csv').write_text('node_id\nnode-a\nnode-b\n', encoding='utf-8')
    (instance / 'arcs.csv').write_text(
        'arc_id,tail_node_id,head_node_id\narc-1,node-a,node-b\n',
        encoding='utf-8',
    )
    (instance / 'depots.csv').write_text(
        'depot_id,node_id\ndepot-1,node-a\n', encoding='utf-8'
    )
    (instance / 'vehicles.csv').write_text(
        'vehicle_id,cargo_capacity,seat_capacity,initial_depot_id,max_tours_per_day\n'
        'vehicle-1,100.0,1,depot-1,1\n',
        encoding='utf-8',
    )
    (instance / 'vehicle-overnight-depots.csv').write_text(
        'vehicle_id,depot_id\nvehicle-1,depot-1\n', encoding='utf-8'
    )
    (instance / 'personnel.csv').write_text(
        'person_id,authorized_driver,initial_depot_id\ndriver-1,true,\n',
        encoding='utf-8',
    )
    (instance / 'objectives.toml').write_text(
        """format_version = 1

[[objectives]]
objective_id = 'vehicles-used'
base_measure_id = 'vehicles_used'
base_measure_parameters = {}
aggregation = { id = 'weighted_mean' }
penalty_aggregation = { id = 'sum' }
        penalty_scales = { vehicle_availability = 1.0, personnel_availability = 1.0, duty_overlap = 1.0 }

[[penalty_functions]]
objective_id = 'vehicles-used'
violation_category = 'vehicle_availability'
rule_id = ''
function_id = 'linear'
parameters = { rate = 1.0 }

[[penalty_functions]]
objective_id = 'vehicles-used'
violation_category = 'personnel_availability'
rule_id = ''
function_id = 'linear'
parameters = { rate = 1.0 }

[[penalty_functions]]
objective_id = 'vehicles-used'
violation_category = 'duty_overlap'
rule_id = ''
function_id = 'linear'
parameters = { rate = 1.0 }

""",
        encoding='utf-8',
    )
    return instance / 'instance.toml'


def write_pickup_symmetry_bundle(root: Path) -> Path:
    """Write a minimal instance with two declared interchangeable pickups.

    :param root: Temporary directory receiving the bundle.
    :returns: Path to the written instance manifest.
    """
    manifest = write_minimal_vrp_bundle(root)
    instance_dir = manifest.parent
    source = manifest.read_text(encoding='utf-8')
    source = source.replace(
        '[files]\n',
        """[[pickup_symmetry_groups]]
group_id = 'same-pickup'
package_ids = ['package-a', 'package-b']

[files]
packages = 'packages.csv'
package_time_windows = 'package-time-windows.csv'
vehicle_package_compatibility = 'vehicle-package-compatibility.csv'
""",
    )
    manifest.write_text(source, encoding='utf-8')
    (instance_dir / 'packages.csv').write_text(
        'package_id,pickup_node_id,delivery_node_id,load,'
        'pickup_service_duration,delivery_service_duration,max_transfers,'
        'transfer_out_service_duration,transfer_in_service_duration\n'
        'package-a,node-a,node-b,1.0,0.0,0.0,0,0.0,0.0\n'
        'package-b,node-a,node-b,1.0,0.0,0.0,0,0.0,0.0\n',
        encoding='utf-8',
    )
    (instance_dir / 'package-time-windows.csv').write_text(
        'package_id,activity,window_index,start_time,end_time\n'
        'package-a,pickup,1,2025-01-02T00:00:00+01:00,'
        '2025-01-02T00:00:00+01:00\n'
        'package-a,delivery,1,2025-01-02T00:00:00+01:00,'
        '2025-01-03T00:00:00+01:00\n'
        'package-b,pickup,1,2025-01-02T00:00:00+01:00,'
        '2025-01-02T00:00:00+01:00\n'
        'package-b,delivery,1,2025-01-02T00:00:00+01:00,'
        '2025-01-03T00:00:00+01:00\n',
        encoding='utf-8',
    )
    (instance_dir / 'vehicle-package-compatibility.csv').write_text(
        'vehicle_id,package_id,compatible\n'
        'vehicle-1,package-a,true\n'
        'vehicle-1,package-b,true\n',
        encoding='utf-8',
    )
    objectives = instance_dir / 'objectives.toml'
    with objectives.open('a', encoding='utf-8') as file:
        file.write(
            """

[[objectives]]
objective_id = 'window-penalty'
base_measure_id = 'zero'
base_measure_parameters = {}
aggregation = { id = 'weighted_mean' }
penalty_aggregation = { id = 'sum' }
penalty_scales = { early_window = 1.0, late_window = 1.0 }

[[penalty_functions]]
objective_id = 'window-penalty'
violation_category = 'early_window'
rule_id = ''
function_id = 'linear'
parameters = { rate = 1.0 }

[[penalty_functions]]
objective_id = 'window-penalty'
violation_category = 'late_window'
rule_id = ''
function_id = 'linear'
parameters = { rate = 1.0 }
"""
        )
    return manifest
