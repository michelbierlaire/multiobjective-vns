"""Feature-profile derivation from normalized instance data.

Created: 2026-11-03T16:34:50+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from multiobjective_vns.multiday_vrp.input_records import (
    LoadedScenarioBank,
    VrpActivityRelationData,
    VrpEventData,
    VrpFeatureId,
    VrpFeatureProfile,
    VrpFleetData,
    VrpInstanceSettings,
    VrpInterventionData,
    VrpNetworkData,
    VrpObjectiveData,
    VrpPackageData,
    VrpPersonnelData,
    VrpSpatialData,
)
from multiobjective_vns.multiday_vrp.input_validation import (
    _has_restrictive_windows,
    _intervals_cover_horizon,
    _numeric_tolerance,
)


def _derive_features(
    scenario_bank: LoadedScenarioBank,
    network: VrpNetworkData,
    fleet: VrpFleetData,
    packages: VrpPackageData,
    interventions: VrpInterventionData,
    personnel: VrpPersonnelData,
    relations: VrpActivityRelationData,
    events: VrpEventData,
    objectives: VrpObjectiveData,
    settings: VrpInstanceSettings,
    spatial_data: VrpSpatialData | None = None,
) -> VrpFeatureProfile:
    features: set[VrpFeatureId] = {
        'directed_route_paths',
        'objective_declaration',
    }
    if spatial_data is not None and spatial_data.coordinate_system_id is not None:
        features.add('planar_node_coordinates')
    travel = scenario_bank.travel_times
    for scenario in scenario_bank.scenarios:
        for arc_id in scenario_bank.arc_ids:
            for day_index, boundaries in enumerate(
                scenario_bank.day_boundaries, start=1
            ):
                values = tuple(
                    travel[(scenario.scenario_id, arc_id, day_index, boundary_index)]
                    for boundary_index in range(len(boundaries))
                )
                if _range_exceeds_tolerance(values):
                    features.add('within_day_time_dependent_travel')
    if len(scenario_bank.scenarios) > 1:
        for arc_id in scenario_bank.arc_ids:
            for day_index, boundaries in enumerate(
                scenario_bank.day_boundaries, start=1
            ):
                for boundary_index in range(len(boundaries)):
                    scenario_values = tuple(
                        scenario_bank.travel_times[
                            (scenario.scenario_id, arc_id, day_index, boundary_index)
                        ]
                        for scenario in scenario_bank.scenarios
                    )
                    if _range_exceeds_tolerance(scenario_values):
                        features.add('scenario_uncertainty')
                        break
                if 'scenario_uncertainty' in features:
                    break
            if 'scenario_uncertainty' in features:
                break
    selected_event_types = {item.event_type_id for item in events.objective_event_types}
    if any(item.event_type_id in selected_event_types for item in events.events):
        features.add('network_event_exposure')
    if len(scenario_bank.planning_dates) > 1:
        features.add('multi_day_planning')
    if settings.exact_active_vehicle_count is None:
        features.add('variable_active_vehicle_count')
    overnight: dict[str, set[str]] = {}
    for item in fleet.vehicle_overnight_depots:
        overnight.setdefault(item.vehicle_id, set()).add(item.depot_id)
    if any(len(values) > 1 for values in overnight.values()):
        features.add('overnight_depot_choice')
    if any(item.max_tours_per_day > 1 for item in fleet.vehicles):
        features.add('multiple_tours_per_day')
    if any(
        not _intervals_cover_horizon(
            tuple(
                (item.start_time, item.end_time)
                for item in fleet.vehicle_availability
                if item.vehicle_id == entity.vehicle_id
            ),
            scenario_bank.day_boundaries[0][0],
            scenario_bank.day_boundaries[-1][-1],
        )
        for entity in fleet.vehicles
    ):
        features.add('restricted_vehicle_availability')
    package_load = sum(item.load for item in packages.packages)
    used_tool_ids = {item.tool_id for item in interventions.intervention_tools}
    tool_load = sum(
        item.capacity_demand
        for item in interventions.tools
        if item.tool_id in used_tool_ids
    )
    all_load = package_load + tool_load
    if any(
        item.cargo_capacity + _numeric_tolerance(item.cargo_capacity, all_load)
        < all_load
        for item in fleet.vehicles
    ):
        features.add('potentially_binding_cargo_tool_capacity')
    if interventions.interventions and any(
        item.seat_capacity < len(personnel.personnel) for item in fleet.vehicles
    ):
        features.add('potentially_binding_personnel_seat_capacity')
    if any(not item.compatible for item in packages.vehicle_package_compatibility):
        features.add('package_vehicle_compatibility')
    if any(item.max_transfers > 0 for item in packages.packages):
        features.add('package_transfer_operations')
    facility_nodes = {item.node_id for item in network.transfer_facilities}
    if (
        any(item.pickup_node_id in facility_nodes for item in packages.packages)
        or packages.package_transfer_facilities
    ):
        features.add('transfer_facility_storage')
    if packages.packages:
        features.add('package_pickup_delivery')
    if any(
        _positive_beyond_tolerance(value)
        for package in packages.packages
        for value in (
            package.pickup_service_duration,
            package.delivery_service_duration,
            package.transfer_out_service_duration,
            package.transfer_in_service_duration,
        )
    ) or any(
        _positive_beyond_tolerance(item.service_duration)
        for item in interventions.interventions
    ):
        features.add('positive_service_durations')
    if settings.service_coverage_mode == 'penalized_omission' and (
        packages.packages or interventions.interventions
    ):
        features.add('optional_service_omission')
    if _has_restrictive_windows(scenario_bank, packages, interventions):
        features.add('restrictive_service_windows')
    if interventions.interventions:
        features.add('interventions')
    positive_tools = {
        item.tool_id
        for item in interventions.tools
        if _positive_beyond_tolerance(item.capacity_demand)
    }
    if any(item.tool_id in positive_tools for item in interventions.intervention_tools):
        features.add('tool_capacity_reservations')
    if relations.precedence or relations.co_location:
        features.add('additional_activity_relationships')
    if any(item.skill_id for item in fleet.vehicle_driving_skills):
        features.add('driving_skill_restrictions')
    required_qualification_ids = {
        item.qualification_id
        for item in interventions.intervention_qualifications_required
    }
    if required_qualification_ids or any(
        item.minimum_team_size > 1 for item in interventions.interventions
    ):
        features.add('intervention_qualifications_or_team_size')
    if any(
        not _intervals_cover_horizon(
            tuple(
                (item.start_time, item.end_time)
                for item in personnel.personnel_availability
                if item.person_id == entity.person_id
            ),
            scenario_bank.day_boundaries[0][0],
            scenario_bank.day_boundaries[-1][-1],
        )
        for entity in personnel.personnel
    ):
        features.add('restricted_personnel_availability')
    if _personnel_availability_differs(personnel, interventions):
        features.add('personnel_availability_differentiation')
    if _driver_assignment_differs(fleet, personnel, interventions, settings):
        features.add('driver_assignment_differentiation')
    if settings.staff_depot_mode == 'depot_continuity':
        features.add('staff_depot_continuity')
    if settings.active_simple_rules or settings.hos_regime_id != 'none':
        features.add('work_driving_rest_hos_rules')
    if len(objectives.objectives) > 1:
        features.add('multiple_objectives')
    if len(scenario_bank.scenarios) > 1:
        features.add('scenario_aggregation')
    return VrpFeatureProfile(frozenset(features))


def _range_exceeds_tolerance(values: tuple[float, ...]) -> bool:
    minimum = min(values)
    maximum = max(values)
    return maximum - minimum > _numeric_tolerance(maximum, minimum)


def _positive_beyond_tolerance(value: float) -> bool:
    return value > _numeric_tolerance(value, 0.0)


def _effective_availability(
    person_id: str, personnel: VrpPersonnelData
) -> tuple[tuple[float, float], ...]:
    intervals = sorted(
        (item.start_time, item.end_time)
        for item in personnel.personnel_availability
        if item.person_id == person_id
    )
    merged: list[tuple[float, float]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + _numeric_tolerance(start, merged[-1][1]):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _person_assignment_profile(
    person_id: str,
    personnel: VrpPersonnelData,
    settings: VrpInstanceSettings,
    *,
    include_skills: bool,
    include_qualifications: bool,
) -> tuple[object, ...]:
    person = next(item for item in personnel.personnel if item.person_id == person_id)
    driving_skills = (
        tuple(
            sorted(
                item.skill_id
                for item in personnel.personnel_driving_skills
                if item.person_id == person_id
            )
        )
        if include_skills
        else ()
    )
    qualifications = (
        tuple(
            sorted(
                item.qualification_id
                for item in personnel.personnel_intervention_qualifications
                if item.person_id == person_id
            )
        )
        if include_qualifications
        else ()
    )
    initial_depot = (
        person.initial_depot_id
        if settings.staff_depot_mode == 'depot_continuity'
        else None
    )
    return (
        driving_skills,
        qualifications,
        _effective_availability(person_id, personnel),
        initial_depot,
    )


def _personnel_availability_differs(
    personnel: VrpPersonnelData,
    interventions: VrpInterventionData,
) -> bool:
    relevant_person_ids = {
        person.person_id for person in personnel.personnel if person.authorized_driver
    }
    person_qualifications: dict[str, set[str]] = {}
    for qualification_record in personnel.personnel_intervention_qualifications:
        person_qualifications.setdefault(qualification_record.person_id, set()).add(
            qualification_record.qualification_id
        )
    requirements_by_intervention: dict[str, set[str]] = {
        intervention.intervention_id: set()
        for intervention in interventions.interventions
    }
    for required_qualification in interventions.intervention_qualifications_required:
        requirements_by_intervention.setdefault(
            required_qualification.intervention_id, set()
        ).add(required_qualification.qualification_id)
    if interventions.interventions:
        for person in personnel.personnel:
            qualifications = person_qualifications.get(person.person_id, set())
            if any(
                requirements.issubset(qualifications)
                for requirements in requirements_by_intervention.values()
            ):
                relevant_person_ids.add(person.person_id)
    profiles = tuple(
        _effective_availability(person.person_id, personnel)
        for person in personnel.personnel
        if person.person_id in relevant_person_ids
    )
    return _has_different_profiles(profiles)


def _driver_assignment_differs(
    fleet: VrpFleetData,
    personnel: VrpPersonnelData,
    interventions: VrpInterventionData,
    settings: VrpInstanceSettings,
) -> bool:
    skill_by_person: dict[str, set[str]] = {}
    for personnel_skill in personnel.personnel_driving_skills:
        skill_by_person.setdefault(personnel_skill.person_id, set()).add(
            personnel_skill.skill_id
        )
    required_by_vehicle: dict[str, set[str]] = {}
    for vehicle_skill in fleet.vehicle_driving_skills:
        required_by_vehicle.setdefault(vehicle_skill.vehicle_id, set()).add(
            vehicle_skill.skill_id
        )
    assignment_rules_active = (
        settings.staff_depot_mode == 'depot_continuity'
        or bool(settings.active_simple_rules)
        or settings.hos_regime_id != 'none'
    )
    include_skills = bool(fleet.vehicle_driving_skills)
    include_qualifications = bool(interventions.intervention_qualifications_required)
    for vehicle in fleet.vehicles:
        required = required_by_vehicle.get(vehicle.vehicle_id, set())
        eligible = tuple(
            person.person_id
            for person in personnel.personnel
            if person.authorized_driver
            and required.issubset(skill_by_person.get(person.person_id, set()))
        )
        if len(eligible) < 2:
            continue
        profiles = tuple(
            _person_assignment_profile(
                person_id,
                personnel,
                settings,
                include_skills=include_skills,
                include_qualifications=include_qualifications,
            )
            for person_id in eligible
        )
        if assignment_rules_active or _has_different_profiles(profiles):
            return True
    return False


def _has_different_profiles(profiles: tuple[object, ...]) -> bool:
    if len(profiles) < 2:
        return False
    first = profiles[0]
    return any(_values_differ(first, profile) for profile in profiles[1:])


def _values_differ(first: object, second: object) -> bool:
    if isinstance(first, bool) or isinstance(second, bool):
        return first != second
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        left = float(first)
        right = float(second)
        return abs(left - right) > _numeric_tolerance(left, right)
    if isinstance(first, tuple) and isinstance(second, tuple):
        return len(first) != len(second) or any(
            _values_differ(left, right)
            for left, right in zip(first, second, strict=True)
        )
    return first != second
