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
