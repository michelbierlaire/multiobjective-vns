"""Tests for the VRP ApplicationEvaluator contract.

Created: 2026-11-03T18:40:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import inf
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp import (
    VrpApplicationEvaluator,
    VrpExtensionRegistry,
    build_vrp_objective_declaration,
    load_vrp_instance,
)
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    BaseMeasureRegistration,
    BaseMeasureRequest,
)
from multiobjective_vns.multiday_vrp.solution import (
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveFunction
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .input_fixtures import write_minimal_vrp_bundle


def _add_destination_depot(root: Path) -> Path:
    manifest = write_minimal_vrp_bundle(root)
    instance_dir = manifest.parent
    (instance_dir / 'depots.csv').write_text(
        'depot_id,node_id\ndepot-1,node-a\ndepot-2,node-b\n', encoding='utf-8'
    )
    (instance_dir / 'vehicle-overnight-depots.csv').write_text(
        'vehicle_id,depot_id\nvehicle-1,depot-1\nvehicle-1,depot-2\n',
        encoding='utf-8',
    )
    return manifest


def _route(start_time: float = 0.0) -> VrpSolution:
    return VrpSolution(
        routes=(
            VehicleDayRoute(
                vehicle_id='vehicle-1',
                day_index=1,
                overnight_depot_id='depot-2',
                start_time=start_time,
                driver_id='driver-1',
                stops=(),
                legs=(RouteLeg(('arc-1',)),),
            ),
        ),
        transfer_steps=(),
    )


def _set_base_measure(manifest: Path, base_measure_id: str) -> None:
    path = manifest.parent / 'objectives.toml'
    contents = path.read_text(encoding='utf-8')
    path.write_text(
        contents.replace(
            "base_measure_id = 'vehicles_used'",
            f"base_measure_id = '{base_measure_id}'",
        ),
        encoding='utf-8',
    )


def test_empty_plan_returns_complete_valid_evaluation(tmp_path: Path) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))
    evaluator = VrpApplicationEvaluator(instance)

    result = evaluator.evaluate(
        VrpSolution((), ()), build_vrp_objective_declaration(instance)
    )

    assert isinstance(result, ApplicationEvaluation)
    assert result.representation_valid is True
    assert result.feasible is True
    assert result.objective_values == (0.0,)
    assert result.analysis is not None
    analysis = cast(VrpOperatorAnalysis, result.analysis)
    assert analysis.is_complete
    assert len(analysis.scenario_evaluations) == 1
    assert analysis.scenario_objective_costs[0].value == 0.0


def test_route_travel_is_scored_with_the_declared_builtin_measure(
    tmp_path: Path,
) -> None:
    manifest = _add_destination_depot(tmp_path)
    _set_base_measure(manifest, 'total_travel_time')
    instance = load_vrp_instance(manifest)
    evaluator = VrpApplicationEvaluator(instance)

    result = evaluator.evaluate(_route(), build_vrp_objective_declaration(instance))

    assert result.representation_valid
    assert result.feasible is True
    assert result.objective_values == (5.0,)
    assert result.analysis is not None
    analysis = cast(VrpOperatorAnalysis, result.analysis)
    assert analysis.scenario_objective_costs[0].value == 5.0


def test_malformed_representation_returns_invalid_without_objectives(
    tmp_path: Path,
) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))
    evaluator = VrpApplicationEvaluator(instance)
    malformed = VrpSolution(
        routes=(
            VehicleDayRoute(
                vehicle_id='missing-vehicle',
                day_index=1,
                overnight_depot_id='depot-1',
                start_time=0.0,
                driver_id='driver-1',
                stops=(),
                legs=(RouteLeg(()),),
            ),
        ),
        transfer_steps=(),
    )

    result = evaluator.evaluate(malformed, build_vrp_objective_declaration(instance))

    assert result.representation_valid is False
    assert result.feasible is None
    assert result.objective_values is None


def test_hard_daily_return_failure_assigns_positive_infinity_to_each_objective(
    tmp_path: Path,
) -> None:
    manifest = _add_destination_depot(tmp_path)
    _set_base_measure(manifest, 'total_travel_time')
    instance = load_vrp_instance(manifest)
    evaluator = VrpApplicationEvaluator(instance)

    result = evaluator.evaluate(
        _route(1438.0), build_vrp_objective_declaration(instance)
    )

    assert result.representation_valid
    assert result.feasible is False
    assert result.objective_values == (inf,)
    assert result.analysis is not None
    analysis = cast(VrpOperatorAnalysis, result.analysis)
    assert not analysis.is_complete
    assert analysis.hard_failure is not None
    assert analysis.hard_failure.kind == 'scenario_constraint'
    assert analysis.hard_failure.scenario_id == 'scenario-000001'


def test_custom_base_measure_receives_the_completed_scenario_trace(
    tmp_path: Path,
) -> None:
    manifest = _add_destination_depot(tmp_path)
    _set_base_measure(manifest, 'custom_total_arcs')
    seen: list[BaseMeasureRequest] = []

    def validate_parameters(request: object) -> None:
        return None

    def evaluate(request: BaseMeasureRequest) -> float:
        seen.append(request)
        return float(
            sum(
                len(leg.arc_traversals)
                for day in request.scenario.vehicle_days
                for leg in day.legs
            )
        )

    registry = VrpExtensionRegistry(
        registry_id='test.vrp.extensions',
        configuration_id='test-vrp-extensions-v1',
        base_measures=(
            BaseMeasureRegistration(
                extension_id='custom_total_arcs',
                component_id='test.custom-total-arcs',
                configuration_id='test-custom-total-arcs-v1',
                validate_parameters=validate_parameters,
                evaluate=evaluate,
            ),
        ),
    )
    instance = load_vrp_instance(manifest, extension_registry=registry)
    evaluator = VrpApplicationEvaluator(instance, extension_registry=registry)

    result = evaluator.evaluate(_route(), build_vrp_objective_declaration(instance))

    assert result.objective_values == (1.0,)
    assert len(seen) == 1
    assert seen[0].scenario.scenario_id == 'scenario-000001'
    assert seen[0].scenario.vehicle_days[0].legs[0].arc_traversals[0].arc_id == 'arc-1'


def test_objective_order_must_match_loaded_instance(tmp_path: Path) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))
    evaluator = VrpApplicationEvaluator(instance)

    class WrongOrder:
        objective_id = 'other'
        component_id = 'test.other'
        configuration_id = 'test-other-v1'

        def compare(self, left: object, right: object) -> object:
            return None

        def format(self, value: object) -> str:
            return str(value)

        def __str__(self) -> str:
            return 'other'

    from multiobjective_vns.objectives import ObjectiveDeclaration

    with pytest.raises(ValueError, match='order must match'):
        evaluator.evaluate(
            VrpSolution((), ()),
            ObjectiveDeclaration((cast(ObjectiveFunction, WrongOrder()),)),
        )
