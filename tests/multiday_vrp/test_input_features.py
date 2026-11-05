"""Tests for tolerance-aware VRP feature-profile derivation.

Created: 2026-11-04T13:15:06+01:00
Author: Michel Bierlaire, EPFL
"""

from pathlib import Path

import pytest

from multiobjective_vns.multiday_vrp import load_vrp_instance
from multiobjective_vns.multiday_vrp.input_features import _derive_features
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    InterventionToolRecord,
    LoadedScenarioBank,
    LoadedVrpInstance,
    PersonRecord,
    PackageRecord,
    PackageTimeWindowRecord,
    PersonnelAvailabilityRecord,
    ScenarioRecord,
    ToolRecord,
    VehicleAvailabilityRecord,
    VrpFeatureId,
    VrpFleetData,
    VrpInterventionData,
    VrpPackageData,
)
from multiobjective_vns.multiday_vrp.input_validation import _numeric_tolerance

from .input_fixtures import write_minimal_vrp_bundle


def _features(
    instance: LoadedVrpInstance,
    *,
    scenario_bank: LoadedScenarioBank | None = None,
    fleet_data: VrpFleetData | None = None,
    package_data: VrpPackageData | None = None,
    intervention_data: VrpInterventionData | None = None,
) -> frozenset[VrpFeatureId]:
    return _derive_features(
        scenario_bank if scenario_bank is not None else instance.scenario_bank,
        instance.network_data,
        fleet_data if fleet_data is not None else instance.fleet_data,
        package_data if package_data is not None else instance.package_data,
        intervention_data
        if intervention_data is not None
        else instance.intervention_data,
        instance.personnel_data,
        instance.activity_relations,
        instance.event_data,
        instance.objective_data,
        instance.settings,
    ).active_features


def test_time_dependence_uses_the_full_profile_range(tmp_path: Path) -> None:
    instance = _load_instance(tmp_path)
    travel_times = dict(instance.scenario_bank.travel_times)
    travel_times.update(
        {
            ('scenario-000001', 'arc-1', 1, 0): 5.0,
            ('scenario-000001', 'arc-1', 1, 1): 5.0 + 0.75e-9,
            ('scenario-000001', 'arc-1', 1, 2): 5.0 + 1.5e-9,
        }
    )
    scenario_bank = instance.scenario_bank._replace(travel_times=travel_times)

    features = _features(instance, scenario_bank=scenario_bank)

    assert 'within_day_time_dependent_travel' in features


def test_scenario_uncertainty_uses_the_full_scenario_range(tmp_path: Path) -> None:
    instance = _load_instance(tmp_path)
    scenario_ids = ('scenario-000001', 'scenario-000002', 'scenario-000003')
    scenario_bank_data = instance.scenario_bank
    travel_times = {
        (scenario_id, 'arc-1', 1, boundary_index): 5.0 + offset
        for scenario_id, offset in zip(
            scenario_ids, (0.0, 0.75e-9, 1.5e-9), strict=True
        )
        for boundary_index in range(3)
    }
    scenario_bank = scenario_bank_data._replace(
        scenarios=tuple(
            ScenarioRecord(scenario_id, probability)
            for scenario_id, probability in zip(
                scenario_ids, (0.2, 0.3, 0.5), strict=True
            )
        ),
        travel_times=travel_times,
    )

    features = _features(instance, scenario_bank=scenario_bank)

    assert 'scenario_uncertainty' in features


@pytest.mark.parametrize(
    ('load', 'is_binding'),
    ((1.0 + 0.5e-9, False), (1.0 + 2.0e-9, True)),
)
def test_cargo_capacity_activation_uses_numeric_tolerance(
    tmp_path: Path,
    load: float,
    is_binding: bool,
) -> None:
    instance = _load_instance(tmp_path)
    vehicle = instance.fleet_data.vehicles[0]._replace(cargo_capacity=1.0)
    fleet_data = instance.fleet_data._replace(vehicles=(vehicle,))
    package = PackageRecord(
        'package-1', 'node-a', 'node-b', load, 0.0, 0.0, 0, 0.0, 0.0
    )
    package_data = instance.package_data._replace(packages=(package,))

    features = _features(instance, fleet_data=fleet_data, package_data=package_data)

    assert ('potentially_binding_cargo_tool_capacity' in features) is is_binding


@pytest.mark.parametrize(('duration', 'is_active'), ((0.5e-9, False), (2.0e-9, True)))
def test_positive_service_duration_activation_uses_numeric_tolerance(
    tmp_path: Path,
    duration: float,
    is_active: bool,
) -> None:
    instance = _load_instance(tmp_path)
    package = PackageRecord(
        'package-1', 'node-a', 'node-b', 1.0, duration, 0.0, 0, 0.0, 0.0
    )
    package_data = instance.package_data._replace(packages=(package,))

    features = _features(instance, package_data=package_data)

    assert ('positive_service_durations' in features) is is_active


@pytest.mark.parametrize(
    ('capacity_demand', 'is_active'), ((0.5e-9, False), (2.0e-9, True))
)
def test_tool_reservation_activation_uses_numeric_tolerance(
    tmp_path: Path,
    capacity_demand: float,
    is_active: bool,
) -> None:
    instance = _load_instance(tmp_path)
    intervention_data = instance.intervention_data._replace(
        interventions=(InterventionRecord('job-1', 'node-a', 0.0, 1),),
        tools=(ToolRecord('tool-1', capacity_demand),),
        intervention_tools=(InterventionToolRecord('job-1', 'tool-1'),),
    )

    features = _features(instance, intervention_data=intervention_data)

    assert ('tool_capacity_reservations' in features) is is_active


@pytest.mark.parametrize(('gap_multiple', 'is_restricted'), ((0.5, False), (4.0, True)))
def test_window_coverage_activation_uses_numeric_tolerance(
    tmp_path: Path,
    gap_multiple: float,
    is_restricted: bool,
) -> None:
    instance = _load_instance(tmp_path)
    package = PackageRecord('package-1', 'node-a', 'node-b', 1.0, 0.0, 0.0, 0, 0.0, 0.0)
    gap = gap_multiple * _numeric_tolerance(1440.0, 0.0)
    windows = tuple(
        PackageTimeWindowRecord('package-1', activity, 1, 0.0, 1440.0 - gap)
        for activity in ('pickup', 'delivery')
    )
    package_data = instance.package_data._replace(
        packages=(package,), package_time_windows=windows
    )

    features = _features(instance, package_data=package_data)

    assert ('restrictive_service_windows' in features) is is_restricted


@pytest.mark.parametrize(('gap_multiple', 'is_restricted'), ((0.5, False), (4.0, True)))
def test_availability_coverage_activation_uses_numeric_tolerance(
    tmp_path: Path,
    gap_multiple: float,
    is_restricted: bool,
) -> None:
    instance = _load_instance(tmp_path)
    gap = gap_multiple * _numeric_tolerance(1440.0, 0.0)
    fleet_data = instance.fleet_data._replace(
        vehicle_availability=(
            VehicleAvailabilityRecord('vehicle-1', 0.0, 1440.0 - gap),
        )
    )

    features = _features(instance, fleet_data=fleet_data)

    assert ('restricted_vehicle_availability' in features) is is_restricted


def test_personnel_availability_differentiation_ignores_shared_restrictions(
    tmp_path: Path,
) -> None:
    instance = _load_instance(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            PersonRecord('driver-1', True, None),
            PersonRecord('driver-2', True, None),
        ),
        personnel_availability=(
            PersonnelAvailabilityRecord('driver-1', 0.0, 100.0),
            PersonnelAvailabilityRecord('driver-2', 0.0, 100.0),
        ),
    )
    instance = instance._replace(personnel_data=personnel)

    shared_calendar_features = _features(instance)

    assert 'restricted_personnel_availability' in shared_calendar_features
    assert 'personnel_availability_differentiation' not in shared_calendar_features

    different_personnel = personnel._replace(
        personnel_availability=(
            PersonnelAvailabilityRecord('driver-1', 0.0, 100.0),
            PersonnelAvailabilityRecord('driver-2', 0.0, 80.0),
        ),
    )
    instance = instance._replace(personnel_data=different_personnel)

    assert 'personnel_availability_differentiation' in _features(instance)


def test_driver_assignment_differentiation_requires_a_meaningful_choice(
    tmp_path: Path,
) -> None:
    instance = _load_instance(tmp_path)
    personnel = instance.personnel_data._replace(
        personnel=(
            PersonRecord('driver-1', True, None),
            PersonRecord('driver-2', True, None),
        ),
        personnel_availability=(
            PersonnelAvailabilityRecord('driver-1', 0.0, 1440.0),
            PersonnelAvailabilityRecord('driver-2', 0.0, 1440.0),
        ),
    )
    instance = instance._replace(personnel_data=personnel)

    assert 'driver_assignment_differentiation' not in _features(instance)

    different_personnel = personnel._replace(
        personnel_availability=(
            PersonnelAvailabilityRecord('driver-1', 0.0, 1440.0),
            PersonnelAvailabilityRecord('driver-2', 0.0, 1200.0),
        ),
    )
    instance = instance._replace(personnel_data=different_personnel)

    assert 'driver_assignment_differentiation' in _features(instance)


def _load_instance(root: Path) -> LoadedVrpInstance:
    return load_vrp_instance(write_minimal_vrp_bundle(root))
