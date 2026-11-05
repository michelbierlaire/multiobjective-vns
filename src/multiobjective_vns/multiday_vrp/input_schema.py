"""Version-one manifest keys and CSV table schemas.

Created: 2026-11-03T16:33:58+01:00
Author: Michel Bierlaire, EPFL
"""

MANIFEST_REQUIRED = frozenset(
    {
        'format_version',
        'instance_id',
        'scenario_bank_file',
        'load_unit',
        'service_coverage_mode',
        'staff_depot_mode',
        'rule_treatment',
        'active_simple_rules',
        'hos_regime_id',
        'hos_regime_version',
        'files',
    }
)
MANIFEST_OPTIONAL = frozenset({'application_data_file', 'hos_configuration_file'})

FILE_COLUMNS: dict[str, tuple[str, ...]] = {
    'nodes': ('node_id',),
    'arcs': ('arc_id', 'tail_node_id', 'head_node_id'),
    'depots': ('depot_id', 'node_id'),
    'transfer_facilities': ('node_id', 'storage_capacity'),
    'vehicles': (
        'vehicle_id',
        'cargo_capacity',
        'seat_capacity',
        'initial_depot_id',
        'max_tours_per_day',
    ),
    'vehicle_overnight_depots': ('vehicle_id', 'depot_id'),
    'vehicle_driving_skills': ('vehicle_id', 'skill_id'),
    'vehicle_availability': ('vehicle_id', 'start_time', 'end_time'),
    'packages': (
        'package_id',
        'pickup_node_id',
        'delivery_node_id',
        'load',
        'pickup_service_duration',
        'delivery_service_duration',
        'max_transfers',
        'transfer_out_service_duration',
        'transfer_in_service_duration',
    ),
    'package_time_windows': (
        'package_id',
        'activity',
        'window_index',
        'start_time',
        'end_time',
    ),
    'vehicle_package_compatibility': ('vehicle_id', 'package_id', 'compatible'),
    'package_transfer_facilities': ('package_id', 'node_id'),
    'interventions': (
        'intervention_id',
        'node_id',
        'service_duration',
        'minimum_team_size',
    ),
    'intervention_time_windows': (
        'intervention_id',
        'day_index',
        'window_index',
        'start_time',
        'end_time',
    ),
    'tools': ('tool_id', 'capacity_demand'),
    'intervention_tools': ('intervention_id', 'tool_id'),
    'intervention_qualifications_required': ('intervention_id', 'qualification_id'),
    'intervention_qualifications': ('qualification_id',),
    'personnel': ('person_id', 'authorized_driver', 'initial_depot_id'),
    'driving_skills': ('skill_id',),
    'personnel_driving_skills': ('person_id', 'skill_id'),
    'personnel_intervention_qualifications': ('person_id', 'qualification_id'),
    'personnel_availability': ('person_id', 'start_time', 'end_time'),
    'precedence': (
        'before_activity_kind',
        'before_activity_id',
        'after_activity_kind',
        'after_activity_id',
    ),
    'co_location': (
        'activity_kind_1',
        'activity_id_1',
        'activity_kind_2',
        'activity_id_2',
    ),
    'event_types': ('event_type_id', 'location_domain'),
    'events': ('event_id', 'event_type_id', 'location_id'),
    'objective_event_types': ('objective_id', 'event_type_id'),
    'objective_omission_penalties': (
        'objective_id',
        'activity_kind',
        'activity_id',
        'amount',
    ),
    'daily_work_limits': ('person_id', 'day_index', 'max_work_time'),
    'daily_driving_limits': ('driver_id', 'day_index', 'max_driving_time'),
    'person_rule_limits': (
        'person_id',
        'minimum_rest_time',
        'qualifying_break_minimum',
    ),
    'continuous_driving_limits': ('driver_id', 'max_continuous_driving_time'),
}
FILE_NAMES = {
    'transfer_facilities': 'transfer-facilities.csv',
    'vehicle_overnight_depots': 'vehicle-overnight-depots.csv',
    'vehicle_driving_skills': 'vehicle-driving-skills.csv',
    'vehicle_availability': 'vehicle-availability.csv',
    'package_time_windows': 'package-time-windows.csv',
    'vehicle_package_compatibility': 'vehicle-package-compatibility.csv',
    'package_transfer_facilities': 'package-transfer-facilities.csv',
    'intervention_time_windows': 'intervention-time-windows.csv',
    'intervention_qualifications_required': 'intervention-qualifications-required.csv',
    'intervention_qualifications': 'intervention-qualifications.csv',
    'personnel_driving_skills': 'personnel-driving-skills.csv',
    'personnel_intervention_qualifications': 'personnel-intervention-qualifications.csv',
    'personnel_availability': 'personnel-availability.csv',
    'co_location': 'co-location.csv',
    'objective_event_types': 'objective-event-types.csv',
    'objective_omission_penalties': 'objective-omission-penalties.csv',
    'daily_work_limits': 'daily-work-limits.csv',
    'daily_driving_limits': 'daily-driving-limits.csv',
    'person_rule_limits': 'person-rule-limits.csv',
    'continuous_driving_limits': 'continuous-driving-limits.csv',
}
BASE_FILE_KEYS = frozenset(
    {
        'nodes',
        'arcs',
        'depots',
        'vehicles',
        'vehicle_overnight_depots',
        'personnel',
        'objectives',
    }
)
ALLOWED_FILE_KEYS = frozenset(FILE_COLUMNS) | {'objectives'}
SIMPLE_RULES = frozenset(
    {'daily_work', 'daily_driving', 'continuous_driving', 'minimum_rest'}
)
BUILTIN_BASE_MEASURES = frozenset(
    {
        'total_travel_time',
        'event_count',
        'maximum_event_exposure',
        'total_route_duration',
        'maximum_route_duration',
        'total_personnel_work_time',
        'maximum_personnel_work_time',
        'total_driver_work_time',
        'maximum_driver_work_time',
        'vehicles_used',
        'vehicle_days_used',
        'tours_used',
    }
)
BUILTIN_AGGREGATIONS = frozenset(
    {'weighted_mean', 'weighted_quantile', 'weighted_standard_deviation', 'maximum'}
)
BUILTIN_PENALTY_AGGREGATIONS = frozenset({'sum'})
PENALTY_CATEGORIES = (
    'early_window',
    'late_window',
    'vehicle_availability',
    'personnel_availability',
    'duty_overlap',
    'simple_rule',
    'hos_rule',
)
UNIT_SECONDS = {'seconds': 1.0, 'minutes': 60.0, 'hours': 3600.0}
