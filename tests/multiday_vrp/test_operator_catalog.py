"""Tests for default VRP Operator catalog expansion.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.identifiers import identifier_text
from multiobjective_vns.multiday_vrp import (
    build_default_vrp_operator_catalog as build_public_catalog,
)
from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    VrpObjectiveFunction,
)
from multiobjective_vns.multiday_vrp.input_records import VrpFeatureProfile
from multiobjective_vns.multiday_vrp.operators import (
    CapacityAwarePackageChainRelocationOperator,
    TimeWindowRegretBasedReinsertionOperator,
    ToolReservationGuidedInterventionRelocationOperator,
    TransferStepOperator,
    VehicleEliminationDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators import (
    PersonnelRepairOperator as ExportedPersonnelRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators import (
    VehicleRouteConsolidationOperator as ExportedVehicleRouteConsolidationOperator,
)
from multiobjective_vns.multiday_vrp.operators import (
    VehicleRouteSplitOperator as ExportedVehicleRouteSplitOperator,
)
from multiobjective_vns.multiday_vrp.operators.aggregation_contribution import (
    AggregationContributionOperator,
)
from multiobjective_vns.multiday_vrp.operators.catalog import (
    VrpOperatorCatalogSettings,
    VrpOperatorId,
    build_default_vrp_operator_catalog,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.feature_timing import (
    FeatureTimingOperator,
)
from multiobjective_vns.multiday_vrp.operators.driver_reassignment import (
    DriverReassignmentOperator,
)
from multiobjective_vns.multiday_vrp.operators.pareto_tradeoff import (
    ParetoTradeoffOperator,
)
from multiobjective_vns.multiday_vrp.operators.personnel_repair import (
    PersonnelRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.relationship_repair import (
    RelationshipGuidedServiceRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.route_path import (
    AdditivePathCostCallback,
    AdditivePathCostRegistration,
    AdditivePathCostRequest,
    ObjectiveSpecificShortestPathOperator,
    RouteLegRerouteOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_repair import (
    TimeWindowFocusedDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.timing import TimingAdjustmentOperator
from multiobjective_vns.multiday_vrp.operators.vehicle_route_consolidation import (
    VehicleRouteConsolidationOperator,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_split import (
    VehicleRouteSplitOperator,
)
from multiobjective_vns.objectives import ObjectiveDeclaration

from .test_universal_route_order import _always_assess, _test_plan


def _assessor() -> VrpPlanAssessor:
    return cast(VrpPlanAssessor, _always_assess)


def test_default_catalog_factory_is_reexported_from_vrp_package() -> None:
    assert build_public_catalog is build_default_vrp_operator_catalog
    assert ExportedPersonnelRepairOperator is PersonnelRepairOperator
    assert CapacityAwarePackageChainRelocationOperator.__name__ == (
        'CapacityAwarePackageChainRelocationOperator'
    )
    assert TransferStepOperator.__name__ == 'TransferStepOperator'
    assert ToolReservationGuidedInterventionRelocationOperator.__name__ == (
        'ToolReservationGuidedInterventionRelocationOperator'
    )
    assert (
        ExportedVehicleRouteConsolidationOperator is VehicleRouteConsolidationOperator
    )
    assert ExportedVehicleRouteSplitOperator is VehicleRouteSplitOperator
    assert VehicleEliminationDestroyRepairOperator.__name__ == (
        'VehicleEliminationDestroyRepairOperator'
    )
    assert TimeWindowRegretBasedReinsertionOperator.__name__ == (
        'TimeWindowRegretBasedReinsertionOperator'
    )


def test_factory_builds_core_catalog_with_stable_objective_variants(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)

    registrations = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )

    identifiers = tuple(cast(VrpOperatorId, item.operator_id) for item in registrations)
    texts = tuple(identifier_text(value) for value in identifiers)
    assert len(texts) == len(set(texts))
    assert all(isinstance(value, VrpOperatorId) for value in identifiers)
    assert all(item.initial_state is None for item in registrations)
    assert all(item.max_neighborhood_size == 5 for item in registrations)
    assert not any(
        isinstance(item.operator, DriverReassignmentOperator)
        for item in registrations
    )
    assert any(
        isinstance(item.operator, TimingAdjustmentOperator) for item in registrations
    )
    assert (
        sum(
            isinstance(item.operator, RouteLegRerouteOperator) for item in registrations
        )
        == 2
    )

    reroute_ids = tuple(
        cast(VrpOperatorId, item.operator_id)
        for item in registrations
        if isinstance(item.operator, RouteLegRerouteOperator)
    )
    assert tuple(item.objective_id for item in reroute_ids) == (
        None,
        objectives.objectives[0].objective_id,
    )
    assert all(
        'sample-instance' in identifier_text(item.configuration_id)
        for item in registrations
    )


def test_factory_registers_route_consolidation_and_split_as_core_operators(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)

    registrations = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )

    consolidations = tuple(
        item
        for item in registrations
        if isinstance(item.operator, VehicleRouteConsolidationOperator)
    )
    splits = tuple(
        item
        for item in registrations
        if isinstance(item.operator, VehicleRouteSplitOperator)
    )
    assert len(consolidations) == 2
    assert len(splits) == 2
    assert all(not item.feasibility_restoration for item in consolidations + splits)
    assert {
        cast(VrpOperatorId, item.operator_id).objective_id for item in consolidations
    } == {None, objectives.objectives[0].objective_id}
    assert {cast(VrpOperatorId, item.operator_id).objective_id for item in splits} == {
        None,
        objectives.objectives[0].objective_id,
    }


def test_factory_registers_vehicle_elimination_as_core_operator(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)

    registrations = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )
    eliminations = tuple(
        item
        for item in registrations
        if isinstance(item.operator, VehicleEliminationDestroyRepairOperator)
    )

    assert len(eliminations) == 2
    assert all(not item.feasibility_restoration for item in eliminations)
    assert {
        cast(VrpOperatorId, item.operator_id).objective_id for item in eliminations
    } == {None, objectives.objectives[0].objective_id}


def test_factory_adds_only_active_feature_operators_in_target_order(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    instance = instance._replace(
        feature_profile=VrpFeatureProfile(
            frozenset(
                {
                    *instance.feature_profile.active_features,
                    'within_day_time_dependent_travel',
                    'scenario_uncertainty',
                }
            )
        ),
    )
    settings = VrpOperatorCatalogSettings(
        objective_targets=(objectives.objectives[0].objective_id,),
        time_step=0.5,
    )

    registrations = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
        settings=settings,
    )

    timed = tuple(
        item.operator
        for item in registrations
        if isinstance(item.operator, FeatureTimingOperator)
    )
    assert len(timed) == 2
    identifiers = tuple(identifier_text(item.operator_id) for item in registrations)
    assert any('differential_reroute' in item for item in identifiers)


def test_factory_registers_personnel_and_work_rule_repairs(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    active_features = frozenset(
        {
            *instance.feature_profile.active_features,
            'driving_skill_restrictions',
            'restricted_personnel_availability',
            'personnel_availability_differentiation',
            'staff_depot_continuity',
            'work_driving_rest_hos_rules',
        }
    )
    instance = instance._replace(
        feature_profile=instance.feature_profile._replace(
            active_features=active_features
        )
    )

    registrations = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='personnel-repairs',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )

    repairs = tuple(
        item.operator
        for item in registrations
        if isinstance(item.operator, PersonnelRepairOperator)
    )
    assert {item._mode for item in repairs} == {
        'skill_guided',
        'availability',
        'staff_continuity',
        'work_rule',
    }
    assert len(repairs) == 6
    repair_registrations = tuple(
        item
        for item in registrations
        if isinstance(item.operator, PersonnelRepairOperator)
    )
    assert all(item.feasibility_restoration for item in repair_registrations)


def test_factory_skips_assignment_repairs_for_shared_personnel_profiles(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    active_features = frozenset(
        {
            *instance.feature_profile.active_features,
            'restricted_personnel_availability',
        }
    )
    instance = instance._replace(
        feature_profile=instance.feature_profile._replace(
            active_features=active_features
        )
    )

    registrations = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='shared-personnel-profiles',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )

    assert not any(
        isinstance(item.operator, DriverReassignmentOperator)
        for item in registrations
    )
    assert not any(
        isinstance(item.operator, PersonnelRepairOperator)
        and item.operator._mode == 'availability'
        for item in registrations
    )


def test_factory_builds_core_multiobjective_and_scenario_operators(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    objectives = ObjectiveDeclaration(
        (
            *objectives.objectives,
            VrpObjectiveFunction(
                'duration',
                'test:duration-objective',
                'test:duration-objective-v1',
            ),
        )
    )
    instance = instance._replace(
        feature_profile=VrpFeatureProfile(
            frozenset(
                {
                    *instance.feature_profile.active_features,
                    'multiple_objectives',
                    'scenario_aggregation',
                }
            )
        ),
    )

    registrations = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )

    operators = {
        cast(VrpOperatorId, item.operator_id).operator_spec_id: item.operator
        for item in registrations
    }
    assert isinstance(
        operators['vrp.multiple_objectives.pareto_tradeoff_path'],
        ParetoTradeoffOperator,
    )
    assert isinstance(
        operators['vrp.multiple_objectives.pareto_tradeoff_timing'],
        ParetoTradeoffOperator,
    )
    assert isinstance(
        operators['vrp.multiple_objectives.pareto_tradeoff_activity'],
        ParetoTradeoffOperator,
    )
    assert isinstance(
        operators['vrp.scenario_aggregation.aggregation_contribution_path_repair'],
        AggregationContributionOperator,
    )
    assert isinstance(
        operators['vrp.scenario_aggregation.aggregation_contribution_timing_repair'],
        AggregationContributionOperator,
    )


def test_factory_builds_route_and_service_repair_operators(tmp_path: Path) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    instance = instance._replace(
        feature_profile=VrpFeatureProfile(
            frozenset(
                {
                    *instance.feature_profile.active_features,
                    'restrictive_service_windows',
                    'additional_activity_relationships',
                }
            )
        ),
    )

    registrations = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )
    operators = tuple(item.operator for item in registrations)

    assert any(
        isinstance(item, TimeWindowFocusedDestroyRepairOperator) for item in operators
    )
    assert any(
        isinstance(item, RelationshipGuidedServiceRepairOperator) for item in operators
    )
    assert any(
        isinstance(item, TimeWindowRegretBasedReinsertionOperator) for item in operators
    )


def test_factory_rejects_duplicate_or_unknown_objective_targets(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    objective_id = objectives.objectives[0].objective_id

    with pytest.raises(ValueError, match='unique'):
        VrpOperatorCatalogSettings(objective_targets=(objective_id, objective_id))

    settings = VrpOperatorCatalogSettings(objective_targets=('unknown-objective',))
    with pytest.raises(ValueError, match='declared'):
        build_default_vrp_operator_catalog(
            instance,
            problem_instance_id='sample-instance',
            objective_declaration=objectives,
            assess_plan=_assessor(),
            settings=settings,
        )


def test_factory_registers_the_five_capacity_and_transfer_repairs(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    instance = instance._replace(
        feature_profile=VrpFeatureProfile(
            frozenset(
                {
                    *instance.feature_profile.active_features,
                    'package_transfer_operations',
                    'potentially_binding_cargo_tool_capacity',
                    'tool_capacity_reservations',
                }
            )
        )
    )

    registrations = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )
    expected = {
        'vrp.cargo_tool_capacity.capacity_aware_package_chain_relocation',
        'vrp.package_transfer.add_transfer_step',
        'vrp.package_transfer.remove_transfer_step',
        'vrp.package_transfer.reassign_transfer_step',
        'vrp.tool_capacity.tool_reservation_guided_intervention_relocation',
    }
    found = {
        (
            cast(VrpOperatorId, item.operator_id).operator_spec_id,
            cast(VrpOperatorId, item.operator_id).objective_id,
        ): item
        for item in registrations
        if cast(VrpOperatorId, item.operator_id).operator_spec_id in expected
    }
    assert {spec_id for spec_id, _objective_id in found} == expected
    assert all(item.feasibility_restoration for item in found.values())
    assert all((spec_id, None) in found for spec_id in expected)


def test_catalog_settings_reject_invalid_limits_and_time_steps() -> None:
    with pytest.raises(TypeError, match='integer'):
        VrpOperatorCatalogSettings(max_neighborhood_size=True)
    with pytest.raises(ValueError, match='time_step'):
        VrpOperatorCatalogSettings(time_step=0.0)
    with pytest.raises(ValueError, match='at most 32'):
        VrpOperatorCatalogSettings(max_paths_per_leg=33)


def test_factory_instantiates_the_supported_feature_families(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    active = {
        *instance.feature_profile.active_features,
        'within_day_time_dependent_travel',
        'scenario_uncertainty',
        'network_event_exposure',
        'multi_day_planning',
        'overnight_depot_choice',
        'multiple_tours_per_day',
        'restricted_vehicle_availability',
        'potentially_binding_personnel_seat_capacity',
        'package_vehicle_compatibility',
        'package_pickup_delivery',
        'positive_service_durations',
        'optional_service_omission',
        'interventions',
        'intervention_qualifications_or_team_size',
    }
    instance = instance._replace(feature_profile=VrpFeatureProfile(frozenset(active)))

    registrations = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )

    assert len(registrations) > 20
    assert len({identifier_text(item.operator_id) for item in registrations}) == len(
        registrations
    )


def test_factory_configuration_changes_with_registration_settings(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    first = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
        settings=VrpOperatorCatalogSettings(time_step=1.0),
    )
    second = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
        settings=VrpOperatorCatalogSettings(time_step=0.25),
    )

    first_by_id = {identifier_text(item.operator_id): item for item in first}
    second_by_id = {identifier_text(item.operator_id): item for item in second}
    assert first_by_id.keys() == second_by_id.keys()
    timing_ids = tuple(
        key
        for key, item in first_by_id.items()
        if isinstance(item.operator, TimingAdjustmentOperator)
    )
    assert timing_ids
    assert all(
        identifier_text(first_by_id[key].configuration_id)
        != identifier_text(second_by_id[key].configuration_id)
        for key in timing_ids
    )


def test_factory_preserves_objective_order_and_additive_callback_identity(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    first_objective = objectives.objectives[0]
    second_objective = VrpObjectiveFunction(
        'second-objective',
        'component:second-objective',
        'configuration:second-objective',
    )
    declaration = ObjectiveDeclaration((first_objective, second_objective))

    class _PathCost:
        def __call__(self, _arc_id: str, _request: AdditivePathCostRequest) -> float:
            return 1.0

    registrations = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=declaration,
        assess_plan=_assessor(),
        additive_path_costs={
            first_objective.objective_id: AdditivePathCostRegistration(
                'provider-x',
                'settings-x',
                cast(AdditivePathCostCallback, _PathCost()),
            )
        },
    )
    path_targets = tuple(
        cast(VrpOperatorId, item.operator_id).objective_id
        for item in registrations
        if isinstance(item.operator, ObjectiveSpecificShortestPathOperator)
    )

    assert path_targets == (
        first_objective.objective_id,
        second_objective.objective_id,
    )
    first_path_registration = next(
        item
        for item in registrations
        if isinstance(item.operator, ObjectiveSpecificShortestPathOperator)
        and cast(VrpOperatorId, item.operator_id).objective_id
        == first_objective.objective_id
    )
    configuration_text = identifier_text(first_path_registration.configuration_id)
    assert 'provider-x' in configuration_text
    assert 'settings-x' in configuration_text
