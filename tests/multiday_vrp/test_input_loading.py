"""Tests for loading and validating complete multi-day VRP bundles.

Created: 2026-11-03T16:11:47+01:00
Author: Michel Bierlaire, EPFL
"""

from pathlib import Path

import pytest

from multiobjective_vns.multiday_vrp import VrpInputError, load_vrp_instance
from multiobjective_vns.multiday_vrp.extensions import (
    BaseMeasureRegistration,
    ExtensionParametersRequest,
    VrpExtensionConfigurationError,
    VrpExtensionRegistry,
)

from .input_fixtures import write_minimal_vrp_bundle


def test_loader_builds_normalized_instance_and_loads_scenario_bank(
    tmp_path: Path,
) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)

    instance = load_vrp_instance(manifest)

    assert instance.instance_id == 'test-instance'
    assert instance.scenario_bank.time_zone == 'Europe/Zurich'
    assert instance.scenario_bank.time_unit == 'minutes'
    assert instance.scenario_bank.planning_dates[0].isoformat() == '2025-01-02'
    assert instance.scenario_bank.day_boundaries == ((0.0, 5.0, 1440.0),)
    assert instance.scenario_bank.scenarios[0].probability == 1.0
    assert (
        instance.scenario_bank.travel_times[('scenario-000001', 'arc-1', 1, 1)] == 5.0
    )
    assert instance.network_data.arcs[0].tail_node_id == 'node-a'
    assert instance.fleet_data.vehicles[0].seat_capacity == 1
    assert instance.personnel_data.personnel[0].initial_depot_id is None
    assert instance.objective_data.objectives[0].objective_id == 'vehicles-used'
    assert 'directed_route_paths' in instance.feature_profile.active_features
    assert 'objective_declaration' in instance.feature_profile.active_features
    assert (
        'within_day_time_dependent_travel'
        not in instance.feature_profile.active_features
    )


def test_omitted_availability_files_expand_to_full_horizon(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)

    instance = load_vrp_instance(manifest)

    assert len(instance.fleet_data.vehicle_availability) == 1
    assert instance.fleet_data.vehicle_availability[0].start_time == 0.0
    assert instance.fleet_data.vehicle_availability[0].end_time == 1440.0
    assert len(instance.personnel_data.personnel_availability) == 1


def test_header_only_availability_file_means_empty_calendar(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    instance_dir = manifest.parent
    with manifest.open('a', encoding='utf-8') as file:
        file.write("vehicle_availability = 'vehicle-availability.csv'\n")
    (instance_dir / 'vehicle-availability.csv').write_text(
        'vehicle_id,start_time,end_time\n', encoding='utf-8'
    )

    instance = load_vrp_instance(manifest)

    assert instance.fleet_data.vehicle_availability == ()


def test_optional_tables_become_empty_tuples(tmp_path: Path) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))

    assert instance.network_data.transfer_facilities == ()
    assert instance.package_data.packages == ()
    assert instance.intervention_data.interventions == ()
    assert instance.event_data.events == ()
    assert instance.activity_relations.precedence == ()


@pytest.mark.parametrize(
    ('relative_path', 'contents', 'expected_file'),
    [
        ('nodes.csv', 'node_id,unexpected\nnode-a,x\n', 'nodes.csv'),
        (
            'arcs.csv',
            'arc_id,tail_node_id,head_node_id\narc-1,missing,node-b\n',
            'arcs.csv',
        ),
        (
            'vehicles.csv',
            'vehicle_id,cargo_capacity,seat_capacity,initial_depot_id,max_tours_per_day\nvehicle-1,100.0,true,depot-1,1\n',
            'vehicles.csv',
        ),
    ],
)
def test_invalid_csv_schema_or_references_report_the_source_file(
    tmp_path: Path,
    relative_path: str,
    contents: str,
    expected_file: str,
) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    (manifest.parent / relative_path).write_text(contents, encoding='utf-8')

    with pytest.raises(VrpInputError) as error:
        load_vrp_instance(manifest)

    assert error.value.file == expected_file
    assert error.value.reason


def test_scenario_bank_requires_complete_travel_time_profiles(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    bank_file = tmp_path / 'scenario-bank' / 'travel-times.csv'
    bank_file.write_text(
        'scenario_id,arc_id,day_index,boundary_index,travel_time\n'
        'scenario-000001,arc-1,1,0,5.0\n'
        'scenario-000001,arc-1,1,1,5.0\n',
        encoding='utf-8',
    )

    with pytest.raises(VrpInputError, match='missing'):
        load_vrp_instance(manifest)


def test_scenario_bank_rejects_non_fifo_profiles(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    bank_file = tmp_path / 'scenario-bank' / 'travel-times.csv'
    bank_file.write_text(
        'scenario_id,arc_id,day_index,boundary_index,travel_time\n'
        'scenario-000001,arc-1,1,0,20.0\n'
        'scenario-000001,arc-1,1,1,0.0\n'
        'scenario-000001,arc-1,1,2,0.0\n',
        encoding='utf-8',
    )

    with pytest.raises(VrpInputError, match='FIFO'):
        load_vrp_instance(manifest)


def test_bank_calendar_and_instance_arc_catalog_must_match(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    (manifest.parent / 'arcs.csv').write_text(
        'arc_id,tail_node_id,head_node_id\narc-other,node-a,node-b\n',
        encoding='utf-8',
    )

    with pytest.raises(VrpInputError, match='arc catalog'):
        load_vrp_instance(manifest)


def test_custom_base_measure_registration_validates_its_parameters(
    tmp_path: Path,
) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    objectives = manifest.parent / 'objectives.toml'
    source = objectives.read_text(encoding='utf-8').replace(
        "base_measure_id = 'vehicles_used'\nbase_measure_parameters = {}",
        "base_measure_id = 'custom_metric'\nbase_measure_parameters = { factor = 2.0 }",
    )
    objectives.write_text(source, encoding='utf-8')
    received: list[str] = []

    def validate(request: ExtensionParametersRequest) -> None:
        received.append(request.objective_id)

    registry = VrpExtensionRegistry(
        registry_id='test.extensions',
        configuration_id='test-extensions-v1',
        base_measures=(
            BaseMeasureRegistration(
                extension_id='custom_metric',
                component_id='test:custom-metric',
                configuration_id='custom-metric-v1',
                validate_parameters=validate,
                evaluate=lambda request: 0.0,
            ),
        ),
    )

    instance = load_vrp_instance(manifest, extension_registry=registry)

    assert instance.objective_data.objectives[0].base_measure_id == 'custom_metric'
    assert received == ['vehicles-used']


def test_custom_extension_validator_failure_has_input_context(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    objectives = manifest.parent / 'objectives.toml'
    source = objectives.read_text(encoding='utf-8').replace(
        "base_measure_id = 'vehicles_used'\nbase_measure_parameters = {}",
        "base_measure_id = 'custom_metric'\nbase_measure_parameters = {}",
    )
    objectives.write_text(source, encoding='utf-8')

    def fail(request: ExtensionParametersRequest) -> None:
        raise RuntimeError('bad custom parameters')

    registry = VrpExtensionRegistry(
        registry_id='test.extensions',
        configuration_id='test-extensions-v1',
        base_measures=(
            BaseMeasureRegistration(
                extension_id='custom_metric',
                component_id='test:custom-metric',
                configuration_id='custom-metric-v1',
                validate_parameters=fail,
                evaluate=lambda request: 0.0,
            ),
        ),
    )

    with pytest.raises(VrpExtensionConfigurationError) as error:
        load_vrp_instance(manifest, extension_registry=registry)

    assert error.value.extension_id == 'custom_metric'
    assert error.value.input_location.startswith('objectives.toml.')


def test_unknown_custom_extension_is_rejected_before_search(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    objectives = manifest.parent / 'objectives.toml'
    source = objectives.read_text(encoding='utf-8').replace(
        "base_measure_id = 'vehicles_used'", "base_measure_id = 'unknown_metric'"
    )
    objectives.write_text(source, encoding='utf-8')

    with pytest.raises(VrpInputError, match='unknown'):
        load_vrp_instance(manifest)


def test_duplicate_csv_header_reports_column_location(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    (manifest.parent / 'nodes.csv').write_text(
        'node_id,node_id\nnode-a,node-a\n', encoding='utf-8'
    )

    with pytest.raises(VrpInputError) as error:
        load_vrp_instance(manifest)

    assert error.value.file == 'nodes.csv'
    assert error.value.column == 'node_id'
    assert 'duplicate' in error.value.reason


def test_scenario_bank_timestamps_must_match_declared_time_zone(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    grid = tmp_path / 'scenario-bank' / 'target-grid.csv'
    grid.write_text(
        grid.read_text(encoding='utf-8').replace('+01:00', '+02:00'),
        encoding='utf-8',
    )

    with pytest.raises(VrpInputError, match='UTC offset'):
        load_vrp_instance(manifest)


def test_loader_rejects_mandatory_colocation_at_different_nodes(
    tmp_path: Path,
) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    instance_dir = manifest.parent
    manifest.write_text(
        manifest.read_text(encoding='utf-8')
        .replace(
            '[files]\nnodes =',
            "[files]\npackages = 'packages.csv'\nco_location = 'co-location.csv'\nnodes =",
        )
        .replace(
            "[files]\npackages = 'packages.csv'",
            "[files]\npackage_time_windows = 'package-time-windows.csv'\n"
            "vehicle_package_compatibility = 'vehicle-package-compatibility.csv'\n"
            "packages = 'packages.csv'",
        ),
        encoding='utf-8',
    )
    (instance_dir / 'packages.csv').write_text(
        'package_id,pickup_node_id,delivery_node_id,load,pickup_service_duration,'
        'delivery_service_duration,max_transfers,transfer_out_service_duration,'
        'transfer_in_service_duration\n'
        'parcel-1,node-a,node-b,1,0,0,0,0,0\n',
        encoding='utf-8',
    )
    (instance_dir / 'co-location.csv').write_text(
        'activity_kind_1,activity_id_1,activity_kind_2,activity_id_2\n'
        'delivery,parcel-1,pickup,parcel-1\n',
        encoding='utf-8',
    )
    (instance_dir / 'package-time-windows.csv').write_text(
        'package_id,activity,window_index,start_time,end_time\n'
        'parcel-1,pickup,1,2025-01-02T00:00:00+01:00,2025-01-02T00:05:00+01:00\n'
        'parcel-1,delivery,1,2025-01-02T00:00:00+01:00,2025-01-02T00:05:00+01:00\n',
        encoding='utf-8',
    )
    (instance_dir / 'vehicle-package-compatibility.csv').write_text(
        'vehicle_id,package_id,compatible\nvehicle-1,parcel-1,true\n',
        encoding='utf-8',
    )

    with pytest.raises(VrpInputError, match='different nodes'):
        load_vrp_instance(manifest)


def test_duplicate_package_window_index_reports_its_source_file(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    instance_dir = manifest.parent
    with manifest.open('a', encoding='utf-8') as file:
        file.write(
            "packages = 'packages.csv'\n"
            "package_time_windows = 'package-time-windows.csv'\n"
            "vehicle_package_compatibility = 'vehicle-package-compatibility.csv'\n"
        )
    (instance_dir / 'packages.csv').write_text(
        'package_id,pickup_node_id,delivery_node_id,load,pickup_service_duration,'
        'delivery_service_duration,max_transfers,transfer_out_service_duration,'
        'transfer_in_service_duration\n'
        'parcel-1,node-a,node-b,1,0,0,0,0,0\n',
        encoding='utf-8',
    )
    (instance_dir / 'package-time-windows.csv').write_text(
        'package_id,activity,window_index,start_time,end_time\n'
        'parcel-1,pickup,1,2025-01-02T00:00:00+01:00,2025-01-02T12:00:00+01:00\n'
        'parcel-1,pickup,1,2025-01-02T12:00:00+01:00,2025-01-03T00:00:00+01:00\n'
        'parcel-1,delivery,1,2025-01-02T00:00:00+01:00,2025-01-03T00:00:00+01:00\n',
        encoding='utf-8',
    )
    (instance_dir / 'vehicle-package-compatibility.csv').write_text(
        'vehicle_id,package_id,compatible\nvehicle-1,parcel-1,true\n',
        encoding='utf-8',
    )

    with pytest.raises(VrpInputError) as error:
        load_vrp_instance(manifest)

    assert error.value.file == 'package-time-windows.csv'
    assert error.value.column is not None
    assert 'window_index' in error.value.column


def test_loader_normalizes_package_and_intervention_tables(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    instance_dir = manifest.parent
    with manifest.open('a', encoding='utf-8') as file:
        file.write(
            "packages = 'packages.csv'\n"
            "package_time_windows = 'package-time-windows.csv'\n"
            "vehicle_package_compatibility = 'vehicle-package-compatibility.csv'\n"
            "interventions = 'interventions.csv'\n"
            "intervention_time_windows = 'intervention-time-windows.csv'\n"
        )
    (instance_dir / 'packages.csv').write_text(
        'package_id,pickup_node_id,delivery_node_id,load,pickup_service_duration,'
        'delivery_service_duration,max_transfers,transfer_out_service_duration,'
        'transfer_in_service_duration\n'
        'parcel-1,node-a,node-b,2.5,4,6,0,0,0\n',
        encoding='utf-8',
    )
    (instance_dir / 'package-time-windows.csv').write_text(
        'package_id,activity,window_index,start_time,end_time\n'
        'parcel-1,pickup,1,2025-01-02T00:00:00+01:00,2025-01-03T00:00:00+01:00\n'
        'parcel-1,delivery,1,2025-01-02T00:00:00+01:00,2025-01-03T00:00:00+01:00\n',
        encoding='utf-8',
    )
    (instance_dir / 'vehicle-package-compatibility.csv').write_text(
        'vehicle_id,package_id,compatible\nvehicle-1,parcel-1,true\n',
        encoding='utf-8',
    )
    (instance_dir / 'interventions.csv').write_text(
        'intervention_id,node_id,service_duration,minimum_team_size\n'
        'repair-1,node-b,12,1\n',
        encoding='utf-8',
    )
    (instance_dir / 'intervention-time-windows.csv').write_text(
        'intervention_id,day_index,window_index,start_time,end_time\n'
        'repair-1,1,1,2025-01-02T00:00:00+01:00,2025-01-03T00:00:00+01:00\n',
        encoding='utf-8',
    )
    objectives = instance_dir / 'objectives.toml'
    objectives.write_text(
        objectives.read_text(encoding='utf-8').replace(
            'duty_overlap = 1.0 }',
            'duty_overlap = 1.0, early_window = 1.0, late_window = 1.0 }',
        )
        + """

[[penalty_functions]]
objective_id = 'vehicles-used'
violation_category = 'early_window'
rule_id = ''
function_id = 'linear'
parameters = { rate = 1.0 }

[[penalty_functions]]
objective_id = 'vehicles-used'
violation_category = 'late_window'
rule_id = ''
function_id = 'linear'
parameters = { rate = 1.0 }
""",
        encoding='utf-8',
    )

    instance = load_vrp_instance(manifest)

    assert instance.package_data.packages[0].load == 2.5
    assert instance.package_data.package_time_windows[0].activity == 'pickup'
    assert instance.package_data.vehicle_package_compatibility[0].compatible
    assert instance.intervention_data.interventions[0].intervention_id == 'repair-1'
    assert instance.intervention_data.interventions[0].service_duration == 12.0


def test_loader_links_activity_relations_and_event_objectives(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    instance_dir = manifest.parent
    with manifest.open('a', encoding='utf-8') as file:
        file.write(
            "packages = 'packages.csv'\n"
            "package_time_windows = 'package-time-windows.csv'\n"
            "vehicle_package_compatibility = 'vehicle-package-compatibility.csv'\n"
            "precedence = 'precedence.csv'\n"
            "event_types = 'event-types.csv'\n"
            "events = 'events.csv'\n"
            "objective_event_types = 'objective-event-types.csv'\n"
        )
    (instance_dir / 'packages.csv').write_text(
        'package_id,pickup_node_id,delivery_node_id,load,pickup_service_duration,'
        'delivery_service_duration,max_transfers,transfer_out_service_duration,'
        'transfer_in_service_duration\n'
        'parcel-1,node-a,node-b,1,0,0,0,0,0\n',
        encoding='utf-8',
    )
    (instance_dir / 'package-time-windows.csv').write_text(
        'package_id,activity,window_index,start_time,end_time\n'
        'parcel-1,pickup,1,2025-01-02T00:00:00+01:00,2025-01-03T00:00:00+01:00\n'
        'parcel-1,delivery,1,2025-01-02T00:00:00+01:00,2025-01-03T00:00:00+01:00\n',
        encoding='utf-8',
    )
    (instance_dir / 'vehicle-package-compatibility.csv').write_text(
        'vehicle_id,package_id,compatible\nvehicle-1,parcel-1,true\n',
        encoding='utf-8',
    )
    (instance_dir / 'precedence.csv').write_text(
        'before_activity_kind,before_activity_id,after_activity_kind,after_activity_id\n'
        'pickup,parcel-1,delivery,parcel-1\n',
        encoding='utf-8',
    )
    (instance_dir / 'event-types.csv').write_text(
        'event_type_id,location_domain\ntraffic-light,node\n', encoding='utf-8'
    )
    (instance_dir / 'events.csv').write_text(
        'event_id,event_type_id,location_id\nevent-1,traffic-light,node-b\n',
        encoding='utf-8',
    )
    (instance_dir / 'objective-event-types.csv').write_text(
        'objective_id,event_type_id\nvehicles-used,traffic-light\n',
        encoding='utf-8',
    )
    objectives = instance_dir / 'objectives.toml'
    objectives.write_text(
        objectives.read_text(encoding='utf-8').replace(
            "base_measure_id = 'vehicles_used'", "base_measure_id = 'event_count'"
        ),
        encoding='utf-8',
    )
    objectives.write_text(
        objectives.read_text(encoding='utf-8').replace(
            'duty_overlap = 1.0 }',
            'duty_overlap = 1.0, early_window = 1.0, late_window = 1.0 }',
        )
        + """

[[penalty_functions]]
objective_id = 'vehicles-used'
violation_category = 'early_window'
rule_id = ''
function_id = 'linear'
parameters = { rate = 1.0 }

[[penalty_functions]]
objective_id = 'vehicles-used'
violation_category = 'late_window'
rule_id = ''
function_id = 'linear'
parameters = { rate = 1.0 }
""",
        encoding='utf-8',
    )

    instance = load_vrp_instance(manifest)

    assert instance.activity_relations.precedence[0].before_activity_kind == 'pickup'
    assert instance.event_data.events[0].location_id == 'node-b'
    assert instance.event_data.objective_event_types[0].event_type_id == 'traffic-light'
    assert 'network_event_exposure' in instance.feature_profile.active_features


def test_loader_normalizes_active_work_rule_tables(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    instance_dir = manifest.parent
    manifest.write_text(
        manifest.read_text(encoding='utf-8').replace(
            'active_simple_rules = []',
            "active_simple_rules = ['daily_work', 'daily_driving', "
            "'continuous_driving', 'minimum_rest']",
        ),
        encoding='utf-8',
    )
    objectives = instance_dir / 'objectives.toml'
    objectives.write_text(
        objectives.read_text(encoding='utf-8').replace(
            'duty_overlap = 1.0 }',
            'duty_overlap = 1.0, simple_rule = 1.0 }',
        )
        + """

[[penalty_functions]]
objective_id = 'vehicles-used'
violation_category = 'simple_rule'
rule_id = ''
function_id = 'linear'
parameters = { rate = 1.0 }
""",
        encoding='utf-8',
    )
    with manifest.open('a', encoding='utf-8') as file:
        file.write(
            "daily_work_limits = 'daily-work-limits.csv'\n"
            "daily_driving_limits = 'daily-driving-limits.csv'\n"
            "continuous_driving_limits = 'continuous-driving-limits.csv'\n"
            "person_rule_limits = 'person-rule-limits.csv'\n"
        )
    (instance_dir / 'daily-work-limits.csv').write_text(
        'person_id,day_index,max_work_time\ndriver-1,1,600\n', encoding='utf-8'
    )
    (instance_dir / 'daily-driving-limits.csv').write_text(
        'driver_id,day_index,max_driving_time\ndriver-1,1,480\n', encoding='utf-8'
    )
    (instance_dir / 'continuous-driving-limits.csv').write_text(
        'driver_id,max_continuous_driving_time\ndriver-1,240\n', encoding='utf-8'
    )
    (instance_dir / 'person-rule-limits.csv').write_text(
        'person_id,minimum_rest_time,qualifying_break_minimum\ndriver-1,600,30\n',
        encoding='utf-8',
    )

    instance = load_vrp_instance(manifest)

    assert instance.work_rule_data.daily_work_limits[0].max_work_time == 600.0
    assert instance.work_rule_data.daily_driving_limits[0].max_driving_time == 480.0
    assert (
        instance.work_rule_data.continuous_driving_limits[0].max_continuous_driving_time
        == 240.0
    )
    assert instance.work_rule_data.person_rule_limits[0].minimum_rest_time == 600.0
