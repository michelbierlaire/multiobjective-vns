"""Build feature-aware Operator registrations for multi-day VRP instances.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from multiobjective_vns.callbacks import Operator
from multiobjective_vns.identifiers import (
    ObjectiveId,
    ProblemInstanceId,
    identifier_text,
)
from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    VrpFeatureId,
)
from multiobjective_vns.multiday_vrp.operators.activity_relocation import (
    ActivityRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators.aggregation_contribution import (
    AggregationContributionOperator,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.depot_visit import (
    DepotVisitMode,
    DepotVisitOperator,
)
from multiobjective_vns.multiday_vrp.operators.driver_reassignment import (
    DriverReassignmentOperator,
)
from multiobjective_vns.multiday_vrp.operators.driver_schedule_repair import (
    DriverScheduleDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.feature_path import (
    EventObjectiveRouteRerouteOperator,
    ScenarioDifferentialRerouteOperator,
    TimeWindowGuidedRouteRerouteOperator,
)
from multiobjective_vns.multiday_vrp.operators.feature_timing import (
    FeatureTimingMode,
    FeatureTimingOperator,
)
from multiobjective_vns.multiday_vrp.operators.package_capacity import (
    CapacityAwarePackageChainRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators.package_chain import (
    PackageChainRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators.pareto_tradeoff import (
    ParetoTradeoffOperator,
)
from multiobjective_vns.multiday_vrp.operators.personnel_features import (
    InterventionTeamReassignmentOperator,
)
from multiobjective_vns.multiday_vrp.operators.personnel_repair import (
    PersonnelRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.relationship_repair import (
    RelationshipGuidedServiceRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.route_order import (
    ExchangeOperator,
    OrOptChainRelocationOperator,
    RelocateOperator,
    SwapStarOperator,
    TwoOptOperator,
    TwoOptStarOperator,
)
from multiobjective_vns.multiday_vrp.operators.route_path import (
    AdditivePathCostRegistration,
    ObjectiveSpecificShortestPathOperator,
    RouteLegRerouteOperator,
)
from multiobjective_vns.multiday_vrp.operators.schedule_features import (
    DriverSwapCycleOperator,
    MultiDayDepotReassignmentOperator,
    OvernightDepotChangeOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_coverage import (
    ServiceCoverageChangeOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_regret import (
    TimeWindowRegretBasedReinsertionOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_repair import (
    TimeWindowFocusedDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.timing import TimingAdjustmentOperator
from multiobjective_vns.multiday_vrp.operators.tool_capacity import (
    ToolReservationGuidedInterventionRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators.tour_boundaries import (
    MergeToursOperator,
    SplitTourOperator,
)
from multiobjective_vns.multiday_vrp.operators.transfer_facility import (
    TransferFacilityAssignmentOperator,
)
from multiobjective_vns.multiday_vrp.operators.transfer_step import (
    TransferStepMode,
    TransferStepOperator,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_elimination import (
    VehicleEliminationDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_consolidation import (
    VehicleRouteConsolidationOperator,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_split import (
    VehicleRouteSplitOperator,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.registrations import OperatorRegistration
from multiobjective_vns.values import FiniteReal, finite_real, require_integer

VrpOperatorSpecId: TypeAlias = str
_TargetMode: TypeAlias = Literal['independent', 'objective', 'both']

__all__ = [
    'VrpOperatorCatalogSettings',
    'VrpOperatorId',
    'VrpOperatorSpecId',
    'build_default_vrp_operator_catalog',
]


@dataclass(frozen=True, slots=True)
class VrpOperatorId:
    """Stable identity for one VRP Operator specification and target.

    :param operator_spec_id: Package-owned stable specification string.
    :param objective_id: Objective target, or ``None`` for an unranked choice.
    """

    operator_spec_id: VrpOperatorSpecId
    objective_id: ObjectiveId | None

    def __post_init__(self) -> None:
        if (
            type(self.operator_spec_id) is not str
            or not self.operator_spec_id
            or self.operator_spec_id.strip() != self.operator_spec_id
        ):
            raise ValueError('operator_spec_id must be a nonempty unpadded string')
        if self.objective_id is not None:
            identifier_text(self.objective_id)

    def to_log_text(self) -> str:
        """Return a tagged, boundary-safe textual identifier."""
        objective_text = (
            None if self.objective_id is None else identifier_text(self.objective_id)
        )
        return 'vrp-operator:' + json.dumps(
            [self.operator_spec_id, objective_text],
            ensure_ascii=True,
            separators=(',', ':'),
        )


@dataclass(frozen=True, slots=True)
class VrpOperatorCatalogSettings:
    """Immutable limits and objective targets for the default Operator catalog.

    :param max_neighborhood_size: Maximum supported size registered per Operator.
    :param time_step: Positive step used by timing Operators.
    :param objective_targets: Unique selected objective IDs, or ``None`` for all.
    :param max_candidate_alternatives: Complete alternatives assessed per move.
    :param max_paths_per_leg: Maximum sampled paths for one leg endpoint pair.
    :param max_arc_expansion_steps: Search bound for a sampled path.
    """

    max_neighborhood_size: int = 5
    time_step: FiniteReal = 1.0
    objective_targets: tuple[ObjectiveId, ...] | None = None
    max_candidate_alternatives: int = 32
    max_paths_per_leg: int = 32
    max_arc_expansion_steps: int = 10000

    def __post_init__(self) -> None:
        require_integer(
            self.max_neighborhood_size,
            name='max_neighborhood_size',
            minimum=1,
        )
        step = finite_real(self.time_step, name='time_step')
        if step <= 0.0:
            raise ValueError('time_step must be positive')
        object.__setattr__(self, 'time_step', step)
        for name, value, maximum in (
            ('max_candidate_alternatives', self.max_candidate_alternatives, 32),
            ('max_paths_per_leg', self.max_paths_per_leg, 32),
            ('max_arc_expansion_steps', self.max_arc_expansion_steps, 10000),
        ):
            require_integer(value, name=name, minimum=1)
            if value > maximum:
                raise ValueError(f'{name} must be at most {maximum}')
        if self.objective_targets is not None:
            if type(self.objective_targets) is not tuple:
                raise TypeError('objective_targets must be a tuple or None')
            texts = tuple(identifier_text(value) for value in self.objective_targets)
            if len(set(texts)) != len(texts):
                raise ValueError('objective_targets must be unique')


@dataclass(frozen=True, slots=True)
class _Specification:
    spec_id: str
    target_mode: _TargetMode
    required_features: tuple[VrpFeatureId, ...]
    any_features: tuple[VrpFeatureId, ...]
    restoration: bool
    modification_count_definition: str
    uses_paths: bool = False
    uses_time_step: bool = False


_SPECS: tuple[_Specification, ...] = (
    _Specification(
        'vrp.generic.two_opt',
        'independent',
        (),
        (),
        False,
        'number of route 2-opt moves applied',
    ),
    _Specification(
        'vrp.generic.relocate',
        'independent',
        (),
        (),
        False,
        'number of stops relocated',
    ),
    _Specification(
        'vrp.generic.or_opt_chain_relocation',
        'independent',
        (),
        (),
        False,
        'number of stops relocated in chains',
    ),
    _Specification(
        'vrp.generic.exchange',
        'independent',
        (),
        (),
        False,
        'number of stop exchanges applied',
    ),
    _Specification(
        'vrp.generic.two_opt_star_suffix_exchange',
        'independent',
        (),
        (),
        False,
        'number of route suffix exchanges',
    ),
    _Specification(
        'vrp.generic.swap_star_free_insertion_exchange',
        'independent',
        (),
        (),
        False,
        'number of free-insertion exchanges',
    ),
    _Specification(
        'vrp.generic.vehicle_route_consolidation',
        'both',
        (),
        (),
        False,
        'number of vehicle-day route records removed',
        True,
    ),
    _Specification(
        'vrp.generic.vehicle_elimination_destroy_and_repair',
        'both',
        (),
        (),
        False,
        'number of vehicle IDs eliminated',
        True,
    ),
    _Specification(
        'vrp.generic.vehicle_route_split',
        'both',
        (),
        (),
        False,
        'number of vehicle-day route records created',
        True,
    ),
    _Specification(
        'vrp.generic.route_leg_reroute',
        'both',
        (),
        (),
        True,
        'number of route legs whose paths changed',
        True,
    ),
    _Specification(
        'vrp.generic.objective_specific_shortest_path',
        'objective',
        (),
        (),
        True,
        'number of route legs whose paths changed',
        True,
    ),
    _Specification(
        'vrp.generic.timing_adjustment',
        'independent',
        (),
        (),
        True,
        'number of timing fields changed',
        uses_time_step=True,
    ),
    _Specification(
        'vrp.generic.driver_reassignment',
        'independent',
        ('driver_assignment_differentiation',),
        (),
        True,
        'number of route-driver assignments changed',
    ),
    _Specification(
        'vrp.time_dependent.departure_time_profile_adjustment',
        'objective',
        ('within_day_time_dependent_travel',),
        (),
        True,
        'number of timing decisions changed',
        uses_time_step=True,
    ),
    _Specification(
        'vrp.scenario_uncertainty.differential_reroute',
        'objective',
        ('scenario_uncertainty',),
        (),
        True,
        'number of route legs whose paths changed',
        True,
    ),
    _Specification(
        'vrp.scenario_uncertainty.differential_timing_adjustment',
        'objective',
        ('scenario_uncertainty',),
        (),
        True,
        'number of timing decisions changed',
        uses_time_step=True,
    ),
    _Specification(
        'vrp.network_events.event_objective_route_reroute',
        'objective',
        ('network_event_exposure',),
        (),
        False,
        'number of route legs whose paths changed',
        True,
    ),
    _Specification(
        'vrp.multi_day.driver_swap_or_cycle',
        'independent',
        ('multi_day_planning',),
        (),
        True,
        'number of route-driver assignments changed',
    ),
    _Specification(
        'vrp.multi_day.driver_schedule_destroy_and_repair',
        'both',
        ('multi_day_planning',),
        (),
        True,
        'number of selected routes assigned a different driver',
    ),
    _Specification(
        'vrp.overnight_depot.overnight_depot_change',
        'both',
        ('overnight_depot_choice',),
        (),
        True,
        'number of routes assigned a different overnight depot',
        True,
    ),
    _Specification(
        'vrp.multi_day.multi_day_depot_reassignment',
        'both',
        ('multi_day_planning', 'overnight_depot_choice'),
        (),
        True,
        'number of reconsidered routes assigned a different overnight depot',
        True,
    ),
    _Specification(
        'vrp.multiple_tours.split_tour',
        'both',
        ('multiple_tours_per_day',),
        (),
        True,
        'number of tour boundaries added',
        True,
    ),
    _Specification(
        'vrp.multiple_tours.merge_tours',
        'both',
        ('multiple_tours_per_day',),
        (),
        True,
        'number of tour boundaries removed',
    ),
    _Specification(
        'vrp.depot_visits.depot_visit_insertion',
        'both',
        (),
        (
            'multiple_tours_per_day',
            'tool_capacity_reservations',
            'work_driving_rest_hos_rules',
        ),
        True,
        'number of depot-only stops inserted',
        True,
    ),
    _Specification(
        'vrp.depot_visits.depot_visit_removal',
        'both',
        (),
        (
            'multiple_tours_per_day',
            'tool_capacity_reservations',
            'work_driving_rest_hos_rules',
        ),
        True,
        'number of depot-only stops removed',
        True,
    ),
    _Specification(
        'vrp.vehicle_availability.guided_timing_adjustment',
        'objective',
        ('restricted_vehicle_availability',),
        (),
        False,
        'number of timing decisions changed',
        uses_time_step=True,
    ),
    _Specification(
        'vrp.vehicle_availability.guided_stop_relocation',
        'both',
        ('restricted_vehicle_availability',),
        (),
        False,
        'number of completed stop relocations',
        True,
    ),
    _Specification(
        'vrp.personnel_seat_capacity.seat_aware_intervention_team_reassignment',
        'both',
        ('potentially_binding_personnel_seat_capacity',),
        (),
        True,
        'number of intervention stops assigned a different team',
    ),
    _Specification(
        'vrp.package_compatibility.compatibility_guided_package_chain_repair',
        'both',
        ('package_vehicle_compatibility',),
        (),
        True,
        'number of package chains whose carrier sequence or positions changed',
        True,
    ),
    _Specification(
        'vrp.transfer_storage.storage_aware_transfer_assignment',
        'both',
        ('transfer_facility_storage', 'package_transfer_operations'),
        (),
        True,
        'number of transfer-step facility choices changed',
        True,
    ),
    _Specification(
        'vrp.package_requests.package_chain_destroy_and_repair',
        'both',
        ('package_pickup_delivery',),
        (),
        True,
        'number of package chains whose service or route decisions changed',
        True,
    ),
    _Specification(
        'vrp.positive_service_duration.service_order_impact_repair',
        'objective',
        ('positive_service_durations',),
        (),
        True,
        'number of activity occurrences relocated',
        True,
    ),
    _Specification(
        'vrp.positive_service_duration.duration_impact_timing_adjustment',
        'objective',
        ('positive_service_durations',),
        (),
        True,
        'number of timing decisions changed',
        uses_time_step=True,
    ),
    _Specification(
        'vrp.optional_service_omission.service_coverage_change',
        'independent',
        ('optional_service_omission',),
        (),
        True,
        'number of activity coverage states changed',
        True,
    ),
    _Specification(
        'vrp.service_windows.service_wait_adjustment',
        'objective',
        ('restrictive_service_windows',),
        (),
        False,
        'number of pre-service-wait fields changed',
        uses_time_step=True,
    ),
    _Specification(
        'vrp.service_windows.route_start_shift',
        'objective',
        ('restrictive_service_windows',),
        (),
        False,
        'number of route start times changed',
        uses_time_step=True,
    ),
    _Specification(
        'vrp.service_windows.activity_relocation',
        'objective',
        ('restrictive_service_windows',),
        (),
        False,
        'number of activity occurrences relocated',
        True,
    ),
    _Specification(
        'vrp.service_windows.route_leg_rerouting',
        'objective',
        ('restrictive_service_windows',),
        (),
        False,
        'number of route legs whose paths changed',
        True,
    ),
    _Specification(
        'vrp.service_windows.destroy_and_repair',
        'objective',
        ('restrictive_service_windows',),
        (),
        False,
        'number of activity occurrences changed',
        True,
    ),
    _Specification(
        'vrp.service_windows.regret_based_reinsertion',
        'objective',
        ('restrictive_service_windows',),
        (),
        False,
        'number of time-windowed service units relocated',
        True,
    ),
    _Specification(
        'vrp.interventions.intervention_relocation',
        'both',
        ('interventions',),
        (),
        True,
        'number of intervention activity occurrences relocated',
        True,
    ),
    _Specification(
        'vrp.activity_relationships.relationship_guided_service_repair',
        'both',
        ('additional_activity_relationships',),
        (),
        True,
        'number of relationship pairs repaired',
        True,
    ),
    _Specification(
        'vrp.intervention_qualifications.intervention_team_reassignment',
        'both',
        ('intervention_qualifications_or_team_size',),
        (),
        True,
        'number of intervention stops assigned a different team',
    ),
    _Specification(
        'vrp.driving_skills.skill_guided_driver_schedule_repair',
        'both',
        ('driving_skill_restrictions',),
        (),
        True,
        'number of route-driver assignments changed',
    ),
    _Specification(
        'vrp.personnel_availability.personnel_calendar_timing_adjustment',
        'objective',
        ('restricted_personnel_availability',),
        (),
        False,
        'number of timing decisions changed',
        uses_time_step=True,
    ),
    _Specification(
        'vrp.personnel_availability.personnel_assignment_repair',
        'objective',
        (
            'restricted_personnel_availability',
            'personnel_availability_differentiation',
        ),
        (),
        True,
        'number of person-duty assignments changed',
    ),
    _Specification(
        'vrp.staff_depot_continuity.staff_continuity_schedule_repair',
        'both',
        ('staff_depot_continuity',),
        (),
        True,
        'number of personnel assignments changed',
    ),
    _Specification(
        'vrp.work_rules.violation_guided_assignment_repair',
        'objective',
        ('work_driving_rest_hos_rules',),
        (),
        True,
        'number of person-duty assignments changed',
    ),
    _Specification(
        'vrp.work_rules.violation_guided_timing_repair',
        'objective',
        ('work_driving_rest_hos_rules',),
        (),
        True,
        'number of timing fields changed',
        uses_time_step=True,
    ),
    _Specification(
        'vrp.multiple_objectives.objective_targeted_activity_relocation',
        'objective',
        ('multiple_objectives',),
        (),
        False,
        'number of activity occurrences moved',
        True,
    ),
    _Specification(
        'vrp.multiple_objectives.pareto_tradeoff_path',
        'independent',
        ('multiple_objectives',),
        (),
        False,
        'number of route-decision units applied',
        True,
    ),
    _Specification(
        'vrp.multiple_objectives.pareto_tradeoff_timing',
        'independent',
        ('multiple_objectives',),
        (),
        False,
        'number of route-decision units applied',
        uses_time_step=True,
    ),
    _Specification(
        'vrp.multiple_objectives.pareto_tradeoff_activity',
        'independent',
        ('multiple_objectives',),
        (),
        False,
        'number of route-decision units applied',
        True,
    ),
    _Specification(
        'vrp.scenario_aggregation.aggregation_contribution_path_repair',
        'objective',
        ('scenario_aggregation',),
        (),
        False,
        'number of changed route legs',
        True,
    ),
    _Specification(
        'vrp.scenario_aggregation.aggregation_contribution_timing_repair',
        'objective',
        ('scenario_aggregation',),
        (),
        False,
        'number of changed timing fields',
        uses_time_step=True,
    ),
    _Specification(
        'vrp.cargo_tool_capacity.capacity_aware_package_chain_relocation',
        'both',
        ('potentially_binding_cargo_tool_capacity',),
        (),
        True,
        'number of package chains relocated',
        True,
    ),
    _Specification(
        'vrp.package_transfer.add_transfer_step',
        'both',
        ('package_transfer_operations',),
        (),
        True,
        'number of transfer steps added',
        True,
    ),
    _Specification(
        'vrp.package_transfer.remove_transfer_step',
        'both',
        ('package_transfer_operations',),
        (),
        True,
        'number of transfer steps removed',
        True,
    ),
    _Specification(
        'vrp.package_transfer.reassign_transfer_step',
        'both',
        ('package_transfer_operations',),
        (),
        True,
        'number of transfer steps changed',
        True,
    ),
    _Specification(
        'vrp.tool_capacity.tool_reservation_guided_intervention_relocation',
        'both',
        ('tool_capacity_reservations',),
        (),
        True,
        'number of interventions moved to a different segment',
        True,
    ),
)


def build_default_vrp_operator_catalog(
    instance: LoadedVrpInstance,
    *,
    problem_instance_id: ProblemInstanceId,
    objective_declaration: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    additive_path_costs: Mapping[ObjectiveId, AdditivePathCostRegistration]
    | None = None,
    settings: VrpOperatorCatalogSettings = VrpOperatorCatalogSettings(),
) -> tuple[OperatorRegistration, ...]:
    """Build deterministic registrations for the implemented default VRP catalog.

    :param instance: Validated, normalized VRP application data.
    :param problem_instance_id: Stable identity for captured instance data.
    :param objective_declaration: Ordered objective comparison definitions.
    :param assess_plan: Shared complete-plan assessment callback.
    :param additive_path_costs: Optional objective-specific additive path callbacks.
    :param settings: Catalog limits and selected objective targets.
    :returns: Ordered stateless Operator registrations.
    :raises TypeError: If inputs or setting records have unsupported types.
    :raises ValueError: If IDs, targets, registrations, or active catalog coverage
        are inconsistent.
    """
    if not isinstance(instance, LoadedVrpInstance):
        raise TypeError('instance must be a LoadedVrpInstance')
    if not isinstance(objective_declaration, ObjectiveDeclaration):
        raise TypeError('objective_declaration must be an ObjectiveDeclaration')
    if not callable(assess_plan):
        raise TypeError('assess_plan must be callable')
    if not isinstance(settings, VrpOperatorCatalogSettings):
        raise TypeError('settings must be VrpOperatorCatalogSettings')
    instance_text = identifier_text(problem_instance_id)
    objective_by_text = {
        identifier_text(objective.objective_id): objective.objective_id
        for objective in objective_declaration.objectives
    }
    if settings.objective_targets is None:
        selected_objectives = tuple(
            objective.objective_id for objective in objective_declaration.objectives
        )
    else:
        selected_objectives = settings.objective_targets
        unknown = tuple(
            identifier_text(value)
            for value in selected_objectives
            if identifier_text(value) not in objective_by_text
        )
        if unknown:
            raise ValueError(f'objective targets are not declared: {unknown!r}')
    callbacks = _validated_path_costs(additive_path_costs, objective_by_text)
    active_features = instance.feature_profile.active_features
    registrations: list[OperatorRegistration] = []
    for spec in _SPECS:
        if not _spec_is_active(spec, active_features):
            continue
        applicable_objectives = _applicable_objectives(
            spec,
            instance,
            selected_objectives,
            objective_declaration,
        )
        targets = _target_values(spec, applicable_objectives)
        for objective_id in targets:
            operator = _create_operator(
                spec.spec_id,
                instance,
                objective_declaration,
                assess_plan,
                settings,
                objective_id,
                callbacks,
            )
            registrations.append(
                _make_registration(
                    spec,
                    operator,
                    objective_id,
                    instance_text,
                    settings,
                    callbacks,
                )
            )
    texts = tuple(identifier_text(item.operator_id) for item in registrations)
    if len(texts) != len(set(texts)):
        raise ValueError('generated Operator IDs and text forms must be unique')
    return tuple(registrations)


def _validated_path_costs(
    supplied: Mapping[ObjectiveId, AdditivePathCostRegistration] | None,
    objectives: Mapping[str, ObjectiveId],
) -> dict[ObjectiveId, AdditivePathCostRegistration]:
    if supplied is None:
        return {}
    if not isinstance(supplied, Mapping):
        raise TypeError('additive_path_costs must be a mapping or None')
    validated: dict[ObjectiveId, AdditivePathCostRegistration] = {}
    for objective_id, registration in supplied.items():
        text = identifier_text(objective_id)
        if text not in objectives or objectives[text] != objective_id:
            raise ValueError(f'additive path objective {text!r} is not declared')
        if not isinstance(registration, AdditivePathCostRegistration):
            raise TypeError(
                'additive_path_costs values must be AdditivePathCostRegistration'
            )
        validated[objective_id] = registration
    return validated


def _spec_is_active(
    spec: _Specification, active_features: frozenset[VrpFeatureId]
) -> bool:
    return all(feature in active_features for feature in spec.required_features) and (
        not spec.any_features
        or any(feature in active_features for feature in spec.any_features)
    )


def _target_values(
    spec: _Specification, objectives: tuple[ObjectiveId, ...]
) -> tuple[ObjectiveId | None, ...]:
    if spec.target_mode == 'independent':
        return (None,)
    if spec.target_mode == 'objective':
        return objectives
    return (None, *objectives)


def _applicable_objectives(
    spec: _Specification,
    instance: LoadedVrpInstance,
    selected: tuple[ObjectiveId, ...],
    declaration: ObjectiveDeclaration,
) -> tuple[ObjectiveId, ...]:
    if spec.spec_id != 'vrp.network_events.event_objective_route_reroute':
        return selected
    allowed = {
        item.objective_id
        for item in instance.objective_data.objectives
        if item.base_measure_id in {'event_count', 'maximum_event_exposure'}
        and any(
            association.objective_id == item.objective_id
            for association in instance.event_data.objective_event_types
        )
    }
    declared = {
        identifier_text(item.objective_id): item.objective_id
        for item in declaration.objectives
    }
    return tuple(
        objective_id
        for objective_id in selected
        if identifier_text(objective_id) in allowed
        and identifier_text(objective_id) in declared
    )


def _create_operator(
    spec_id: str,
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    settings: VrpOperatorCatalogSettings,
    objective_id: ObjectiveId | None,
    path_costs: Mapping[ObjectiveId, AdditivePathCostRegistration],
) -> Operator:
    common = {
        'max_neighborhood_size': settings.max_neighborhood_size,
        'max_candidate_alternatives': settings.max_candidate_alternatives,
    }
    path = {
        **common,
        'max_paths_per_leg': settings.max_paths_per_leg,
        'max_arc_expansion_steps': settings.max_arc_expansion_steps,
    }
    if spec_id == 'vrp.generic.two_opt':
        return TwoOptOperator(instance, objectives, assess_plan, **common)
    if spec_id == 'vrp.generic.relocate':
        return RelocateOperator(instance, objectives, assess_plan, **common)
    if spec_id == 'vrp.generic.or_opt_chain_relocation':
        return OrOptChainRelocationOperator(instance, objectives, assess_plan, **common)
    if spec_id == 'vrp.generic.exchange':
        return ExchangeOperator(instance, objectives, assess_plan, **common)
    if spec_id == 'vrp.generic.two_opt_star_suffix_exchange':
        return TwoOptStarOperator(instance, objectives, assess_plan, **common)
    if spec_id == 'vrp.generic.swap_star_free_insertion_exchange':
        return SwapStarOperator(instance, objectives, assess_plan, **common)
    if spec_id == 'vrp.generic.vehicle_route_consolidation':
        return VehicleRouteConsolidationOperator(
            instance,
            objectives,
            assess_plan,
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.generic.vehicle_elimination_destroy_and_repair':
        return VehicleEliminationDestroyRepairOperator(
            instance,
            objectives,
            assess_plan,
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.generic.vehicle_route_split':
        return VehicleRouteSplitOperator(
            instance,
            objectives,
            assess_plan,
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.generic.route_leg_reroute':
        return RouteLegRerouteOperator(
            instance, objectives, assess_plan, objective_id=objective_id, **path
        )
    if spec_id == 'vrp.generic.objective_specific_shortest_path':
        assert objective_id is not None
        callback = path_costs.get(objective_id)
        return ObjectiveSpecificShortestPathOperator(
            instance,
            objectives,
            assess_plan,
            objective_id=objective_id,
            additive_path_cost=None if callback is None else callback.callback,
            **path,
        )
    if spec_id == 'vrp.generic.timing_adjustment':
        return TimingAdjustmentOperator(
            instance,
            objectives,
            assess_plan,
            time_step=settings.time_step,
            max_neighborhood_size=settings.max_neighborhood_size,
        )
    if spec_id == 'vrp.generic.driver_reassignment':
        return DriverReassignmentOperator(instance, objectives, assess_plan, **common)
    if spec_id == 'vrp.time_dependent.departure_time_profile_adjustment':
        return _feature_timing(
            'departure_profile',
            instance,
            objectives,
            assess_plan,
            objective_id,
            settings,
        )
    if spec_id == 'vrp.scenario_uncertainty.differential_reroute':
        assert objective_id is not None
        return ScenarioDifferentialRerouteOperator(
            instance, objectives, assess_plan, objective_id=objective_id, **path
        )
    if spec_id == 'vrp.scenario_uncertainty.differential_timing_adjustment':
        return _feature_timing(
            'scenario_differential',
            instance,
            objectives,
            assess_plan,
            objective_id,
            settings,
        )
    if spec_id == 'vrp.network_events.event_objective_route_reroute':
        assert objective_id is not None
        return EventObjectiveRouteRerouteOperator(
            instance, objectives, assess_plan, objective_id=objective_id, **path
        )
    if spec_id == 'vrp.multi_day.driver_swap_or_cycle':
        return DriverSwapCycleOperator(
            instance,
            objectives,
            assess_plan,
            max_neighborhood_size=settings.max_neighborhood_size,
        )
    if spec_id == 'vrp.multi_day.driver_schedule_destroy_and_repair':
        return DriverScheduleDestroyRepairOperator(
            instance, objectives, assess_plan, objective_id=objective_id, **common
        )
    if spec_id == 'vrp.overnight_depot.overnight_depot_change':
        return OvernightDepotChangeOperator(
            instance, objectives, assess_plan, objective_id=objective_id, **path
        )
    if spec_id == 'vrp.multi_day.multi_day_depot_reassignment':
        return MultiDayDepotReassignmentOperator(
            instance, objectives, assess_plan, objective_id=objective_id, **path
        )
    if spec_id == 'vrp.multiple_tours.split_tour':
        return SplitTourOperator(
            instance,
            objectives,
            assess_plan,
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.multiple_tours.merge_tours':
        return MergeToursOperator(
            instance,
            objectives,
            assess_plan,
            objective_id=objective_id,
            **common,
        )
    if spec_id in {
        'vrp.depot_visits.depot_visit_insertion',
        'vrp.depot_visits.depot_visit_removal',
    }:
        mode: DepotVisitMode = 'insert' if spec_id.endswith('insertion') else 'remove'
        return DepotVisitOperator(
            instance,
            objectives,
            assess_plan,
            mode=mode,
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.vehicle_availability.guided_timing_adjustment':
        return _feature_timing(
            'vehicle_availability',
            instance,
            objectives,
            assess_plan,
            objective_id,
            settings,
        )
    if spec_id == 'vrp.vehicle_availability.guided_stop_relocation':
        return ActivityRelocationOperator(
            instance,
            objectives,
            assess_plan,
            mode='vehicle_availability',
            objective_id=objective_id,
            **path,
        )
    if (
        spec_id
        == 'vrp.personnel_seat_capacity.seat_aware_intervention_team_reassignment'
    ):
        return InterventionTeamReassignmentOperator(
            instance,
            objectives,
            assess_plan,
            mode='seat_aware',
            objective_id=objective_id,
            **common,
        )
    if spec_id == 'vrp.package_compatibility.compatibility_guided_package_chain_repair':
        return PackageChainRelocationOperator(
            instance,
            objectives,
            assess_plan,
            mode='compatibility',
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.transfer_storage.storage_aware_transfer_assignment':
        return TransferFacilityAssignmentOperator(
            instance, objectives, assess_plan, objective_id=objective_id, **path
        )
    if spec_id == 'vrp.cargo_tool_capacity.capacity_aware_package_chain_relocation':
        return CapacityAwarePackageChainRelocationOperator(
            instance, objectives, assess_plan, objective_id=objective_id, **path
        )
    if spec_id in {
        'vrp.package_transfer.add_transfer_step',
        'vrp.package_transfer.remove_transfer_step',
        'vrp.package_transfer.reassign_transfer_step',
    }:
        transfer_modes: dict[str, TransferStepMode] = {
            'vrp.package_transfer.add_transfer_step': 'add',
            'vrp.package_transfer.remove_transfer_step': 'remove',
            'vrp.package_transfer.reassign_transfer_step': 'reassign',
        }
        return TransferStepOperator(
            instance,
            objectives,
            assess_plan,
            mode=transfer_modes[spec_id],
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.tool_capacity.tool_reservation_guided_intervention_relocation':
        return ToolReservationGuidedInterventionRelocationOperator(
            instance, objectives, assess_plan, objective_id=objective_id, **path
        )
    if spec_id == 'vrp.package_requests.package_chain_destroy_and_repair':
        return PackageChainRelocationOperator(
            instance,
            objectives,
            assess_plan,
            mode='requests',
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.positive_service_duration.service_order_impact_repair':
        assert objective_id is not None
        return ActivityRelocationOperator(
            instance,
            objectives,
            assess_plan,
            mode='service_order_impact',
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.positive_service_duration.duration_impact_timing_adjustment':
        return _feature_timing(
            'positive_service_duration',
            instance,
            objectives,
            assess_plan,
            objective_id,
            settings,
        )
    if spec_id == 'vrp.optional_service_omission.service_coverage_change':
        return ServiceCoverageChangeOperator(instance, objectives, assess_plan, **path)
    if spec_id == 'vrp.service_windows.service_wait_adjustment':
        return _feature_timing(
            'service_window_wait',
            instance,
            objectives,
            assess_plan,
            objective_id,
            settings,
        )
    if spec_id == 'vrp.service_windows.route_start_shift':
        return _feature_timing(
            'route_start_window',
            instance,
            objectives,
            assess_plan,
            objective_id,
            settings,
        )
    if spec_id == 'vrp.service_windows.activity_relocation':
        assert objective_id is not None
        return ActivityRelocationOperator(
            instance,
            objectives,
            assess_plan,
            mode='window_guided',
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.service_windows.route_leg_rerouting':
        assert objective_id is not None
        return TimeWindowGuidedRouteRerouteOperator(
            instance, objectives, assess_plan, objective_id=objective_id, **path
        )
    if spec_id == 'vrp.service_windows.destroy_and_repair':
        assert objective_id is not None
        return TimeWindowFocusedDestroyRepairOperator(
            instance, objectives, assess_plan, objective_id=objective_id, **path
        )
    if spec_id == 'vrp.service_windows.regret_based_reinsertion':
        assert objective_id is not None
        return TimeWindowRegretBasedReinsertionOperator(
            instance,
            objectives,
            assess_plan,
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.interventions.intervention_relocation':
        return ActivityRelocationOperator(
            instance,
            objectives,
            assess_plan,
            mode='intervention',
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.activity_relationships.relationship_guided_service_repair':
        return RelationshipGuidedServiceRepairOperator(
            instance,
            objectives,
            assess_plan,
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.driving_skills.skill_guided_driver_schedule_repair':
        return PersonnelRepairOperator(
            instance,
            objectives,
            assess_plan,
            mode='skill_guided',
            objective_id=objective_id,
            **common,
        )
    if spec_id == 'vrp.personnel_availability.personnel_assignment_repair':
        assert objective_id is not None
        return PersonnelRepairOperator(
            instance,
            objectives,
            assess_plan,
            mode='availability',
            objective_id=objective_id,
            **common,
        )
    if spec_id == 'vrp.staff_depot_continuity.staff_continuity_schedule_repair':
        return PersonnelRepairOperator(
            instance,
            objectives,
            assess_plan,
            mode='staff_continuity',
            objective_id=objective_id,
            **common,
        )
    if spec_id == 'vrp.work_rules.violation_guided_assignment_repair':
        assert objective_id is not None
        return PersonnelRepairOperator(
            instance,
            objectives,
            assess_plan,
            mode='work_rule',
            objective_id=objective_id,
            **common,
        )
    if spec_id == 'vrp.intervention_qualifications.intervention_team_reassignment':
        return InterventionTeamReassignmentOperator(
            instance,
            objectives,
            assess_plan,
            mode='general',
            objective_id=objective_id,
            **common,
        )
    if spec_id == 'vrp.personnel_availability.personnel_calendar_timing_adjustment':
        return _feature_timing(
            'personnel_calendar',
            instance,
            objectives,
            assess_plan,
            objective_id,
            settings,
        )
    if spec_id == 'vrp.work_rules.violation_guided_timing_repair':
        return _feature_timing(
            'work_rule', instance, objectives, assess_plan, objective_id, settings
        )
    if spec_id == 'vrp.multiple_objectives.objective_targeted_activity_relocation':
        assert objective_id is not None
        return ActivityRelocationOperator(
            instance,
            objectives,
            assess_plan,
            mode='objective_targeted',
            objective_id=objective_id,
            **path,
        )
    if spec_id == 'vrp.multiple_objectives.pareto_tradeoff_path':
        return ParetoTradeoffOperator(
            instance,
            objectives,
            assess_plan,
            move_family='path',
            time_step=settings.time_step,
            **path,
        )
    if spec_id == 'vrp.multiple_objectives.pareto_tradeoff_timing':
        return ParetoTradeoffOperator(
            instance,
            objectives,
            assess_plan,
            move_family='timing',
            time_step=settings.time_step,
            **common,
        )
    if spec_id == 'vrp.multiple_objectives.pareto_tradeoff_activity':
        return ParetoTradeoffOperator(
            instance,
            objectives,
            assess_plan,
            move_family='activity',
            time_step=settings.time_step,
            **path,
        )
    if spec_id == 'vrp.scenario_aggregation.aggregation_contribution_path_repair':
        assert objective_id is not None
        return AggregationContributionOperator(
            instance,
            objectives,
            assess_plan,
            objective_id=objective_id,
            move_family='path',
            time_step=settings.time_step,
            **path,
        )
    if spec_id == 'vrp.scenario_aggregation.aggregation_contribution_timing_repair':
        assert objective_id is not None
        return AggregationContributionOperator(
            instance,
            objectives,
            assess_plan,
            objective_id=objective_id,
            move_family='timing',
            time_step=settings.time_step,
            **common,
        )
    raise NotImplementedError(f'no Operator builder for {spec_id!r}')


def _feature_timing(
    mode: FeatureTimingMode,
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    objective_id: ObjectiveId | None,
    settings: VrpOperatorCatalogSettings,
) -> Operator:
    if objective_id is None:
        raise ValueError(f'{mode} timing Operator requires an objective target')
    return FeatureTimingOperator(
        instance,
        objectives,
        assess_plan,
        mode=mode,
        objective_id=objective_id,
        time_step=settings.time_step,
        max_neighborhood_size=settings.max_neighborhood_size,
        max_candidate_alternatives=settings.max_candidate_alternatives,
    )


def _make_registration(
    spec: _Specification,
    operator: Operator,
    objective_id: ObjectiveId | None,
    instance_text: str,
    settings: VrpOperatorCatalogSettings,
    path_costs: Mapping[ObjectiveId, AdditivePathCostRegistration],
) -> OperatorRegistration:
    operator_id = VrpOperatorId(spec.spec_id, objective_id)
    objective_text = None if objective_id is None else identifier_text(objective_id)
    callback = None if objective_id is None else path_costs.get(objective_id)
    config_values: list[object] = [
        'vrp-default-catalog-v1',
        spec.spec_id,
        objective_text,
        instance_text,
        settings.max_neighborhood_size,
        settings.max_candidate_alternatives,
        spec.restoration,
        spec.modification_count_definition,
    ]
    if spec.uses_time_step:
        config_values.append(finite_real(settings.time_step).hex())
    if spec.uses_paths:
        config_values.extend(
            (settings.max_paths_per_leg, settings.max_arc_expansion_steps)
        )
    if spec.spec_id == 'vrp.generic.objective_specific_shortest_path':
        config_values.append(
            None
            if callback is None
            else [callback.provider_id, callback.configuration_id]
        )
    return OperatorRegistration(
        operator_id=operator_id,
        configuration_id='vrp-operator-config:'
        + json.dumps(
            config_values,
            ensure_ascii=True,
            separators=(',', ':'),
        ),
        operator=operator,
        initial_state=None,
        max_neighborhood_size=settings.max_neighborhood_size,
        feasibility_restoration=spec.restoration,
        modification_count_definition=spec.modification_count_definition,
    )
