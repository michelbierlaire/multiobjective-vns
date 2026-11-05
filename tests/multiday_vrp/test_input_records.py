"""Tests for immutable normalized VRP input records.

Created: 2026-11-03T16:11:47+01:00
Author: Michel Bierlaire, EPFL
"""

from typing import get_args, get_type_hints

import pytest

from multiobjective_vns.multiday_vrp.input_records import (
    FrozenTomlTable,
    VrpFeatureProfile,
)


def test_frozen_toml_table_recursively_freezes_nested_tables_and_arrays() -> None:
    table = FrozenTomlTable({'outer': {'values': [1, {'enabled': True}]}})

    outer = table['outer']
    assert isinstance(outer, FrozenTomlTable)
    values = outer['values']
    assert isinstance(values, tuple)
    assert values == (1, FrozenTomlTable({'enabled': True}))
    with pytest.raises(TypeError):
        table['new'] = 'value'  # type: ignore[index]
    nested = values[1]
    assert isinstance(nested, FrozenTomlTable)
    with pytest.raises(TypeError):
        nested['enabled'] = False  # type: ignore[index]


def test_feature_profile_is_an_immutable_set_of_active_feature_ids() -> None:
    profile = VrpFeatureProfile(
        active_features=frozenset({'directed_route_paths', 'objective_declaration'})
    )

    assert 'directed_route_paths' in profile.active_features
    assert 'scenario_uncertainty' not in profile.active_features
    with pytest.raises(AttributeError):
        profile.active_features = frozenset()  # type: ignore[misc]


def test_feature_profile_uses_the_closed_design_feature_catalog() -> None:
    active_features_type = get_type_hints(VrpFeatureProfile)['active_features']
    literal_type = get_args(active_features_type)[0]

    assert set(get_args(literal_type)) == {
        'directed_route_paths',
        'within_day_time_dependent_travel',
        'scenario_uncertainty',
        'network_event_exposure',
        'multi_day_planning',
        'overnight_depot_choice',
        'multiple_tours_per_day',
        'restricted_vehicle_availability',
        'potentially_binding_cargo_tool_capacity',
        'potentially_binding_personnel_seat_capacity',
        'package_vehicle_compatibility',
        'package_transfer_operations',
        'transfer_facility_storage',
        'package_pickup_delivery',
        'positive_service_durations',
        'optional_service_omission',
        'restrictive_service_windows',
        'interventions',
        'tool_capacity_reservations',
        'additional_activity_relationships',
        'driving_skill_restrictions',
        'intervention_qualifications_or_team_size',
        'restricted_personnel_availability',
        'personnel_availability_differentiation',
        'driver_assignment_differentiation',
        'staff_depot_continuity',
        'work_driving_rest_hos_rules',
        'objective_declaration',
        'multiple_objectives',
        'scenario_aggregation',
    }
