"""Immutable normalized records for multi-day VRP input data.

Created: 2026-11-03T16:13:22+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date, datetime, time
from types import MappingProxyType
from typing import Literal, NamedTuple, cast

type TomlScalar = str | int | float | bool | date | time | datetime
type FrozenTomlValue = TomlScalar | tuple[FrozenTomlValue, ...] | FrozenTomlTable

__all__ = [
    'AggregationSpec',
    'ArcRecord',
    'CoLocationRecord',
    'ContinuousDrivingLimitRecord',
    'DailyDrivingLimitRecord',
    'DailyWorkLimitRecord',
    'DepotRecord',
    'DrivingSkillRecord',
    'EventRecord',
    'EventTypeRecord',
    'FrozenTomlTable',
    'FrozenTomlValue',
    'HosRuleDefinition',
    'InterventionRecord',
    'InterventionQualificationRecord',
    'InterventionQualificationRequiredRecord',
    'InterventionTimeWindowRecord',
    'InterventionToolRecord',
    'LoadedScenarioBank',
    'LoadedVrpInstance',
    'NodeRecord',
    'ObjectiveEventTypeRecord',
    'ObjectiveOmissionPenaltyRecord',
    'ObjectiveSpec',
    'PackageRecord',
    'PackageTimeWindowRecord',
    'PackageTransferFacilityRecord',
    'PenaltyFunctionSpec',
    'PersonRecord',
    'PersonRuleLimitRecord',
    'PersonnelAvailabilityRecord',
    'PersonnelDrivingSkillRecord',
    'PersonnelInterventionQualificationRecord',
    'PrecedenceRecord',
    'ScenarioRecord',
    'ToolRecord',
    'TransferFacilityRecord',
    'VehicleAvailabilityRecord',
    'VehicleDrivingSkillRecord',
    'VehicleOvernightDepotRecord',
    'VehiclePackageCompatibilityRecord',
    'VehicleRecord',
    'VrpActivityRelationData',
    'VrpEventData',
    'VrpFeatureId',
    'VrpFeatureProfile',
    'VrpFleetData',
    'VrpInstanceSettings',
    'VrpInterventionData',
    'VrpNetworkData',
    'VrpObjectiveData',
    'VrpPackageData',
    'VrpPersonnelData',
    'VrpWorkRuleData',
]

VrpFeatureId = Literal[
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
]


class FrozenTomlTable(Mapping[str, FrozenTomlValue]):
    """A recursively immutable mapping containing parsed TOML values."""

    __slots__ = ('_values',)
    _values: Mapping[str, FrozenTomlValue]

    def __init__(self, values: Mapping[str, object]) -> None:
        frozen: dict[str, FrozenTomlValue] = {}
        for key, value in values.items():
            if type(key) is not str:
                raise TypeError('TOML table keys must be strings')
            frozen[key] = _freeze_toml_value(value)
        object.__setattr__(self, '_values', MappingProxyType(frozen))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError('FrozenTomlTable is immutable')

    def __getitem__(self, key: str) -> FrozenTomlValue:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


def _freeze_toml_value(value: object) -> FrozenTomlValue:
    if type(value) is dict:
        return FrozenTomlTable(value)
    if type(value) is list:
        return tuple(_freeze_toml_value(item) for item in value)
    if type(value) in (str, int, float, bool, date, time, datetime):
        return cast(TomlScalar, value)
    raise TypeError(f'unsupported TOML value type: {type(value).__name__}')


class VrpFeatureProfile(NamedTuple):
    """Feature identifiers active in a validated VRP instance."""

    active_features: frozenset[VrpFeatureId]


class ScenarioRecord(NamedTuple):
    """One scenario and its normalized probability."""

    scenario_id: str
    probability: float


class HosRuleDefinition(NamedTuple):
    """One configured rule and its applicable personnel."""

    rule_id: str
    personnel_ids: tuple[str, ...]


class LoadedScenarioBank(NamedTuple):
    """Validated scenario data referenced by a VRP instance."""

    format_version: int
    arc_ids: tuple[str, ...]
    time_zone: str
    time_unit: str
    planning_dates: tuple[date, ...]
    day_boundaries: tuple[tuple[float, ...], ...]
    scenarios: tuple[ScenarioRecord, ...]
    travel_times: Mapping[tuple[str, str, int, int], float]
    post_horizon_rule_id: str
    post_horizon_rule_configuration_id: str


class NodeRecord(NamedTuple):
    node_id: str


class ArcRecord(NamedTuple):
    arc_id: str
    tail_node_id: str
    head_node_id: str


class DepotRecord(NamedTuple):
    depot_id: str
    node_id: str


class TransferFacilityRecord(NamedTuple):
    node_id: str
    storage_capacity: float


class VehicleRecord(NamedTuple):
    vehicle_id: str
    cargo_capacity: float
    seat_capacity: int
    initial_depot_id: str
    max_tours_per_day: int


class VehicleOvernightDepotRecord(NamedTuple):
    vehicle_id: str
    depot_id: str


class VehicleDrivingSkillRecord(NamedTuple):
    vehicle_id: str
    skill_id: str


class DrivingSkillRecord(NamedTuple):
    skill_id: str


class VehicleAvailabilityRecord(NamedTuple):
    vehicle_id: str
    start_time: float
    end_time: float


class PackageRecord(NamedTuple):
    package_id: str
    pickup_node_id: str
    delivery_node_id: str
    load: float
    pickup_service_duration: float
    delivery_service_duration: float
    max_transfers: int
    transfer_out_service_duration: float
    transfer_in_service_duration: float


class PackageTimeWindowRecord(NamedTuple):
    package_id: str
    activity: str
    window_index: int
    start_time: float
    end_time: float


class VehiclePackageCompatibilityRecord(NamedTuple):
    vehicle_id: str
    package_id: str
    compatible: bool


class PackageTransferFacilityRecord(NamedTuple):
    package_id: str
    node_id: str


class ToolRecord(NamedTuple):
    tool_id: str
    capacity_demand: float


class InterventionRecord(NamedTuple):
    intervention_id: str
    node_id: str
    service_duration: float
    minimum_team_size: int


class InterventionTimeWindowRecord(NamedTuple):
    intervention_id: str
    day_index: int
    window_index: int
    start_time: float
    end_time: float


class InterventionToolRecord(NamedTuple):
    intervention_id: str
    tool_id: str


class InterventionQualificationRecord(NamedTuple):
    qualification_id: str


class InterventionQualificationRequiredRecord(NamedTuple):
    intervention_id: str
    qualification_id: str


class PersonRecord(NamedTuple):
    person_id: str
    authorized_driver: bool
    initial_depot_id: str | None


class PersonnelDrivingSkillRecord(NamedTuple):
    person_id: str
    skill_id: str


class PersonnelInterventionQualificationRecord(NamedTuple):
    person_id: str
    qualification_id: str


class PersonnelAvailabilityRecord(NamedTuple):
    person_id: str
    start_time: float
    end_time: float


class PrecedenceRecord(NamedTuple):
    before_activity_kind: str
    before_activity_id: str
    after_activity_kind: str
    after_activity_id: str


class CoLocationRecord(NamedTuple):
    activity_kind_1: str
    activity_id_1: str
    activity_kind_2: str
    activity_id_2: str


class EventTypeRecord(NamedTuple):
    event_type_id: str
    location_domain: str


class EventRecord(NamedTuple):
    event_id: str
    event_type_id: str
    location_id: str


class ObjectiveEventTypeRecord(NamedTuple):
    objective_id: str
    event_type_id: str


class DailyWorkLimitRecord(NamedTuple):
    person_id: str
    day_index: int
    max_work_time: float


class DailyDrivingLimitRecord(NamedTuple):
    driver_id: str
    day_index: int
    max_driving_time: float


class PersonRuleLimitRecord(NamedTuple):
    person_id: str
    minimum_rest_time: float | None
    qualifying_break_minimum: float | None


class ContinuousDrivingLimitRecord(NamedTuple):
    driver_id: str
    max_continuous_driving_time: float


class AggregationSpec(NamedTuple):
    id: str
    q: float | None
    parameters: FrozenTomlTable


class ObjectiveSpec(NamedTuple):
    objective_id: str
    base_measure_id: str
    base_measure_parameters: FrozenTomlTable
    aggregation: AggregationSpec
    penalty_aggregation: AggregationSpec
    penalty_scales: Mapping[str, float]


class PenaltyFunctionSpec(NamedTuple):
    objective_id: str
    violation_category: str
    rule_id: str
    function_id: str
    parameters: FrozenTomlTable


class ObjectiveOmissionPenaltyRecord(NamedTuple):
    objective_id: str
    activity_kind: str
    activity_id: str
    amount: float


class VrpInstanceSettings(NamedTuple):
    load_unit: str
    service_coverage_mode: str
    staff_depot_mode: str
    rule_treatment: str
    active_simple_rules: frozenset[str]
    hos_regime_id: str
    hos_regime_version: str


class VrpNetworkData(NamedTuple):
    nodes: tuple[NodeRecord, ...]
    arcs: tuple[ArcRecord, ...]
    depots: tuple[DepotRecord, ...]
    transfer_facilities: tuple[TransferFacilityRecord, ...]


class VrpFleetData(NamedTuple):
    vehicles: tuple[VehicleRecord, ...]
    vehicle_overnight_depots: tuple[VehicleOvernightDepotRecord, ...]
    vehicle_driving_skills: tuple[VehicleDrivingSkillRecord, ...]
    vehicle_availability: tuple[VehicleAvailabilityRecord, ...]


class VrpPackageData(NamedTuple):
    packages: tuple[PackageRecord, ...]
    package_time_windows: tuple[PackageTimeWindowRecord, ...]
    vehicle_package_compatibility: tuple[VehiclePackageCompatibilityRecord, ...]
    package_transfer_facilities: tuple[PackageTransferFacilityRecord, ...]


class VrpInterventionData(NamedTuple):
    interventions: tuple[InterventionRecord, ...]
    intervention_time_windows: tuple[InterventionTimeWindowRecord, ...]
    tools: tuple[ToolRecord, ...]
    intervention_tools: tuple[InterventionToolRecord, ...]
    intervention_qualifications_required: tuple[
        InterventionQualificationRequiredRecord, ...
    ]


class VrpPersonnelData(NamedTuple):
    personnel: tuple[PersonRecord, ...]
    driving_skills: tuple[DrivingSkillRecord, ...]
    intervention_qualifications: tuple[InterventionQualificationRecord, ...]
    personnel_driving_skills: tuple[PersonnelDrivingSkillRecord, ...]
    personnel_intervention_qualifications: tuple[
        PersonnelInterventionQualificationRecord, ...
    ]
    personnel_availability: tuple[PersonnelAvailabilityRecord, ...]


class VrpActivityRelationData(NamedTuple):
    precedence: tuple[PrecedenceRecord, ...]
    co_location: tuple[CoLocationRecord, ...]


class VrpWorkRuleData(NamedTuple):
    daily_work_limits: tuple[DailyWorkLimitRecord, ...]
    daily_driving_limits: tuple[DailyDrivingLimitRecord, ...]
    person_rule_limits: tuple[PersonRuleLimitRecord, ...]
    continuous_driving_limits: tuple[ContinuousDrivingLimitRecord, ...]


class VrpEventData(NamedTuple):
    event_types: tuple[EventTypeRecord, ...]
    events: tuple[EventRecord, ...]
    objective_event_types: tuple[ObjectiveEventTypeRecord, ...]


class VrpObjectiveData(NamedTuple):
    objectives: tuple[ObjectiveSpec, ...]
    penalty_functions: tuple[PenaltyFunctionSpec, ...]
    objective_omission_penalties: tuple[ObjectiveOmissionPenaltyRecord, ...]


class LoadedVrpInstance(NamedTuple):
    format_version: int
    instance_id: str
    scenario_bank: LoadedScenarioBank
    settings: VrpInstanceSettings
    network_data: VrpNetworkData
    fleet_data: VrpFleetData
    package_data: VrpPackageData
    intervention_data: VrpInterventionData
    personnel_data: VrpPersonnelData
    activity_relations: VrpActivityRelationData
    work_rule_data: VrpWorkRuleData
    event_data: VrpEventData
    objective_data: VrpObjectiveData
    application_data: FrozenTomlTable | None
    hos_configuration: FrozenTomlTable | None
    hos_rule_definitions: tuple[HosRuleDefinition, ...]
    feature_profile: VrpFeatureProfile
