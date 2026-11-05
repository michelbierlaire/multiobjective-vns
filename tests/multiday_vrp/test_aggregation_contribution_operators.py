"""Tests for scenario-aggregation contribution Operators.

Created: 2026-11-04T09:03:56+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.application import build_vrp_application
from multiobjective_vns.multiday_vrp.evaluation.evaluator import (
    VrpApplicationEvaluator,
)
from multiobjective_vns.multiday_vrp.evaluation.records import (
    ScenarioObjectiveCost,
    VrpOperatorAnalysis,
)
from multiobjective_vns.multiday_vrp.extensions import (
    ArcTraversalEvaluation,
    LegScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    ScenarioRecord,
    VrpFeatureProfile,
)
from multiobjective_vns.multiday_vrp.operators.aggregation_contribution import (
    AggregationContributionOperator,
)
from multiobjective_vns.multiday_vrp.operators.catalog import VrpOperatorId
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 50000)

    def random(self) -> float:
        return next(self._draws)


def _two_scenario_instance(instance: LoadedVrpInstance) -> LoadedVrpInstance:
    original_bank = instance.scenario_bank
    scenario_ids = ('scenario-000001', 'scenario-000002')
    scenarios = (
        ScenarioRecord(scenario_ids[0], 0.8),
        ScenarioRecord(scenario_ids[1], 0.2),
    )
    travel_times: dict[tuple[str, str, int, int], float] = {}
    for scenario_id in scenario_ids:
        for (
            _old_id,
            arc_id,
            day_index,
            boundary_index,
        ), value in original_bank.travel_times.items():
            if arc_id == 'a-b':
                value = 1.0 if scenario_id == scenario_ids[0] else 10.0
            travel_times[(scenario_id, arc_id, day_index, boundary_index)] = value
    bank = original_bank._replace(
        scenarios=scenarios,
        travel_times=MappingProxyType(travel_times),
    )
    profile = VrpFeatureProfile(
        frozenset({*instance.feature_profile.active_features, 'scenario_aggregation'})
    )
    return instance._replace(scenario_bank=bank, feature_profile=profile)


def _assessment(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    solution: VrpSolution,
    base: VrpSolution,
) -> ApplicationEvaluation:
    changed_path = solution.routes[0].legs[0] != base.routes[0].legs[0]
    changed_time = solution.routes[0].start_time != base.routes[0].start_time
    improved = changed_path or changed_time
    scenario_values = (0.5, 5.0) if improved else (1.0, 10.0)
    scenarios: list[VrpScenarioEvaluation] = []
    costs: list[ScenarioObjectiveCost] = []
    objective_id = str(objectives.objectives[0].objective_id)
    aggregate = 0.0
    for scenario_index, scenario in enumerate(instance.scenario_bank.scenarios):
        value = scenario_values[scenario_index]
        aggregate += scenario.probability * value
        costs.append(ScenarioObjectiveCost(objective_id, scenario.scenario_id, value))
        vehicle_days = []
        for route_index, route in enumerate(solution.routes):
            legs = []
            for leg_index, leg in enumerate(route.legs):
                travel_time = (
                    (1.0, 10.0)[scenario_index]
                    if route_index == 0 and leg_index == 0
                    else 1.0
                )
                traversals = (
                    ArcTraversalEvaluation(
                        leg.arc_ids[0], 0.0, travel_time, travel_time
                    ),
                )
                legs.append(LegScenarioEvaluation(leg_index, traversals))
            vehicle_days.append(
                VehicleDayScenarioEvaluation(
                    route.vehicle_id,
                    route.day_index,
                    0.0,
                    100.0,
                    (),
                    tuple(legs),
                )
            )
        scenarios.append(
            VrpScenarioEvaluation(
                scenario.scenario_id,
                scenario.probability,
                tuple(vehicle_days),
            )
        )
    analysis = VrpOperatorAnalysis(
        True,
        tuple(scenarios),
        (),
        tuple(costs),
    )
    return ApplicationEvaluation(True, True, (aggregate,), analysis)


def _context(solution: VrpSolution) -> CandidateContext:
    return CandidateContext(
        solution,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )


def _custom_assessor(
    base: VrpSolution, calls: list[tuple[str, ...]]
) -> VrpPlanAssessor:
    def assess(
        instance: LoadedVrpInstance,
        objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        calls.append(
            tuple(item.scenario_id for item in instance.scenario_bank.scenarios)
        )
        return _assessment(instance, objectives, solution, base)

    return cast(VrpPlanAssessor, assess)


def test_scenario_contribution_path_targets_variable_leg_and_reweights_scenarios(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _two_scenario_instance(instance)
    calls: list[tuple[str, ...]] = []
    operator = AggregationContributionOperator(
        instance,
        objectives,
        _custom_assessor(base, calls),
        objective_id=objectives.objectives[0].objective_id,
        move_family='path',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert candidate.routes[0].legs[0] != base.routes[0].legs[0]
    assert ('scenario-000001',) in calls
    assert ('scenario-000002',) in calls


def test_scenario_contribution_timing_changes_an_upstream_field(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _two_scenario_instance(instance)
    routes = list(base.routes)
    routes[0] = replace(routes[0], start_time=50.0)
    base = replace(base, routes=tuple(routes))
    calls: list[tuple[str, ...]] = []
    operator = AggregationContributionOperator(
        instance,
        objectives,
        _custom_assessor(base, calls),
        objective_id=objectives.objectives[0].objective_id,
        move_family='timing',
        max_neighborhood_size=1,
        time_step=1.0,
        max_candidate_alternatives=8,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    candidate = cast(VrpSolution, result.candidate.solution)
    assert candidate.routes[0].start_time in {49.0, 51.0}
    assert ('scenario-000001',) in calls
    assert ('scenario-000002',) in calls


def test_application_assessor_uses_leave_one_scenario_instances(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    instance, _objectives, base = _test_plan(tmp_path)
    instance = _two_scenario_instance(instance)
    base = replace(base, routes=(base.routes[0],))
    seen: list[tuple[str, ...]] = []
    original_evaluate = VrpApplicationEvaluator.evaluate

    def recording_evaluate(
        evaluator: VrpApplicationEvaluator,
        solution: object,
        objectives: ObjectiveDeclaration,
    ) -> ApplicationEvaluation:
        seen.append(
            tuple(
                item.scenario_id for item in evaluator._instance.scenario_bank.scenarios
            )
        )
        return original_evaluate(evaluator, solution, objectives)

    monkeypatch.setattr(VrpApplicationEvaluator, 'evaluate', recording_evaluate)
    application = build_vrp_application(instance)
    registration = next(
        item
        for item in application.operators
        if cast(VrpOperatorId, item.operator_id).operator_spec_id
        == 'vrp.scenario_aggregation.aggregation_contribution_path_repair'
    )
    assert isinstance(registration.operator, AggregationContributionOperator)

    result = registration.operator.call(_context(base))

    assert result.m == 1
    assert ('scenario-000001',) in seen
    assert ('scenario-000002',) in seen
