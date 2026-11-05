"""Tests for VRP work, driving, and HOS rule evaluation.

Created: 2026-11-03T19:30:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import inf
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp import (
    HosRegimeRegistration,
    VrpApplicationEvaluator,
    VrpExtensionRegistry,
    build_vrp_objective_declaration,
    load_vrp_instance,
)
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    HosConfigurationRequest,
    HosRuleFinding,
    HosScenarioRequest,
)
from multiobjective_vns.multiday_vrp.input_records import (
    DailyWorkLimitRecord,
    FrozenTomlTable,
    HosRuleDefinition,
    LoadedVrpInstance,
    PenaltyFunctionSpec,
    VehicleAvailabilityRecord,
)
from multiobjective_vns.multiday_vrp.solution import (
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)

from .input_fixtures import write_minimal_vrp_bundle


def _make_rule_instance(root: Path, treatment: str) -> LoadedVrpInstance:
    manifest = write_minimal_vrp_bundle(root)
    instance_dir = manifest.parent
    (instance_dir / 'depots.csv').write_text(
        'depot_id,node_id\ndepot-1,node-a\ndepot-2,node-b\n', encoding='utf-8'
    )
    (instance_dir / 'vehicle-overnight-depots.csv').write_text(
        'vehicle_id,depot_id\nvehicle-1,depot-1\nvehicle-1,depot-2\n',
        encoding='utf-8',
    )
    objectives_path = instance_dir / 'objectives.toml'
    objectives_path.write_text(
        objectives_path.read_text(encoding='utf-8').replace(
            "base_measure_id = 'vehicles_used'", "base_measure_id = 'total_travel_time'"
        ),
        encoding='utf-8',
    )
    instance = load_vrp_instance(manifest)
    settings = instance.settings._replace(
        rule_treatment=treatment,
        active_simple_rules=frozenset({'daily_work'}),
    )
    work_rule_data = instance.work_rule_data._replace(
        daily_work_limits=(DailyWorkLimitRecord('driver-1', 1, 3.0),)
    )
    objective = instance.objective_data.objectives[0]._replace(
        penalty_scales={
            'early_window': 1.0,
            'late_window': 1.0,
            'vehicle_availability': 1.0,
            'personnel_availability': 1.0,
            'duty_overlap': 1.0,
            'simple_rule': 1.0,
        }
    )
    objective_data = instance.objective_data._replace(
        objectives=(objective,),
        penalty_functions=(
            *instance.objective_data.penalty_functions,
            PenaltyFunctionSpec(
                'vehicles-used',
                'simple_rule',
                'daily_work',
                'linear',
                FrozenTomlTable({'rate': 1.0}),
            ),
        ),
    )
    return instance._replace(
        settings=settings,
        work_rule_data=work_rule_data,
        objective_data=objective_data,
    )


def _five_minute_route() -> VrpSolution:
    return VrpSolution(
        routes=(
            VehicleDayRoute(
                vehicle_id='vehicle-1',
                day_index=1,
                overnight_depot_id='depot-2',
                start_time=0.0,
                driver_id='driver-1',
                stops=(),
                legs=(RouteLeg(('arc-1',)),),
            ),
        ),
        transfer_steps=(),
    )


def test_penalty_treatment_adds_simple_daily_work_excess(tmp_path: Path) -> None:
    instance = _make_rule_instance(tmp_path, 'penalty')
    result = VrpApplicationEvaluator(instance).evaluate(
        _five_minute_route(), build_vrp_objective_declaration(instance)
    )

    assert result.feasible is True
    assert result.objective_values == (7.0,)
    analysis = cast(VrpOperatorAnalysis, result.analysis)
    finding = next(
        item for item in analysis.resource_findings if item.kind == 'driver_work'
    )
    assert finding.measure == 2.0
    assert finding.penalty_by_objective == (('vehicles-used', 2.0),)


def test_resource_finding_guidance_includes_objective_category_scale(
    tmp_path: Path,
) -> None:
    instance = _make_rule_instance(tmp_path, 'penalty')
    objective = instance.objective_data.objectives[0]._replace(
        penalty_scales={
            **instance.objective_data.objectives[0].penalty_scales,
            'vehicle_availability': 3.0,
        }
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicle_availability=(VehicleAvailabilityRecord('vehicle-1', 2.0, 4.0),)
        ),
        objective_data=instance.objective_data._replace(objectives=(objective,)),
    )

    result = VrpApplicationEvaluator(instance).evaluate(
        _five_minute_route(), build_vrp_objective_declaration(instance)
    )

    assert result.objective_values == (16.0,)
    analysis = cast(VrpOperatorAnalysis, result.analysis)
    finding = next(
        item
        for item in analysis.resource_findings
        if item.kind == 'vehicle_availability'
    )
    assert finding.measure == 3.0
    assert finding.penalty_by_objective == (('vehicles-used', 9.0),)


def test_hos_callback_measures_are_penalized_once_per_scenario(tmp_path: Path) -> None:
    instance = _make_rule_instance(tmp_path, 'penalty')
    settings = instance.settings._replace(
        active_simple_rules=frozenset(),
        hos_regime_id='example-hos',
        hos_regime_version='1',
    )
    objective = instance.objective_data.objectives[0]._replace(
        penalty_scales={
            **instance.objective_data.objectives[0].penalty_scales,
            'hos_rule': 3.0,
        }
    )
    objective_data = instance.objective_data._replace(
        objectives=(objective,),
        penalty_functions=(
            *instance.objective_data.penalty_functions,
            PenaltyFunctionSpec(
                'vehicles-used',
                'hos_rule',
                'driving-cap',
                'linear',
                FrozenTomlTable({'rate': 2.0}),
            ),
        ),
    )
    instance = instance._replace(
        settings=settings,
        objective_data=objective_data,
        hos_configuration=FrozenTomlTable({'period': 'daily'}),
        hos_rule_definitions=(HosRuleDefinition('driving-cap', ('driver-1',)),),
    )
    calls: list[str] = []

    def validate_configuration(
        request: HosConfigurationRequest,
    ) -> tuple[HosRuleDefinition, ...]:
        return (HosRuleDefinition('driving-cap', ('driver-1',)),)

    def evaluate_scenario(
        request: HosScenarioRequest,
    ) -> tuple[HosRuleFinding, ...]:
        calls.append(request.scenario.scenario_id)
        return (HosRuleFinding('driving-cap', 'driver-1', 1.5),)

    registry = VrpExtensionRegistry(
        'test.hos.registry',
        'test-hos-registry-v1',
        hos_regimes=(
            HosRegimeRegistration(
                'example-hos',
                '1',
                'test.example-hos',
                'test-example-hos-v1',
                validate_configuration,
                evaluate_scenario,
            ),
        ),
    )

    result = VrpApplicationEvaluator(instance, extension_registry=registry).evaluate(
        VrpSolution((), ()), build_vrp_objective_declaration(instance)
    )

    assert result.feasible is True
    assert result.objective_values == (9.0,)
    assert calls == ['scenario-000001']
    analysis = cast(VrpOperatorAnalysis, result.analysis)
    finding = next(item for item in analysis.resource_findings if item.kind == 'hos')
    assert finding.measure == 1.5
    assert finding.penalty_by_objective == (('vehicles-used', 9.0),)


def test_compliance_treatment_makes_simple_daily_work_excess_infeasible(
    tmp_path: Path,
) -> None:
    instance = _make_rule_instance(tmp_path, 'compliance')
    result = VrpApplicationEvaluator(instance).evaluate(
        _five_minute_route(), build_vrp_objective_declaration(instance)
    )

    assert result.feasible is False
    assert result.objective_values == (inf,)
    analysis = cast(VrpOperatorAnalysis, result.analysis)
    assert analysis.hard_failure is not None
    assert analysis.hard_failure.kind == 'compliance_rule'
    assert analysis.hard_failure.scenario_id == 'scenario-000001'
    assert not analysis.is_complete
    finding = next(
        item for item in analysis.resource_findings if item.kind == 'driver_work'
    )
    assert finding.measure == 2.0
