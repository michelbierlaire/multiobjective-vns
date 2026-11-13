"""Tests for default VRP Operator catalog expansion.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.candidate_batch import CandidateBatchAdapter
from multiobjective_vns.identifiers import (
    ObjectiveId,
    ProblemInstanceId,
    identifier_text,
)
from multiobjective_vns.multiday_vrp import (
    build_default_vrp_operator_catalog as build_public_catalog,
)
from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    VrpObjectiveFunction,
)
from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    VrpFeatureProfile,
)
from multiobjective_vns.multiday_vrp.operators import (
    CapacityAwarePackageChainRelocationOperator,
    TimeWindowGuidedRouteRerouteOperator,
    TimeWindowRegretBasedReinsertionOperator,
    TimeWindowScheduleComplianceOperator,
    ToolReservationGuidedInterventionRelocationOperator,
    TransferStepOperator,
    VehicleEliminationDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators import (
    PersonnelRepairOperator as ExportedPersonnelRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators import (
    SlackGuidedOrOptRelocationOperator as ExportedSlackGuidedOrOptRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators import (
    VehicleRouteConsolidationOperator as ExportedVehicleRouteConsolidationOperator,
)
from multiobjective_vns.multiday_vrp.operators import (
    VehicleRouteSplitOperator as ExportedVehicleRouteSplitOperator,
)
from multiobjective_vns.multiday_vrp.operators import (
    WindowCompatiblePackageClusterReassignmentOperator as ExportedWindowClusterOperator,
)
from multiobjective_vns.multiday_vrp.operators import (
    WindowFeasibleSwapStarOperator as ExportedWindowFeasibleSwapStarOperator,
)
from multiobjective_vns.multiday_vrp.operators import (
    WindowPrioritizedEjectionChainConsolidationOperator as ExportedWindowEjectionOperator,
)
from multiobjective_vns.multiday_vrp.operators.aggregation_contribution import (
    AggregationContributionOperator,
)
from multiobjective_vns.multiday_vrp.operators.catalog import (
    VrpOperatorCatalog,
    VrpOperatorCatalogSettings,
    VrpOperatorId,
    build_default_vrp_operator_catalog,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.driver_reassignment import (
    DriverReassignmentOperator,
)
from multiobjective_vns.multiday_vrp.operators.feature_timing import (
    FeatureTimingOperator,
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
from multiobjective_vns.multiday_vrp.operators.service_window_cluster import (
    WindowCompatiblePackageClusterReassignmentOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_ejection_chain import (
    WindowPrioritizedEjectionChainConsolidationOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_repair import (
    TimeWindowFocusedDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_structural import (
    SlackGuidedOrOptRelocationOperator,
    WindowFeasibleSwapStarOperator,
)
from multiobjective_vns.multiday_vrp.operators.timing import TimingAdjustmentOperator
from multiobjective_vns.multiday_vrp.operators.vehicle_route_consolidation import (
    VehicleRouteConsolidationOperator,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_split import (
    VehicleRouteSplitOperator,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.registrations import (
    OperatorCompositionRegistration,
    OperatorRegistration,
)

from .test_universal_route_order import _always_assess, _test_plan


def _assessor() -> VrpPlanAssessor:
    return cast(VrpPlanAssessor, _always_assess)


def _operator_implementation(operator: object) -> object:
    if isinstance(operator, CandidateBatchAdapter):
        return operator.delegate
    return operator


def _is_registered_type(registration: OperatorRegistration, expected: type) -> bool:
    return isinstance(_operator_implementation(registration.operator), expected)


def _operator_registrations(
    instance: LoadedVrpInstance,
    *,
    problem_instance_id: ProblemInstanceId,
    objective_declaration: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    additive_path_costs: Mapping[ObjectiveId, AdditivePathCostRegistration]
    | None = None,
    settings: VrpOperatorCatalogSettings = VrpOperatorCatalogSettings(),
) -> tuple[OperatorRegistration, ...]:
    """Return the ordinary registrations from the public catalog record."""
    catalog = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id=problem_instance_id,
        objective_declaration=objective_declaration,
        assess_plan=assess_plan,
        additive_path_costs=additive_path_costs,
        settings=settings,
    )
    return catalog.operators


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
    assert (
        ExportedSlackGuidedOrOptRelocationOperator is SlackGuidedOrOptRelocationOperator
    )
    assert ExportedWindowFeasibleSwapStarOperator is WindowFeasibleSwapStarOperator
    assert (
        ExportedWindowClusterOperator
        is WindowCompatiblePackageClusterReassignmentOperator
    )
    assert (
        ExportedWindowEjectionOperator
        is WindowPrioritizedEjectionChainConsolidationOperator
    )


def test_factory_builds_core_catalog_with_stable_objective_variants(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)

    registrations = _operator_registrations(
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
    assert all(
        isinstance(item.operator, CandidateBatchAdapter) for item in registrations
    )
    assert not any(
        _is_registered_type(item, DriverReassignmentOperator) for item in registrations
    )
    assert any(
        _is_registered_type(item, TimingAdjustmentOperator) for item in registrations
    )
    assert (
        sum(
            _is_registered_type(item, RouteLegRerouteOperator) for item in registrations
        )
        == 2
    )

    reroute_ids = tuple(
        cast(VrpOperatorId, item.operator_id)
        for item in registrations
        if _is_registered_type(item, RouteLegRerouteOperator)
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

    registrations = _operator_registrations(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )

    consolidations = tuple(
        item
        for item in registrations
        if _is_registered_type(item, VehicleRouteConsolidationOperator)
    )
    splits = tuple(
        item
        for item in registrations
        if _is_registered_type(item, VehicleRouteSplitOperator)
    )
    assert len(consolidations) == 4
    assert len(splits) == 2
    assert all(not item.feasibility_restoration for item in consolidations + splits)
    consolidation_ids = tuple(
        cast(VrpOperatorId, item.operator_id) for item in consolidations
    )
    assert {identifier.operator_spec_id for identifier in consolidation_ids} == {
        'vrp.generic.vehicle_route_consolidation_best_insertion',
        'vrp.generic.vehicle_route_consolidation_regret_insertion',
    }
    assert all(
        {
            identifier.objective_id
            for identifier in consolidation_ids
            if identifier.operator_spec_id == spec_id
        }
        == {None, objectives.objectives[0].objective_id}
        for spec_id in {
            'vrp.generic.vehicle_route_consolidation_best_insertion',
            'vrp.generic.vehicle_route_consolidation_regret_insertion',
        }
    )
    assert {cast(VrpOperatorId, item.operator_id).objective_id for item in splits} == {
        None,
        objectives.objectives[0].objective_id,
    }


def test_factory_registers_vehicle_elimination_as_core_operator(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)

    registrations = _operator_registrations(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )
    eliminations = tuple(
        item
        for item in registrations
        if _is_registered_type(item, VehicleEliminationDestroyRepairOperator)
    )

    assert len(eliminations) == 4
    assert all(not item.feasibility_restoration for item in eliminations)
    elimination_ids = tuple(
        cast(VrpOperatorId, item.operator_id) for item in eliminations
    )
    assert {identifier.operator_spec_id for identifier in elimination_ids} == {
        'vrp.generic.vehicle_elimination_best_insertion',
        'vrp.generic.vehicle_elimination_regret_insertion',
    }
    assert all(
        {
            identifier.objective_id
            for identifier in elimination_ids
            if identifier.operator_spec_id == spec_id
        }
        == {None, objectives.objectives[0].objective_id}
        for spec_id in {
            'vrp.generic.vehicle_elimination_best_insertion',
            'vrp.generic.vehicle_elimination_regret_insertion',
        }
    )


def test_factory_omits_vehicle_count_moves_for_one_day_fixed_count(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    instance = instance._replace(
        settings=instance.settings._replace(exact_active_vehicle_count=1),
        feature_profile=VrpFeatureProfile(
            instance.feature_profile.active_features - {'variable_active_vehicle_count'}
        ),
    )

    registrations = _operator_registrations(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )
    spec_ids = {
        cast(VrpOperatorId, item.operator_id).operator_spec_id for item in registrations
    }

    assert 'vrp.generic.vehicle_route_consolidation_best_insertion' not in spec_ids
    assert 'vrp.generic.vehicle_route_consolidation_regret_insertion' not in spec_ids
    assert 'vrp.generic.vehicle_elimination_best_insertion' not in spec_ids
    assert 'vrp.generic.vehicle_elimination_regret_insertion' not in spec_ids
    assert 'vrp.generic.vehicle_route_split' not in spec_ids
    assert 'vrp.generic.two_opt' in spec_ids


def test_factory_keeps_count_preserving_multi_day_moves_with_fixed_count(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    instance = instance._replace(
        settings=instance.settings._replace(exact_active_vehicle_count=1),
        feature_profile=VrpFeatureProfile(
            frozenset(
                {
                    *instance.feature_profile.active_features,
                    'multi_day_planning',
                }
                - {'variable_active_vehicle_count'}
            )
        ),
    )

    registrations = _operator_registrations(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )
    spec_ids = {
        cast(VrpOperatorId, item.operator_id).operator_spec_id for item in registrations
    }

    assert 'vrp.generic.vehicle_route_consolidation_best_insertion' in spec_ids
    assert 'vrp.generic.vehicle_route_consolidation_regret_insertion' in spec_ids
    assert 'vrp.generic.vehicle_elimination_best_insertion' not in spec_ids
    assert 'vrp.generic.vehicle_elimination_regret_insertion' not in spec_ids
    assert 'vrp.generic.vehicle_route_split' in spec_ids


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

    registrations = _operator_registrations(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
        settings=settings,
    )

    timed = tuple(
        item.operator
        for item in registrations
        if _is_registered_type(item, FeatureTimingOperator)
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

    registrations = _operator_registrations(
        instance,
        problem_instance_id='personnel-repairs',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )

    repairs = tuple(
        item.operator
        for item in registrations
        if _is_registered_type(item, PersonnelRepairOperator)
    )
    assert {cast(PersonnelRepairOperator, item)._mode for item in repairs} == {
        'skill_guided',
        'availability',
        'staff_continuity',
        'work_rule',
    }
    assert len(repairs) == 6
    repair_registrations = tuple(
        item
        for item in registrations
        if _is_registered_type(item, PersonnelRepairOperator)
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

    registrations = _operator_registrations(
        instance,
        problem_instance_id='shared-personnel-profiles',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )

    assert not any(
        _is_registered_type(item, DriverReassignmentOperator) for item in registrations
    )
    assert not any(
        _is_registered_type(item, PersonnelRepairOperator)
        and cast(PersonnelRepairOperator, item.operator)._mode == 'availability'
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

    registrations = _operator_registrations(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )

    operators = {
        cast(
            VrpOperatorId, item.operator_id
        ).operator_spec_id: _operator_implementation(item.operator)
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
        time_window_violation_objective_id='window-penalty',
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
    objectives = ObjectiveDeclaration(
        (
            *objectives.objectives,
            VrpObjectiveFunction(
                'window-penalty', 'test-window-objective', 'test-window-v1'
            ),
        )
    )

    catalog = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )
    assert isinstance(catalog, VrpOperatorCatalog)
    registrations = catalog.operators
    operators = tuple(_operator_implementation(item.operator) for item in registrations)

    assert any(
        isinstance(item, TimeWindowFocusedDestroyRepairOperator) for item in operators
    )
    assert any(
        isinstance(item, RelationshipGuidedServiceRepairOperator) for item in operators
    )
    assert any(
        isinstance(item, TimeWindowRegretBasedReinsertionOperator) for item in operators
    )
    compliance_repairs = tuple(
        item
        for item in registrations
        if _is_registered_type(item, TimeWindowScheduleComplianceOperator)
    )
    assert len(compliance_repairs) == len(objectives.objectives)
    assert all(not item.feasibility_restoration for item in compliance_repairs)
    assert {
        cast(VrpOperatorId, item.operator_id).objective_id
        for item in compliance_repairs
    } == {item.objective_id for item in objectives.objectives}
    activity_relocations = tuple(
        cast(VrpOperatorId, item.operator_id)
        for item in registrations
        if cast(VrpOperatorId, item.operator_id).operator_spec_id
        == 'vrp.service_windows.activity_relocation'
    )
    assert tuple(item.objective_id for item in activity_relocations) == (
        'window-penalty',
    )
    destroy_repair_targets = tuple(
        cast(VrpOperatorId, item.operator_id).objective_id
        for item in registrations
        if cast(VrpOperatorId, item.operator_id).operator_spec_id
        == 'vrp.service_windows.destroy_and_repair'
    )
    assert destroy_repair_targets == ('window-penalty',)
    regret_reinsertion_targets = tuple(
        cast(VrpOperatorId, item.operator_id).objective_id
        for item in registrations
        if cast(VrpOperatorId, item.operator_id).operator_spec_id
        == 'vrp.service_windows.regret_based_reinsertion'
    )
    assert regret_reinsertion_targets == ('window-penalty',)
    target_filtered_catalog = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
        settings=VrpOperatorCatalogSettings(
            objective_targets=(objectives.objectives[0].objective_id,)
        ),
    )
    target_filtered_activity_relocations = tuple(
        cast(VrpOperatorId, item.operator_id).objective_id
        for item in target_filtered_catalog.operators
        if cast(VrpOperatorId, item.operator_id).operator_spec_id
        == 'vrp.service_windows.activity_relocation'
    )
    assert target_filtered_activity_relocations == ('window-penalty',)
    target_filtered_route_reroutes = tuple(
        cast(VrpOperatorId, item.operator_id).objective_id
        for item in target_filtered_catalog.operators
        if cast(VrpOperatorId, item.operator_id).operator_spec_id
        == 'vrp.service_windows.route_leg_rerouting'
    )
    assert target_filtered_route_reroutes == ('window-penalty',)
    target_filtered_destroy_repair = tuple(
        cast(VrpOperatorId, item.operator_id).objective_id
        for item in target_filtered_catalog.operators
        if cast(VrpOperatorId, item.operator_id).operator_spec_id
        == 'vrp.service_windows.destroy_and_repair'
    )
    assert target_filtered_destroy_repair == ('window-penalty',)
    target_filtered_regret_reinsertion = tuple(
        cast(VrpOperatorId, item.operator_id).objective_id
        for item in target_filtered_catalog.operators
        if cast(VrpOperatorId, item.operator_id).operator_spec_id
        == 'vrp.service_windows.regret_based_reinsertion'
    )
    assert target_filtered_regret_reinsertion == ('window-penalty',)

    registrations_by_id = {item.operator_id: item for item in registrations}
    composites = catalog.pre_registered_compositions
    assert len(composites) == 3 * len(objectives.objectives)
    expected_families = {
        'vrp.composite.vehicle_route_consolidation_best_then_window_repair': 'vrp.generic.vehicle_route_consolidation_best_insertion',
        'vrp.composite.vehicle_route_consolidation_regret_then_window_repair': 'vrp.generic.vehicle_route_consolidation_regret_insertion',
        'vrp.composite.vehicle_route_split_then_window_repair': 'vrp.generic.vehicle_route_split',
    }
    found: set[tuple[str, ObjectiveId | None]] = set()
    for composition in composites:
        assert isinstance(composition, OperatorCompositionRegistration)
        composite_id = cast(VrpOperatorId, composition.operator_id)
        objective_id = composite_id.objective_id
        first_id = cast(VrpOperatorId, composition.first_operator_id)
        second_id = cast(VrpOperatorId, composition.second_operator_id)
        assert first_id.objective_id == objective_id
        assert second_id == VrpOperatorId(
            'vrp.service_windows.schedule_compliance_repair', objective_id
        )
        assert (
            first_id.operator_spec_id
            == expected_families[composite_id.operator_spec_id]
        )
        assert composition.first_operator_id in registrations_by_id
        assert composition.second_operator_id in registrations_by_id
        assert 'sample-instance' in identifier_text(composition.configuration_id)
        found.add((composite_id.operator_spec_id, objective_id))
    assert found == {
        (spec_id, objective.objective_id)
        for spec_id in expected_families
        for objective in objectives.objectives
    }


def test_factory_registers_window_structural_operators_and_depth_identity(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    instance = instance._replace(
        time_window_violation_objective_id='window-penalty',
        feature_profile=VrpFeatureProfile(
            frozenset(
                {
                    *instance.feature_profile.active_features,
                    'restrictive_service_windows',
                }
            )
        ),
    )
    objectives = ObjectiveDeclaration(
        (
            *objectives.objectives,
            VrpObjectiveFunction(
                'window-penalty', 'test-window-objective', 'test-window-v1'
            ),
        )
    )

    first = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
        settings=VrpOperatorCatalogSettings(max_ejection_chain_depth=2),
    )
    second = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
        settings=VrpOperatorCatalogSettings(max_ejection_chain_depth=4),
    )

    def registrations_by_spec(
        catalog: VrpOperatorCatalog,
    ) -> dict[str, tuple[OperatorRegistration, ...]]:
        return {
            cast(VrpOperatorId, item.operator_id).operator_spec_id: tuple(
                registration
                for registration in catalog.operators
                if cast(VrpOperatorId, registration.operator_id).operator_spec_id
                == cast(VrpOperatorId, item.operator_id).operator_spec_id
            )
            for item in catalog.operators
        }

    first_by_spec = registrations_by_spec(first)
    second_by_spec = registrations_by_spec(second)
    expected = {
        'vrp.service_windows.slack_guided_or_opt_relocation': (
            SlackGuidedOrOptRelocationOperator
        ),
        'vrp.service_windows.window_feasible_swap_star': (
            WindowFeasibleSwapStarOperator
        ),
        'vrp.service_windows.ejection_chain_route_consolidation': (
            WindowPrioritizedEjectionChainConsolidationOperator
        ),
    }
    for spec_id, operator_type in expected.items():
        assert len(first_by_spec[spec_id]) == 1 + len(objectives.objectives)
        assert all(
            isinstance(_operator_implementation(registration.operator), operator_type)
            for registration in first_by_spec[spec_id]
        )
    route_reroutes = first_by_spec['vrp.service_windows.route_leg_rerouting']
    assert len(route_reroutes) == 1
    assert cast(VrpOperatorId, route_reroutes[0].operator_id).objective_id == (
        'window-penalty'
    )
    assert isinstance(
        _operator_implementation(route_reroutes[0].operator),
        TimeWindowGuidedRouteRerouteOperator,
    )
    ejection_first = first_by_spec[
        'vrp.service_windows.ejection_chain_route_consolidation'
    ]
    ejection_second = second_by_spec[
        'vrp.service_windows.ejection_chain_route_consolidation'
    ]
    assert tuple(item.configuration_id for item in ejection_first) != tuple(
        item.configuration_id for item in ejection_second
    )


def test_factory_omits_window_structural_operators_without_window_objective(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    instance = instance._replace(
        feature_profile=VrpFeatureProfile(
            frozenset(
                {
                    *instance.feature_profile.active_features,
                    'restrictive_service_windows',
                }
            )
        ),
    )

    registrations = _operator_registrations(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )

    assert not any(
        cast(VrpOperatorId, item.operator_id).operator_spec_id
        in {
            'vrp.service_windows.schedule_compliance_repair',
            'vrp.service_windows.activity_relocation',
            'vrp.service_windows.route_leg_rerouting',
            'vrp.service_windows.destroy_and_repair',
            'vrp.service_windows.regret_based_reinsertion',
            'vrp.service_windows.slack_guided_or_opt_relocation',
            'vrp.service_windows.window_feasible_swap_star',
            'vrp.service_windows.ejection_chain_route_consolidation',
        }
        for item in registrations
    )


def test_catalog_settings_validate_ejection_chain_depth() -> None:
    with pytest.raises(ValueError, match='max_ejection_chain_depth'):
        VrpOperatorCatalogSettings(max_ejection_chain_depth=0)


def test_factory_rejects_duplicate_or_unknown_objective_targets(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    objective_id = objectives.objectives[0].objective_id

    with pytest.raises(ValueError, match='unique'):
        VrpOperatorCatalogSettings(objective_targets=(objective_id, objective_id))

    settings = VrpOperatorCatalogSettings(objective_targets=('unknown-objective',))
    with pytest.raises(ValueError, match='declared'):
        _operator_registrations(
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

    registrations = _operator_registrations(
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


def test_factory_registers_window_package_cluster_reassignment(
    tmp_path: Path,
) -> None:
    instance, objectives, _solution = _test_plan(tmp_path)
    instance = instance._replace(
        feature_profile=VrpFeatureProfile(
            frozenset(
                {
                    *instance.feature_profile.active_features,
                    'restrictive_service_windows',
                }
            )
        )
    )

    registrations = _operator_registrations(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
    )
    found = tuple(
        registration
        for registration in registrations
        if isinstance(
            _operator_implementation(registration.operator),
            WindowCompatiblePackageClusterReassignmentOperator,
        )
    )

    assert len(found) == 2
    assert all(not registration.feasibility_restoration for registration in found)
    assert {
        cast(VrpOperatorId, registration.operator_id).objective_id
        for registration in found
    } == {None, objectives.objectives[0].objective_id}


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

    registrations = _operator_registrations(
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
    first = _operator_registrations(
        instance,
        problem_instance_id='sample-instance',
        objective_declaration=objectives,
        assess_plan=_assessor(),
        settings=VrpOperatorCatalogSettings(time_step=1.0),
    )
    second = _operator_registrations(
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
        if _is_registered_type(item, TimingAdjustmentOperator)
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

    registrations = _operator_registrations(
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
        if _is_registered_type(item, ObjectiveSpecificShortestPathOperator)
    )

    assert path_targets == (
        first_objective.objective_id,
        second_objective.objective_id,
    )
    first_path_registration = next(
        item
        for item in registrations
        if _is_registered_type(item, ObjectiveSpecificShortestPathOperator)
        and cast(VrpOperatorId, item.operator_id).objective_id
        == first_objective.objective_id
    )
    configuration_text = identifier_text(first_path_registration.configuration_id)
    assert 'provider-x' in configuration_text
    assert 'settings-x' in configuration_text
