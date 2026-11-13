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

from multiobjective_vns.candidate_batch import CandidateBatchAdapter
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
from multiobjective_vns.multiday_vrp.operators import (
    aggregation_contribution as aggregation_module,
)
from multiobjective_vns.multiday_vrp.operators.aggregation_contribution import (
    AggregationContributionOperator,
    _select_leg,
)
from multiobjective_vns.multiday_vrp.operators.catalog import VrpOperatorId
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.path_search import PathSearchIndex
from multiobjective_vns.multiday_vrp.solution import DepotOnlyStop, VrpSolution
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


class _LastRandomSource:
    def random(self) -> float:
        return 0.999999


class _SequenceRandomSource:
    def __init__(self, draws: tuple[float, ...]) -> None:
        self._draws = iter(draws)

    def random(self) -> float:
        return next(self._draws, 0.0)


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

    assert result.candidates[0].m == 1
    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert candidate.routes[0].legs[0] != base.routes[0].legs[0]
    assert ('scenario-000001',) in calls
    assert ('scenario-000002',) in calls


def test_scenario_contribution_path_returns_ranked_batch_even_if_base_is_better(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _two_scenario_instance(instance)
    scores = {
        'candidate-a': 3.0,
        'candidate-b': 1.0,
        'candidate-c': 1.0,
        'candidate-d': 2.0,
    }

    def sample_paths(
        _instance: object,
        _origin: str,
        _destination: str,
        **_kwargs: object,
    ) -> tuple[tuple[str, ...], ...]:
        return tuple((arc_id,) for arc_id in scores)

    def assessor(
        assessed_instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        assessment = _assessment(assessed_instance, declaration, solution, base)
        scenario_ids = tuple(
            item.scenario_id for item in assessed_instance.scenario_bank.scenarios
        )
        if len(scenario_ids) == 1:
            value = 100.0 if scenario_ids[0] == 'scenario-000001' else 10.0
        elif solution.routes[0].legs[0].arc_ids[0] == 'a-b':
            value = 0.0
        else:
            value = scores[solution.routes[0].legs[0].arc_ids[0]]
        return replace(assessment, objective_values=(value,))

    monkeypatch.setattr(aggregation_module, 'sample_loopless_paths', sample_paths)
    operator = AggregationContributionOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        move_family='path',
        max_neighborhood_size=1,
        max_candidate_alternatives=4,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            max_candidates=3,
        )
    )

    candidates = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(candidates) == 3
    assert tuple(proposal.m for proposal in result.candidates) == (1, 1, 1)
    assert tuple(route.routes[0].legs[0].arc_ids[0] for route in candidates) == (
        'candidate-b',
        'candidate-c',
        'candidate-d',
    )


def test_scenario_contribution_path_applies_size_sequentially(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _two_scenario_instance(instance)

    def sample_paths(
        _instance: object,
        origin: str,
        destination: str,
        **_kwargs: object,
    ) -> tuple[tuple[str, ...], ...]:
        return (
            (f'{origin}-{destination}-alternative-a',),
            (f'{origin}-{destination}-alternative-b',),
        )

    monkeypatch.setattr(aggregation_module, 'sample_loopless_paths', sample_paths)
    operator = AggregationContributionOperator(
        instance,
        objectives,
        _custom_assessor(base, []),
        objective_id=objectives.objectives[0].objective_id,
        move_family='path',
        max_neighborhood_size=2,
        max_candidate_alternatives=2,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            max_candidates=2,
        )
    )

    assert len(result.candidates) == 2
    assert all(proposal.m == 2 for proposal in result.candidates)
    assert all(
        cast(VrpSolution, proposal.candidate.solution).routes[0].legs[0].arc_ids
        == ('a-b-alternative-a',)
        for proposal in result.candidates
    )
    assert all(
        cast(VrpSolution, proposal.candidate.solution).routes[0].legs[1].arc_ids
        != base.routes[0].legs[1].arc_ids
        for proposal in result.candidates
    )


def test_scenario_contribution_ignores_incomplete_arc_traces_when_selecting_leg(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _two_scenario_instance(instance)
    assessment = _assessment(instance, objectives, base, base)
    analysis = cast(VrpOperatorAnalysis, assessment.analysis)
    scenarios = list(analysis.scenario_evaluations)
    scenario = scenarios[0]
    first_vehicle_day = scenario.vehicle_days[0]
    first_leg = first_vehicle_day.legs[0]
    incomplete_leg = first_leg._replace(arc_traversals=())
    first_vehicle_day = first_vehicle_day._replace(
        legs=(incomplete_leg, *first_vehicle_day.legs[1:])
    )
    scenarios[0] = scenario._replace(
        vehicle_days=(first_vehicle_day, *scenario.vehicle_days[1:])
    )
    analysis = replace(analysis, scenario_evaluations=tuple(scenarios))
    random_source = cast(RandomSource, _LastRandomSource())
    context = CandidateContext(base, 1, False, None, random_source)

    selected = _select_leg(
        instance,
        base,
        analysis,
        'scenario-000001',
        set(),
        context,
    )

    assert selected == (1, len(base.routes[1].legs) - 1)


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

    assert result.candidates[0].m == 1
    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert candidate.routes[0].start_time in {49.0, 51.0}
    assert ('scenario-000001',) in calls
    assert ('scenario-000002',) in calls


def test_scenario_contribution_timing_returns_ranked_candidates(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _two_scenario_instance(instance)
    routes = list(base.routes)
    routes[0] = replace(routes[0], start_time=50.0)
    base = replace(base, routes=tuple(routes))

    def assessor(
        assessed_instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        assessment = _assessment(assessed_instance, declaration, solution, base)
        scenario_ids = tuple(
            item.scenario_id for item in assessed_instance.scenario_bank.scenarios
        )
        if len(scenario_ids) == 1:
            value = 100.0 if scenario_ids[0] == 'scenario-000001' else 10.0
        else:
            value = {49.0: 3.0, 50.0: 0.0, 51.0: 1.0}[solution.routes[0].start_time]
        return replace(assessment, objective_values=(value,))

    operator = AggregationContributionOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        move_family='timing',
        max_neighborhood_size=1,
        time_step=1.0,
        max_candidate_alternatives=4,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _SequenceRandomSource((0.0, 0.9, 0.0, 0.9))),
        max_candidates=2,
    )

    result = operator.call(context)

    assert len(result.candidates) == 2
    assert tuple(proposal.m for proposal in result.candidates) == (1, 1)
    assert tuple(
        cast(VrpSolution, proposal.candidate.solution).routes[0].start_time
        for proposal in result.candidates
    ) == (51.0, 49.0)


def test_scenario_contribution_timing_can_revisit_a_changed_field(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _two_scenario_instance(instance)
    routes = list(base.routes)
    routes[0] = replace(routes[0], start_time=50.0)
    base = replace(base, routes=tuple(routes))

    def assessor(
        assessed_instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        assessment = _assessment(assessed_instance, declaration, solution, base)
        scenario_ids = tuple(
            item.scenario_id for item in assessed_instance.scenario_bank.scenarios
        )
        value = (
            (100.0 if scenario_ids[0] == 'scenario-000001' else 10.0)
            if len(scenario_ids) == 1
            else solution.routes[0].start_time
        )
        return replace(assessment, objective_values=(value,))

    operator = AggregationContributionOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        move_family='timing',
        max_neighborhood_size=2,
        time_step=1.0,
        max_candidate_alternatives=1,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, _SequenceRandomSource((0.0, 0.0))),
    )

    result = operator.call(context)

    assert len(result.candidates) == 1
    proposal = result.candidates[0]
    assert proposal.m == 2
    assert cast(VrpSolution, proposal.candidate.solution).routes[0].start_time == 48.0


def test_scenario_contribution_timing_can_change_an_upstream_stop_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _two_scenario_instance(instance)
    routes = list(base.routes)
    first_stop = routes[0].stops[0]
    assert isinstance(first_stop, DepotOnlyStop)
    routes[0] = replace(
        routes[0],
        stops=(replace(first_stop, wait=3.0), *routes[0].stops[1:]),
    )
    base = replace(base, routes=tuple(routes))

    def assessor(
        assessed_instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        assessment = _assessment(assessed_instance, declaration, solution, base)
        scenario_ids = tuple(
            item.scenario_id for item in assessed_instance.scenario_bank.scenarios
        )
        if len(scenario_ids) == 1:
            value = 100.0 if scenario_ids[0] == 'scenario-000001' else 10.0
        else:
            stop = solution.routes[0].stops[0]
            assert isinstance(stop, DepotOnlyStop)
            value = {2.0: 1.0, 3.0: 0.0, 4.0: 2.0}[stop.wait]
        return replace(assessment, objective_values=(value,))

    monkeypatch.setattr(aggregation_module, '_select_leg', lambda *_args: (0, 1))
    operator = AggregationContributionOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        move_family='timing',
        max_neighborhood_size=1,
        time_step=1.0,
        max_candidate_alternatives=1,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _SequenceRandomSource((0.5, 0.0))),
    )

    result = operator.call(context)

    assert len(result.candidates) == 1
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    changed_stop = candidate.routes[0].stops[0]
    assert isinstance(changed_stop, DepotOnlyStop)
    assert changed_stop.wait == 2.0


def test_scenario_contribution_path_reuses_path_search_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _two_scenario_instance(instance)
    index = PathSearchIndex(frozenset(), {})
    build_calls = 0
    received_indexes: list[PathSearchIndex | None] = []

    def build_index(_instance: object) -> PathSearchIndex:
        nonlocal build_calls
        build_calls += 1
        return index

    def sample_paths(
        _instance: object,
        _origin: str,
        _destination: str,
        *,
        path_search_index: PathSearchIndex | None = None,
        **_kwargs: object,
    ) -> tuple[tuple[str, ...], ...]:
        received_indexes.append(path_search_index)
        return (('alternative',),)

    monkeypatch.setattr(aggregation_module, 'build_path_search_index', build_index)
    monkeypatch.setattr(aggregation_module, 'sample_loopless_paths', sample_paths)
    operator = AggregationContributionOperator(
        instance,
        objectives,
        _custom_assessor(base, []),
        objective_id=objectives.objectives[0].objective_id,
        move_family='path',
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
    )

    operator.call(_context(base))
    operator.call(_context(base))

    assert build_calls == 1
    assert len(received_indexes) == 2
    assert all(received_index is index for received_index in received_indexes)


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
    assert isinstance(registration.operator, CandidateBatchAdapter)
    assert isinstance(registration.operator.delegate, AggregationContributionOperator)

    result = registration.operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert ('scenario-000001',) in seen
    assert ('scenario-000002',) in seen
