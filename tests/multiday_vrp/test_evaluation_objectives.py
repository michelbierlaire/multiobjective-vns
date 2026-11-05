"""Tests for VRP objective measures, penalties, and scenario costs.

Created: 2026-11-03T20:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from math import fsum
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp import (
    PenaltyAggregationRegistration,
    ScenarioAggregationRegistration,
    VrpApplicationEvaluator,
    VrpExtensionRegistry,
    build_vrp_objective_declaration,
    load_vrp_instance,
)
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    ExtensionParametersRequest,
    PenaltyAggregationRequest,
    ScenarioAggregationRequest,
)
from multiobjective_vns.multiday_vrp.input_records import (
    AggregationSpec,
    EventRecord,
    EventTypeRecord,
    FrozenTomlTable,
    ObjectiveEventTypeRecord,
    ObjectiveOmissionPenaltyRecord,
    PackageTimeWindowRecord,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)

from .input_fixtures import write_minimal_vrp_bundle
from .test_evaluation_capacity import _package_instance, _package_plan


def test_window_penalty_uses_service_start_and_finish_with_planned_wait(
    tmp_path: Path,
) -> None:
    instance = _package_instance(tmp_path, package_loads=(1.0,), vehicle_capacity=2.0)
    package = instance.package_data.packages[0]._replace(pickup_service_duration=5.0)
    instance = instance._replace(
        package_data=instance.package_data._replace(
            packages=(package,),
            package_time_windows=(
                PackageTimeWindowRecord('package-1', 'pickup', 1, 10.0, 15.0),
                PackageTimeWindowRecord('package-1', 'delivery', 1, 0.0, 1440.0),
            ),
        )
    )
    solution = _package_plan(('pickup', 'package-1'), ('delivery', 'package-1'))
    pickup, delivery = solution.routes[0].stops
    assert isinstance(pickup, ActivityStop)
    solution = VrpSolution(
        routes=(
            replace(
                solution.routes[0],
                stops=(replace(pickup, pre_service_wait=10.0), delivery),
            ),
        ),
        transfer_steps=(),
    )

    on_time = VrpApplicationEvaluator(instance).evaluate(
        solution, build_vrp_objective_declaration(instance)
    )
    late_solution = VrpSolution(
        routes=(
            replace(
                solution.routes[0],
                stops=(replace(pickup, pre_service_wait=15.0), delivery),
            ),
        ),
        transfer_steps=(),
    )
    late = VrpApplicationEvaluator(instance).evaluate(
        late_solution, build_vrp_objective_declaration(instance)
    )

    assert on_time.objective_values == (1.0,)
    assert late.objective_values == (6.0,)
    analysis = cast(VrpOperatorAnalysis, on_time.analysis)
    assessment = analysis.scenario_evaluations[0].vehicle_days[0].stops[0]
    assert assessment.window_assessments_by_objective[0].early_violation == 0.0
    assert assessment.window_assessments_by_objective[0].late_violation == 0.0


def test_optional_omissions_add_the_declared_per_objective_cost(
    tmp_path: Path,
) -> None:
    instance = _package_instance(tmp_path, package_loads=(1.0,), vehicle_capacity=2.0)
    instance = instance._replace(
        settings=instance.settings._replace(service_coverage_mode='penalized_omission'),
        objective_data=instance.objective_data._replace(
            objective_omission_penalties=(
                ObjectiveOmissionPenaltyRecord(
                    'vehicles-used', 'pickup', 'package-1', 3.0
                ),
                ObjectiveOmissionPenaltyRecord(
                    'vehicles-used', 'delivery', 'package-1', 4.0
                ),
            )
        ),
    )

    result = VrpApplicationEvaluator(instance).evaluate(
        VrpSolution((), ()), build_vrp_objective_declaration(instance)
    )

    assert result.representation_valid is True
    assert result.feasible is True
    assert result.objective_values == (7.0,)


def test_event_count_observes_node_visits_and_arc_traversals(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    instance_dir = manifest.parent
    (instance_dir / 'depots.csv').write_text(
        'depot_id,node_id\ndepot-1,node-a\ndepot-2,node-b\n', encoding='utf-8'
    )
    (instance_dir / 'vehicle-overnight-depots.csv').write_text(
        'vehicle_id,depot_id\nvehicle-1,depot-1\nvehicle-1,depot-2\n',
        encoding='utf-8',
    )
    instance = load_vrp_instance(manifest)
    objective = instance.objective_data.objectives[0]._replace(
        base_measure_id='event_count'
    )
    instance = instance._replace(
        objective_data=instance.objective_data._replace(objectives=(objective,)),
        event_data=instance.event_data._replace(
            event_types=(
                EventTypeRecord('node-event', 'node'),
                EventTypeRecord('arc-event', 'arc'),
            ),
            events=(
                EventRecord('start-light', 'node-event', 'node-a'),
                EventRecord('end-light-1', 'node-event', 'node-b'),
                EventRecord('end-light-2', 'node-event', 'node-b'),
                EventRecord('work-zone', 'arc-event', 'arc-1'),
            ),
            objective_event_types=(
                ObjectiveEventTypeRecord(objective.objective_id, 'node-event'),
                ObjectiveEventTypeRecord(objective.objective_id, 'arc-event'),
            ),
        ),
    )
    solution = VrpSolution(
        routes=(
            VehicleDayRoute(
                'vehicle-1',
                1,
                'depot-2',
                0.0,
                'driver-1',
                (),
                (RouteLeg(('arc-1',)),),
            ),
        ),
        transfer_steps=(),
    )

    result = VrpApplicationEvaluator(instance).evaluate(
        solution, build_vrp_objective_declaration(instance)
    )

    assert result.objective_values == (4.0,)


def test_registered_scenario_aggregation_receives_complete_scenario_costs(
    tmp_path: Path,
) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    objective_path = manifest.parent / 'objectives.toml'
    objective_path.write_text(
        objective_path.read_text(encoding='utf-8').replace(
            "aggregation = { id = 'weighted_mean' }",
            "aggregation = { id = 'custom_offset' }",
        ),
        encoding='utf-8',
    )
    seen: list[ScenarioAggregationRequest] = []

    def validate_parameters(request: ExtensionParametersRequest) -> None:
        assert request.extension_id == 'custom_offset'

    def aggregate(request: ScenarioAggregationRequest) -> float:
        seen.append(request)
        return request.scenario_values[0].value + 2.0

    registry = VrpExtensionRegistry(
        registry_id='test.vrp.extensions',
        configuration_id='test-vrp-extensions-v1',
        scenario_aggregations=(
            ScenarioAggregationRegistration(
                extension_id='custom_offset',
                component_id='test.custom-offset',
                configuration_id='test-custom-offset-v1',
                validate_parameters=validate_parameters,
                aggregate=aggregate,
            ),
        ),
    )
    instance = load_vrp_instance(manifest, extension_registry=registry)
    evaluator = VrpApplicationEvaluator(instance, extension_registry=registry)

    result = evaluator.evaluate(
        VrpSolution((), ()), build_vrp_objective_declaration(instance)
    )

    assert result.objective_values == (2.0,)
    assert len(seen) == 1
    assert seen[0].scenario_values[0].value == 0.0
    assert seen[0].scenario_values[0].scenario_id == 'scenario-000001'


def test_registered_penalty_aggregation_receives_scaled_category_findings(
    tmp_path: Path,
) -> None:
    instance = _package_instance(tmp_path, package_loads=(1.0,), vehicle_capacity=2.0)
    objective = instance.objective_data.objectives[0]._replace(
        penalty_aggregation=AggregationSpec(
            'custom_penalty_sum', None, FrozenTomlTable({})
        ),
        penalty_scales={
            **instance.objective_data.objectives[0].penalty_scales,
            'early_window': 2.0,
        },
    )
    instance = instance._replace(
        objective_data=instance.objective_data._replace(objectives=(objective,)),
        package_data=instance.package_data._replace(
            package_time_windows=(
                PackageTimeWindowRecord('package-1', 'pickup', 1, 10.0, 20.0),
                PackageTimeWindowRecord('package-1', 'delivery', 1, 0.0, 1440.0),
            )
        ),
    )
    seen: list[PenaltyAggregationRequest] = []

    def validate_parameters(request: ExtensionParametersRequest) -> None:
        assert request.extension_id == 'custom_penalty_sum'

    def aggregate(request: PenaltyAggregationRequest) -> float:
        seen.append(request)
        return fsum(
            category.scale * fsum(category.values)
            for category in request.category_values
        )

    registry = VrpExtensionRegistry(
        registry_id='test.vrp.extensions',
        configuration_id='test-vrp-extensions-v1',
        penalty_aggregations=(
            PenaltyAggregationRegistration(
                extension_id='custom_penalty_sum',
                component_id='test.custom-penalty-sum',
                configuration_id='test-custom-penalty-sum-v1',
                validate_parameters=validate_parameters,
                aggregate=aggregate,
            ),
        ),
    )
    solution = _package_plan(('pickup', 'package-1'), ('delivery', 'package-1'))

    result = VrpApplicationEvaluator(instance, extension_registry=registry).evaluate(
        solution, build_vrp_objective_declaration(instance)
    )

    assert result.objective_values == (21.0,)
    assert len(seen) == 1
    early_window = next(
        item for item in seen[0].category_values if item.category_id == 'early_window'
    )
    assert early_window.scale == 2.0
    assert early_window.values == (10.0, 0.0)
