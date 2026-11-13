"""Tests for feature-guided timing Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from random import Random
from types import MappingProxyType
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.evaluation.evaluator import (
    VrpApplicationEvaluator,
)
from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    VrpObjectiveFunction,
)
from multiobjective_vns.multiday_vrp.evaluation.records import (
    HardFeasibilityFinding,
    ResourceFinding,
    VrpOperatorAnalysis,
)
from multiobjective_vns.multiday_vrp.extensions import (
    StopScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
    WindowScenarioAssessment,
)
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionTimeWindowRecord,
    LoadedVrpInstance,
    PersonnelAvailabilityRecord,
    ScenarioRecord,
)
from multiobjective_vns.multiday_vrp.operators import (
    feature_timing as feature_timing_module,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.feature_timing import (
    FeatureTimingOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteLeg,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
)
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    ProcessingStatus,
)

from .test_universal_route_order import _test_plan
from .test_vehicle_route_assignment import _service_plan


def _service_wait_problem(
    tmp_path: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, _original_objectives, solution = _service_plan(tmp_path)
    intervention_ids = tuple(
        intervention.intervention_id
        for intervention in instance.intervention_data.interventions
    )
    windows = tuple(
        InterventionTimeWindowRecord(intervention_id, 1, 1, 10.0, 20.0)
        for intervention_id in intervention_ids
    )
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            intervention_time_windows=windows
        ),
        time_window_violation_objective_id='window-penalty',
    )
    objectives = ObjectiveDeclaration(
        (
            VrpObjectiveFunction('window-penalty', 'test-window', 'test-window-v1'),
            VrpObjectiveFunction('secondary', 'test-secondary', 'test-secondary-v1'),
        )
    )
    return instance, objectives, solution


def _window_guidance_analysis(
    solution: VrpSolution,
    *,
    scenarios: tuple[tuple[str, float], ...] = (
        ('scenario-1', 0.5),
        ('scenario-2', 0.5),
    ),
    guidance_by_activity: dict[str, float] | None = None,
    scenario_guidance: dict[tuple[str, str], float] | None = None,
) -> VrpOperatorAnalysis:
    base_guidance = guidance_by_activity or {}
    overrides = scenario_guidance or {}
    scenario_records = []
    for scenario_id, probability in scenarios:
        vehicle_days = []
        for route in solution.routes:
            stops = []
            for stop_index, stop in enumerate(route.stops):
                assert isinstance(stop, ActivityStop)
                activity_id = stop.activity_ref.activity_id
                guidance = overrides.get(
                    (scenario_id, activity_id), base_guidance.get(activity_id, 0.0)
                )
                assessment = WindowScenarioAssessment(
                    'window-penalty', 1, 0.0, float(guidance), float(guidance)
                )
                stops.append(
                    StopScenarioEvaluation(
                        stop_index,
                        'activity',
                        route.vehicle_id,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        None,
                        None,
                        stop.activity_ref,
                        None,
                        (assessment,),
                    )
                )
            vehicle_days.append(
                VehicleDayScenarioEvaluation(
                    route.vehicle_id,
                    route.day_index,
                    route.start_time,
                    route.start_time,
                    tuple(stops),
                    (),
                )
            )
        scenario_records.append(
            VrpScenarioEvaluation(scenario_id, probability, tuple(vehicle_days))
        )
    return VrpOperatorAnalysis(True, tuple(scenario_records), (), ())


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


class _SeededRandomSource:
    def __init__(self, seed: int) -> None:
        self._random = Random(seed)

    def random(self) -> float:
        return self._random.random()


class _SequenceRandomSource:
    def __init__(self, draws: tuple[float, ...]) -> None:
        self._draws = draws
        self._position = 0

    def random(self) -> float:
        draw = self._draws[self._position]
        self._position += 1
        return draw


class _HighThenZeroRandomSource:
    def __init__(self) -> None:
        self._first = True

    def random(self) -> float:
        if self._first:
            self._first = False
            return 0.99
        return 0.0


def _profiled_plan(
    tmp_path: Path, variable_arc_ids: frozenset[str]
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _test_plan(tmp_path)
    travel_times = dict(instance.scenario_bank.travel_times)
    for key in tuple(travel_times):
        if key[1] in variable_arc_ids:
            travel_times[key] = float(5 + key[3] * 5)
    bank = instance.scenario_bank._replace(travel_times=MappingProxyType(travel_times))
    return instance._replace(scenario_bank=bank), objectives, base


def _scenario_differential_plan(
    tmp_path: Path, scenario_sensitive_arc_ids: frozenset[str]
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _test_plan(tmp_path)
    bank = instance.scenario_bank
    travel_times = dict(bank.travel_times)
    for _scenario_id, arc_id, day_index, boundary_index in tuple(travel_times):
        scenario_two_time = (
            float(5 + boundary_index * 5)
            if arc_id in scenario_sensitive_arc_ids
            else travel_times[('scenario-000001', arc_id, day_index, boundary_index)]
        )
        travel_times[('scenario-000002', arc_id, day_index, boundary_index)] = (
            scenario_two_time
        )
    bank = bank._replace(
        scenarios=(
            ScenarioRecord('scenario-000001', 0.5),
            ScenarioRecord('scenario-000002', 0.5),
        ),
        travel_times=MappingProxyType(travel_times),
    )
    return instance._replace(scenario_bank=bank), objectives, base


def test_scenario_differential_timing_targets_route_with_variable_travel(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _scenario_differential_plan(
        tmp_path, frozenset({'b-c'})
    )
    evaluator = VrpApplicationEvaluator(instance)

    def assessor(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        evaluated = evaluator.evaluate(solution, declaration)
        if not evaluated.representation_valid or evaluated.objective_values is None:
            return evaluated
        time_value = sum(
            route.start_time + sum(stop.wait for stop in route.stops)
            for route in solution.routes
        )
        return ApplicationEvaluation(
            evaluated.representation_valid,
            evaluated.feasible,
            (time_value,),
            evaluated.analysis,
        )

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='scenario_differential',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _SeededRandomSource(12)),
        max_candidates=4,
    )

    result = operator.call(context)

    assert len(result.candidates) == 3
    assert all(proposal.m == 1 for proposal in result.candidates)
    candidates = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(candidates)) == 3
    assert all(candidate.routes[1] == base.routes[1] for candidate in candidates)
    assert all(
        candidate.routes[0].start_time != base.routes[0].start_time
        or candidate.routes[0].stops[0].wait != base.routes[0].stops[0].wait
        for candidate in candidates
    )
    values = tuple(
        sum(
            route.start_time + sum(stop.wait for stop in route.stops)
            for route in candidate.routes
        )
        for candidate in candidates
    )
    assert values == tuple(sorted(values))


def test_scenario_differential_timing_falls_back_when_no_field_is_sensitive(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _scenario_differential_plan(tmp_path, frozenset())
    evaluator = VrpApplicationEvaluator(instance)
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda _instance, declaration, solution: evaluator.evaluate(
                solution, declaration
            ),
        ),
        mode='scenario_differential',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
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
            max_candidates=4,
        )
    )

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.routes[0].start_time != base.routes[0].start_time


def test_scenario_differential_timing_changes_distinct_fields_per_trial(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _scenario_differential_plan(
        tmp_path, frozenset({'b-c'})
    )
    evaluator = VrpApplicationEvaluator(instance)
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda _instance, declaration, solution: evaluator.evaluate(
                solution, declaration
            ),
        ),
        mode='scenario_differential',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=2,
        max_candidate_alternatives=16,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _SeededRandomSource(24)),
            max_candidates=4,
        )
    )

    assert result.candidates
    assert any(proposal.m == 2 for proposal in result.candidates)
    for proposal in result.candidates:
        assert 1 <= proposal.m <= 2


def test_scenario_differential_timing_respects_requested_feasibility(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _scenario_differential_plan(
        tmp_path, frozenset({'b-c'})
    )
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda *_args: ApplicationEvaluation(True, False, (1.0,)),
        ),
        mode='scenario_differential',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=1,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            True,
            None,
            cast(RandomSource, _ZeroRandomSource()),
        )
    )

    assert result.candidates == ()


def test_departure_profile_only_selects_moves_changing_an_arc_profile(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _profiled_plan(tmp_path, frozenset({'a-b'}))
    evaluator = VrpApplicationEvaluator(instance)
    assessments = 0

    def assessor(*args: object) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return evaluator.evaluate(cast(VrpSolution, args[2]), objectives)

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='departure_profile',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
        max_candidates=4,
    )

    result = operator.call(context)

    assert len(result.candidates) == 1
    assert result.candidates[0].m == 1
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.routes[0].start_time == 1.0
    assert changed.routes[1] == base.routes[1]
    # Profile sensitivity is computed from the scenario bank; the full
    # assessor is needed only once to rank the resulting complete trial.
    assert assessments == 1


def test_departure_profile_returns_empty_for_constant_arc_profiles(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _profiled_plan(tmp_path, frozenset())
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda *_args: ApplicationEvaluation(True, True, (1.0,)),
        ),
        mode='departure_profile',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )

    assert operator.call(context).candidates == ()


def test_departure_profile_returns_distinct_ranked_trials_up_to_context_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _profiled_plan(tmp_path, frozenset())
    # Make every network arc time-dependent so multiple independent timing
    # decisions can produce distinct profile-sensitive trials.
    travel_times = dict(instance.scenario_bank.travel_times)
    arc_ids = {arc.arc_id for arc in instance.network_data.arcs}
    for key in tuple(travel_times):
        if key[1] in arc_ids:
            travel_times[key] = float(5 + key[3] * 5)
    instance = instance._replace(
        scenario_bank=instance.scenario_bank._replace(
            travel_times=MappingProxyType(travel_times)
        )
    )
    evaluator = VrpApplicationEvaluator(instance)
    assessments = 0

    def assessor(
        assessment_instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        evaluated = evaluator.evaluate(solution, declaration)
        if not evaluated.representation_valid or evaluated.analysis is None:
            return evaluated
        analysis = cast(VrpOperatorAnalysis, evaluated.analysis)
        total_travel = sum(
            arc.travel_time
            for scenario in analysis.scenario_evaluations
            for vehicle_day in scenario.vehicle_days
            for leg in vehicle_day.legs
            for arc in leg.arc_traversals
        )
        return ApplicationEvaluation(
            evaluated.representation_valid,
            evaluated.feasible,
            (total_travel,),
            evaluated.analysis,
        )

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='departure_profile',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=2,
        max_candidate_alternatives=12,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, _SeededRandomSource(19)),
        max_candidates=4,
    )

    result = operator.call(context)

    assert len(result.candidates) == 4
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(solutions)) == len(solutions)
    assert all(proposal.m == 2 for proposal in result.candidates)
    assert assessments <= 12
    values_list: list[float] = []
    for solution in solutions:
        evaluation = assessor(instance, objectives, solution)
        assert evaluation.objective_values is not None
        values_list.append(cast(float, evaluation.objective_values[0]))
    values = tuple(values_list)
    assert values == tuple(sorted(values))


def test_departure_profile_respects_requested_feasibility(tmp_path: Path) -> None:
    instance, objectives, base = _profiled_plan(tmp_path, frozenset({'a-b'}))
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda *_args: ApplicationEvaluation(True, False, (1.0,)),
        ),
        mode='departure_profile',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )

    assert operator.call(context).candidates == ()


def test_availability_timing_targets_route_with_largest_finding_and_ranks_batch(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    objective_id = str(objectives.objectives[0].objective_id)
    findings = (
        ResourceFinding(
            'vehicle_availability',
            'vehicle-1',
            'scenario-1',
            'vehicle-1',
            1,
            None,
            1.0,
            ((objective_id, 1.5),),
        ),
        ResourceFinding(
            'vehicle_availability',
            'vehicle-1',
            'scenario-2',
            'vehicle-1',
            1,
            None,
            1.0,
            ((objective_id, 2.5),),
        ),
        ResourceFinding(
            'vehicle_availability',
            'vehicle-2',
            'scenario-1',
            'vehicle-2',
            1,
            None,
            1.0,
            ((objective_id, 3.0),),
        ),
    )
    analysis = VrpOperatorAnalysis(True, (), findings, ())
    assessments = 0

    def objective_value(solution: VrpSolution) -> float:
        return sum(
            route.start_time
            + sum(stop.wait + stop.pre_service_wait for stop in route.stops)
            for route in solution.routes
        )

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return ApplicationEvaluation(True, True, (objective_value(solution),), analysis)

    base_evaluation = ApplicationEvaluation(
        True, True, (objective_value(base),), analysis
    )
    stored_evaluation = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        base_evaluation,
        ApplicationAssessment.VALID,
    )
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='vehicle_availability',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _SequenceRandomSource((0.45, 0.0, 0.0))),
        base_evaluation=stored_evaluation,
        max_candidates=8,
    )

    result = operator.call(context)

    assert len(result.candidates) == 2
    assert all(proposal.m == 1 for proposal in result.candidates)
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    # The two scenario findings for vehicle-1 must not be summed: vehicle-2's
    # single finding (3.0) exceeds vehicle-1's largest finding (2.5).
    assert all(solution.routes[0] == base.routes[0] for solution in solutions)
    assert tuple(solution.routes[1].stops[0].wait for solution in solutions) == (
        0.0,
        2.0,
    )
    assert all(
        tuple(stop.break_duration for stop in solution.routes[1].stops)
        == tuple(stop.break_duration for stop in base.routes[1].stops)
        for solution in solutions
    )
    assert assessments == 2


def test_availability_timing_fallback_selects_a_route_uniformly(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    first_route = base.routes[0]
    routes = (
        replace(
            first_route,
            stops=first_route.stops[:1],
            legs=(RouteLeg(('a-b',)), RouteLeg(('b-a',))),
        ),
        *base.routes[1:],
    )
    base = replace(base, routes=routes)
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda _instance, _objectives, solution: ApplicationEvaluation(
                True,
                True,
                (
                    sum(
                        route.start_time + sum(stop.wait for stop in route.stops)
                        for route in solution.routes
                    ),
                ),
            ),
        ),
        mode='vehicle_availability',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _SequenceRandomSource((0.45, 0.0))),
        max_candidates=4,
    )

    result = operator.call(context)

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.routes[0].start_time == 1.0
    assert changed.routes[1] == base.routes[1]
    targets = feature_timing_module._targets(
        instance,
        base,
        None,
        'vehicle_availability',
        str(objectives.objectives[0].objective_id),
    )
    assert all(field != 'break_duration' for _route, _stop, field in targets)


def test_availability_timing_applies_k_distinct_decisions_sequentially(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        value = sum(
            route.start_time + sum(stop.wait for stop in route.stops)
            for route in solution.routes
        )
        return ApplicationEvaluation(True, True, (value,))

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='vehicle_availability',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, _SeededRandomSource(149)),
        max_candidates=3,
    )

    result = operator.call(context)

    assert result.candidates
    assert len(result.candidates) <= 3
    for proposal in result.candidates:
        candidate = cast(VrpSolution, proposal.candidate.solution)
        changed_decisions = sum(
            route.start_time != original_route.start_time
            for route, original_route in zip(candidate.routes, base.routes, strict=True)
        ) + sum(
            current_value != original_value
            for route, original_route in zip(candidate.routes, base.routes, strict=True)
            for stop, original_stop in zip(
                route.stops, original_route.stops, strict=True
            )
            for field in ('pre_service_wait', 'wait')
            for current_value, original_value in (
                (getattr(stop, field), getattr(original_stop, field)),
            )
        )
        assert proposal.m == 2
        assert changed_decisions == 2


def test_availability_timing_respects_requested_feasibility(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda *_args: ApplicationEvaluation(True, False, (1.0,)),
        ),
        mode='vehicle_availability',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, _SeededRandomSource(151)),
        max_candidates=4,
    )

    assert operator.call(context).candidates == ()


def test_personnel_calendar_fallback_targets_only_restricted_assigned_routes(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    day_start = instance.scenario_bank.day_boundaries[0][0]
    day_end = instance.scenario_bank.day_boundaries[-1][-1]
    personnel = instance.personnel_data._replace(
        personnel_availability=(
            PersonnelAvailabilityRecord('driver-1', day_start, day_start + 10.0),
            PersonnelAvailabilityRecord('driver-2', day_start, day_end),
        )
    )
    first_route = replace(
        base.routes[0],
        start_time=10.0,
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'job-1'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            *base.routes[0].stops[1:],
        ),
    )
    second_route = replace(
        base.routes[1],
        driver_id='driver-2',
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'job-2'),
                frozenset({'driver-1'}),
                0.0,
                0.0,
                0.0,
                False,
            ),
            *base.routes[1].stops[1:],
        ),
    )
    third_route = replace(base.routes[1], driver_id='driver-2')
    base = replace(base, routes=(first_route, second_route, third_route))
    instance = instance._replace(personnel_data=personnel)

    targets = feature_timing_module._targets(
        instance,
        base,
        None,
        'personnel_calendar',
        str(objectives.objectives[0].objective_id),
    )

    assert targets == (
        (0, None, 'start_time'),
        (0, 0, 'pre_service_wait'),
        (0, 0, 'wait'),
        (1, None, 'start_time'),
        (1, 0, 'pre_service_wait'),
        (1, 0, 'wait'),
    )


def test_personnel_calendar_does_not_fallback_when_analysis_has_no_finding(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    analysis = VrpOperatorAnalysis(True, (), (), ())

    targets = feature_timing_module._targets(
        instance,
        base,
        analysis,
        'personnel_calendar',
        str(objectives.objectives[0].objective_id),
    )

    assert targets == ()


def test_personnel_calendar_targets_routes_for_largest_personnel_finding(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    objective_id = str(objectives.objectives[0].objective_id)
    analysis = VrpOperatorAnalysis(
        True,
        (),
        (
            ResourceFinding(
                'personnel_availability',
                'driver-1',
                'scenario-000001',
                None,
                None,
                None,
                1.0,
                ((objective_id, 1.0),),
            ),
            ResourceFinding(
                'personnel_availability',
                'driver-2',
                'scenario-000001',
                None,
                None,
                None,
                2.0,
                ((objective_id, 3.0),),
            ),
        ),
        (),
    )
    second_route = replace(base.routes[1], driver_id='driver-2')
    base = replace(base, routes=(base.routes[0], second_route))

    targets = feature_timing_module._targets(
        instance,
        base,
        analysis,
        'personnel_calendar',
        objective_id,
    )

    assert targets == ((1, None, 'start_time'),)


def test_personnel_calendar_timing_options_respect_field_domains(
    tmp_path: Path,
) -> None:
    instance, _objectives, base = _test_plan(tmp_path)
    day_end = instance.scenario_bank.day_boundaries[-1][-1]
    activity = ActivityStop(
        ActivityRef('intervention', 'job-1'),
        frozenset(),
        0.0,
        1.5,
        1.0,
        False,
    )
    route = replace(
        base.routes[0],
        start_time=day_end - 0.5,
        stops=(activity, *base.routes[0].stops[1:]),
    )
    solution = replace(base, routes=(route, *base.routes[1:]))

    start_options = feature_timing_module._candidate_values(
        instance,
        solution,
        None,
        (0, None, 'start_time'),
        'personnel_calendar',
        1.0,
        cast(RandomSource, _ZeroRandomSource()),
    )
    wait_options = feature_timing_module._candidate_values(
        instance,
        solution,
        None,
        (0, 0, 'wait'),
        'personnel_calendar',
        1.0,
        cast(RandomSource, _ZeroRandomSource()),
    )

    assert start_options == (day_end - 1.5,)
    assert wait_options == (2.5,)


def test_personnel_calendar_returns_sorted_complete_candidate_batch(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    objective_id = str(objectives.objectives[0].objective_id)
    finding = ResourceFinding(
        'personnel_availability',
        'driver-1',
        'scenario-000001',
        'vehicle-1',
        1,
        None,
        1.0,
        ((objective_id, 1.0),),
    )
    analysis = VrpOperatorAnalysis(True, (), (finding,), ())
    base = replace(
        base, routes=(replace(base.routes[0], start_time=10.0), *base.routes[1:])
    )

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(
            True, True, (solution.routes[0].start_time,), analysis
        )

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='personnel_calendar',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, _ZeroRandomSource()),
        max_candidates=2,
    )

    result = operator.call(context)

    assert len(result.candidates) == 2
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert tuple(solution.routes[0].start_time for solution in solutions) == (9.0, 11.0)


def test_personnel_calendar_selects_a_route_before_one_of_its_timing_fields(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    objective_id = str(objectives.objectives[0].objective_id)
    findings = tuple(
        ResourceFinding(
            'personnel_availability',
            f'driver-{index}',
            'scenario-000001',
            f'vehicle-{index}',
            1,
            None,
            1.0,
            ((objective_id, 1.0),),
        )
        for index in (1, 2)
    )
    analysis = VrpOperatorAnalysis(True, (), findings, ())
    first_route = replace(
        base.routes[0],
        start_time=10.0,
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'job-1'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
            *base.routes[0].stops[1:],
        ),
    )
    second_route = replace(base.routes[1], start_time=20.0, driver_id='driver-2')
    base = replace(base, routes=(first_route, second_route))

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        value = sum(route.start_time for route in solution.routes)
        return ApplicationEvaluation(True, True, (value,), analysis)

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='personnel_calendar',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=1,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            True,
            None,
            cast(RandomSource, _SequenceRandomSource((0.9, 0.0))),
            max_candidates=2,
        )
    )

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert candidate.routes[0] == base.routes[0]
    assert candidate.routes[1].start_time == 19.0


def test_service_wait_guidance_aggregates_each_occurrence_across_scenarios(
    tmp_path: Path,
) -> None:
    _instance, _objectives, base = _service_wait_problem(tmp_path)
    analysis = _window_guidance_analysis(
        base,
        scenario_guidance={
            ('scenario-1', 'job-1'): 10.0,
            ('scenario-1', 'job-2'): 10.0,
            ('scenario-2', 'job-1'): 10.0,
            ('scenario-2', 'job-3'): 10.0,
        },
    )

    targets = feature_timing_module._window_targets(
        base, analysis, 'window-penalty', wait_only=True
    )

    assert len(targets) == 3
    assert len(set(targets)) == 3
    assert targets == (
        (0, 0, 'pre_service_wait'),
        (0, 1, 'pre_service_wait'),
        (1, 0, 'pre_service_wait'),
    )


def test_service_wait_uses_base_evaluation_and_ranks_window_first_batch(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_wait_problem(tmp_path)
    guidance = {'job-1': 10.0, 'job-2': 1.0, 'job-3': 1.0, 'job-4': 1.0}
    base_analysis = _window_guidance_analysis(base, guidance_by_activity=guidance)
    base_values = (4.0, 0.0)
    base_application = ApplicationEvaluation(True, True, base_values, base_analysis)
    stored_base = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        base_application,
        ApplicationAssessment.VALID,
    )
    assessments = 0

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        wait = solution.routes[0].stops[0].pre_service_wait
        window_penalty = sum(
            1.0
            for route in solution.routes
            for stop in route.stops
            if stop.pre_service_wait not in (10.0, 20.0)
        )
        secondary = 5.0 if wait == 10.0 else 2.0 if wait == 20.0 else 0.0
        return ApplicationEvaluation(
            True,
            True,
            (window_penalty, secondary),
            _window_guidance_analysis(solution, guidance_by_activity=guidance),
        )

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='service_window_wait',
        objective_id='window-penalty',
        time_step=1.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
    )
    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            base_evaluation=stored_base,
            max_candidates=4,
        )
    )

    assert len(result.candidates) == 1
    proposal = result.candidates[0]
    assert proposal.m == 1
    changed = cast(VrpSolution, proposal.candidate.solution)
    assert changed.routes[0].stops[0].pre_service_wait == 20.0
    assert assessments == 3


def test_service_wait_changes_distinct_fields_sequentially_up_to_k(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_wait_problem(tmp_path)
    guidance = {'job-1': 10.0, 'job-2': 6.0, 'job-3': 8.0, 'job-4': 4.0}
    assessments = 0

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        analysis = _window_guidance_analysis(solution, guidance_by_activity=guidance)
        value = sum(
            stop.pre_service_wait for route in solution.routes for stop in route.stops
        )
        return ApplicationEvaluation(True, True, (value, 0.0), analysis)

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='service_window_wait',
        objective_id='window-penalty',
        time_step=1.0,
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
    )
    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            max_candidates=4,
        )
    )

    assert result.candidates
    assert all(proposal.m == 2 for proposal in result.candidates)
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    changed_waits = tuple(
        (route_index, stop_index, stop.pre_service_wait)
        for route_index, route in enumerate(changed.routes)
        for stop_index, stop in enumerate(route.stops)
        if stop.pre_service_wait
        != base.routes[route_index].stops[stop_index].pre_service_wait
    )
    assert len(changed_waits) == 2
    assert {value for _route, _stop, value in changed_waits} == {1.0}
    assert assessments == 7


def test_service_wait_falls_back_to_windowed_activity_without_base_analysis(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_wait_problem(tmp_path)
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda _instance, _objectives, solution: ApplicationEvaluation(
                True,
                True,
                (1.0, 0.0),
                None,
            ),
        ),
        mode='service_window_wait',
        objective_id='window-penalty',
        time_step=1.0,
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
            max_candidates=2,
        )
    )

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert any(
        stop.pre_service_wait == 1.0
        for route in candidate.routes
        for stop in route.stops
    )


def test_service_wait_caps_the_ranked_candidate_batch(tmp_path: Path) -> None:
    instance, objectives, base = _service_wait_problem(tmp_path)
    analysis = _window_guidance_analysis(
        base,
        guidance_by_activity={
            'job-1': 10.0,
            'job-2': 1.0,
            'job-3': 1.0,
            'job-4': 1.0,
        },
    )
    base_application = ApplicationEvaluation(True, True, (0.0, 0.0), analysis)
    stored_base = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        base_application,
        ApplicationAssessment.VALID,
    )
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda _instance, _objectives, solution: ApplicationEvaluation(
                True,
                True,
                (0.0, 0.0),
                _window_guidance_analysis(solution),
            ),
        ),
        mode='service_window_wait',
        objective_id='window-penalty',
        time_step=1.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _SeededRandomSource(42)),
            base_evaluation=stored_base,
            max_candidates=2,
        )
    )

    assert len(result.candidates) == 2
    candidates = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(candidates)) == 2
    assert all(proposal.m == 1 for proposal in result.candidates)


def test_service_wait_filters_candidates_that_fail_requested_feasibility(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_wait_problem(tmp_path)
    analysis = _window_guidance_analysis(
        base,
        guidance_by_activity={
            'job-1': 10.0,
            'job-2': 1.0,
            'job-3': 1.0,
            'job-4': 1.0,
        },
    )
    base_application = ApplicationEvaluation(True, True, (0.0, 0.0), analysis)
    stored_base = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        base_application,
        ApplicationAssessment.VALID,
    )
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda _instance, _objectives, solution: ApplicationEvaluation(
                True,
                False,
                (0.0, 0.0),
                _window_guidance_analysis(solution),
            ),
        ),
        mode='service_window_wait',
        objective_id='window-penalty',
        time_step=1.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            True,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            base_evaluation=stored_base,
            max_candidates=4,
        )
    )

    assert result.candidates == ()


def test_route_start_targets_unique_max_guidance_per_occurrence(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_wait_problem(tmp_path)
    analysis = _window_guidance_analysis(
        base,
        scenario_guidance={
            ('scenario-1', 'job-1'): 10.0,
            ('scenario-1', 'job-2'): 10.0,
            ('scenario-2', 'job-1'): 10.0,
            ('scenario-2', 'job-3'): 10.0,
        },
    )

    targets = feature_timing_module._targets(
        instance,
        base,
        analysis,
        'route_start_window',
        'window-penalty',
        activity_window_index=feature_timing_module._index_activity_windows(instance),
    )

    assert targets == (
        (0, None, 'start_time'),
        (0, None, 'start_time'),
        (1, None, 'start_time'),
    )


def test_route_start_fallback_weights_windowed_occurrences(tmp_path: Path) -> None:
    instance, _objectives, base = _service_wait_problem(tmp_path)

    targets = feature_timing_module._targets(
        instance,
        base,
        None,
        'route_start_window',
        'window-penalty',
        activity_window_index=feature_timing_module._index_activity_windows(instance),
    )

    assert targets == (
        (0, None, 'start_time'),
        (0, None, 'start_time'),
        (1, None, 'start_time'),
        (1, None, 'start_time'),
    )


def test_route_start_uses_base_evaluation_and_window_first_ranking(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_wait_problem(tmp_path)
    guidance = {'job-1': 10.0, 'job-2': 1.0, 'job-3': 1.0, 'job-4': 1.0}
    base_analysis = _window_guidance_analysis(base, guidance_by_activity=guidance)
    base_application = ApplicationEvaluation(True, True, (1.0, 0.0), base_analysis)
    stored_base = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        base_application,
        ApplicationAssessment.VALID,
    )
    assessments = 0

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        start_time = solution.routes[0].start_time
        window_penalty = 0.0 if start_time in (10.0, 20.0) else 1.0
        secondary = 5.0 if start_time == 10.0 else 2.0 if start_time == 20.0 else 0.0
        return ApplicationEvaluation(
            True,
            True,
            (window_penalty, secondary),
            _window_guidance_analysis(solution, guidance_by_activity=guidance),
        )

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='route_start_window',
        objective_id='window-penalty',
        time_step=1.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
    )
    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            base_evaluation=stored_base,
            max_candidates=4,
        )
    )

    assert len(result.candidates) == 1
    proposal = result.candidates[0]
    assert proposal.m == 1
    changed = cast(VrpSolution, proposal.candidate.solution)
    assert changed.routes[0].start_time == 20.0
    assert assessments == 3


def test_route_start_changes_distinct_routes_sequentially_up_to_k(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_wait_problem(tmp_path)
    guidance = {'job-1': 10.0, 'job-2': 6.0, 'job-3': 8.0, 'job-4': 4.0}
    assessments = 0

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        value = sum(route.start_time for route in solution.routes)
        return ApplicationEvaluation(
            True,
            True,
            (value, 0.0),
            _window_guidance_analysis(solution, guidance_by_activity=guidance),
        )

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        mode='route_start_window',
        objective_id='window-penalty',
        time_step=1.0,
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
    )
    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            max_candidates=4,
        )
    )

    assert result.candidates
    assert all(proposal.m == 2 for proposal in result.candidates)
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    changed_routes = tuple(
        route_index
        for route_index, (route, original) in enumerate(
            zip(changed.routes, base.routes, strict=True)
        )
        if route.start_time != original.start_time
    )
    assert changed_routes == (0, 1)
    assert tuple(changed.routes[index].start_time for index in changed_routes) == (
        1.0,
        1.0,
    )
    assert assessments == 7


def test_route_start_values_stay_inside_the_route_day_interval(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _service_wait_problem(tmp_path)
    scenario_bank = instance.scenario_bank._replace(day_boundaries=((5.0, 10.0, 15.0),))
    instance = instance._replace(scenario_bank=scenario_bank)
    routes = tuple(replace(route, start_time=5.0) for route in base.routes)
    base = replace(base, routes=routes)
    analysis = _window_guidance_analysis(
        base,
        guidance_by_activity={'job-1': 10.0, 'job-2': 1.0, 'job-3': 1.0, 'job-4': 1.0},
    )
    base_application = ApplicationEvaluation(True, True, (0.0, 1.0), analysis)
    stored_base = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        base_application,
        ApplicationAssessment.VALID,
    )
    results = []
    for preferred_invalid_value in (4.0, 25.0):
        operator = FeatureTimingOperator(
            instance,
            objectives,
            cast(
                VrpPlanAssessor,
                lambda _instance, _objectives, solution: ApplicationEvaluation(
                    True,
                    True,
                    (
                        0.0,
                        0.0
                        if solution.routes[0].start_time == preferred_invalid_value
                        else 1.0,
                    ),
                    analysis,
                ),
            ),
            mode='route_start_window',
            objective_id='window-penalty',
            time_step=1.0,
            max_neighborhood_size=1,
            max_candidate_alternatives=8,
        )
        result = operator.call(
            CandidateContext(
                base,
                1,
                False,
                None,
                cast(RandomSource, _SeededRandomSource(73)),
                base_evaluation=stored_base,
                max_candidates=4,
            )
        )
        assert len(result.candidates) == 1
        changed = cast(VrpSolution, result.candidates[0].candidate.solution)
        results.append(changed.routes[0].start_time)

    assert results == [6.0, 6.0]


def test_route_start_falls_back_when_analysis_is_absent(tmp_path: Path) -> None:
    instance, objectives, base = _service_wait_problem(tmp_path)
    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda _instance, _objectives, _solution: ApplicationEvaluation(
                True, True, (0.0, 0.0), None
            ),
        ),
        mode='route_start_window',
        objective_id='window-penalty',
        time_step=1.0,
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
            max_candidates=2,
        )
    )

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.routes[0].start_time == 1.0


def test_work_rule_targets_the_largest_penalty_and_returns_ordered_route_alternatives(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    objective_id = str(objectives.objectives[0].objective_id)
    second_route = replace(
        base.routes[1],
        start_time=10.0,
        stops=(replace(base.routes[1].stops[0], wait=10.0),),
        legs=(base.routes[1].legs[0], base.routes[1].legs[-1]),
    )
    base = replace(base, routes=(base.routes[0], second_route))
    instance = instance._replace(
        settings=instance.settings._replace(rule_treatment='penalty')
    )
    finding = ResourceFinding(
        'driver_work',
        'driver-1:daily_work:1',
        'scenario-1',
        None,
        1,
        None,
        2.0,
        ((objective_id, 3.0),),
    )
    analysis = VrpOperatorAnalysis(True, (), (finding,), ())

    def assess(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        value = sum(
            route.start_time + sum(stop.wait for stop in route.stops)
            for route in solution.routes
        )
        return ApplicationEvaluation(True, True, (value,), analysis)

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='work_rule',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=1,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, _HighThenZeroRandomSource()),
        max_candidates=4,
    )

    result = operator.call(context)

    assert len(result.candidates) == 4
    assert all(proposal.m == 1 for proposal in result.candidates)
    for proposal in result.candidates:
        candidate = cast(VrpSolution, proposal.candidate.solution)
        assert candidate.routes[0] == base.routes[0]
        assert candidate.routes[1] != base.routes[1]
    values = tuple(
        sum(
            route.start_time + sum(stop.wait for stop in route.stops)
            for route in cast(VrpSolution, proposal.candidate.solution).routes
        )
        for proposal in result.candidates
    )
    assert values == tuple(sorted(values))


def test_work_rule_compliance_uses_selected_measure_for_infeasible_trials(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    base = replace(
        base,
        routes=(
            base.routes[0],
            replace(base.routes[1], start_time=10.0, stops=(), legs=(RouteLeg(()),)),
        ),
    )
    instance = instance._replace(
        settings=instance.settings._replace(
            rule_treatment='compliance',
            active_simple_rules=frozenset({'daily_work'}),
        )
    )

    def assess(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        start = solution.routes[1].start_time
        measure, objective = {
            9.0: (1.0, 100.0),
            10.0: (3.0, 50.0),
            11.0: (2.0, 0.0),
        }.get(start, (1.0, 100.0))
        finding = ResourceFinding(
            'driver_work',
            'driver-1:daily_work:1',
            'scenario-1',
            None,
            1,
            None,
            measure,
            (),
        )
        analysis = VrpOperatorAnalysis(
            False,
            (VrpScenarioEvaluation('scenario-1', 1.0, ()),),
            (finding,),
            (),
            HardFeasibilityFinding('compliance_rule', 'work limit', 'scenario-1'),
        )
        return ApplicationEvaluation(True, False, (objective,), analysis)

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='work_rule',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=2,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, _HighThenZeroRandomSource()),
        max_candidates=4,
    )

    result = operator.call(context)

    assert result.candidates
    assert all(proposal.m == 2 for proposal in result.candidates)
    assert all(
        cast(VrpSolution, proposal.candidate.solution).routes[1].start_time == 9.0
        for proposal in result.candidates
    )


def test_work_rule_break_finding_increases_break_and_same_stop_wait(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    activity_one = ActivityRef('intervention', 'break-one')
    activity_two = ActivityRef('intervention', 'break-two')
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(activity_one, frozenset(), 0.0, 0.0, 0.0, False),
            ActivityStop(activity_two, frozenset(), 0.0, 0.0, 0.0, False),
        ),
    )
    base = replace(base, routes=tuple(routes))
    instance = instance._replace(
        settings=instance.settings._replace(
            rule_treatment='compliance',
            active_simple_rules=frozenset({'continuous_driving'}),
        )
    )
    finding = ResourceFinding(
        'break',
        'driver-1:break:1',
        'scenario-1',
        'vehicle-1',
        1,
        activity_two,
        1.0,
        (),
    )

    def assess(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        stop = cast(ActivityStop, solution.routes[0].stops[1])
        if stop.break_duration >= 1.0:
            return ApplicationEvaluation(
                True, True, (0.0,), VrpOperatorAnalysis(True, (), (), ())
            )
        analysis = VrpOperatorAnalysis(
            False,
            (VrpScenarioEvaluation('scenario-1', 1.0, ()),),
            (finding,),
            (),
            HardFeasibilityFinding('compliance_rule', 'break rule', 'scenario-1'),
        )
        return ApplicationEvaluation(True, False, (100.0,), analysis)

    operator = FeatureTimingOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='work_rule',
        objective_id=objectives.objectives[0].objective_id,
        time_step=1.0,
        max_neighborhood_size=2,
    )
    result = operator.call(
        CandidateContext(
            base,
            2,
            True,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            max_candidates=8,
        )
    )

    assert len(result.candidates) == 1
    proposal = result.candidates[0]
    assert proposal.m == 2
    candidate = cast(VrpSolution, proposal.candidate.solution)
    changed_stop = cast(ActivityStop, candidate.routes[0].stops[1])
    assert changed_stop.break_duration == 1.0
    assert changed_stop.wait >= changed_stop.break_duration
    assert candidate.routes[0].stops[0] == base.routes[0].stops[0]


def test_feature_timing_validates_supported_mode(tmp_path: Path) -> None:
    instance, objectives, _base = _test_plan(tmp_path)
    with pytest.raises(ValueError, match='unsupported feature timing mode'):
        FeatureTimingOperator(
            instance,
            objectives,
            cast(
                VrpPlanAssessor,
                lambda *_args: ApplicationEvaluation(True, True, (1.0,)),
            ),
            mode='unknown',  # type: ignore[arg-type]
            objective_id=objectives.objectives[0].objective_id,
            time_step=5.0,
            max_neighborhood_size=1,
        )
