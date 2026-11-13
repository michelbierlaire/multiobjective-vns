"""Tests for time-window schedule compliance repair.

Created: 2026-11-05T11:13:51+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from random import Random
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    VrpObjectiveFunction,
)
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    StopScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    InterventionTimeWindowRecord,
    LoadedVrpInstance,
    ScenarioRecord,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.service_window_schedule import (
    TimeWindowScheduleComplianceOperator,
    _window_score,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
)
from multiobjective_vns.records.status import ApplicationAssessment, ProcessingStatus

from .test_universal_route_order import _test_plan


class _SequenceRandomSource:
    def __init__(self, values: tuple[float, ...]) -> None:
        self._values: Iterator[float] = iter(values)

    def random(self) -> float:
        return next(self._values, 0.0)


def _windowed_plan(
    root: Path,
    *,
    start_time: float = 20.0,
    second_wait: float = 0.0,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _test_plan(root)
    interventions = (
        InterventionRecord('first', 'b', 0.0, 1),
        InterventionRecord('second', 'c', 0.0, 1),
    )
    windows = (
        InterventionTimeWindowRecord('first', 1, 0, 10.0, 10.0),
        InterventionTimeWindowRecord('second', 1, 0, 25.0, 27.0),
    )
    instance = instance._replace(
        scenario_bank=instance.scenario_bank._replace(
            scenarios=(
                ScenarioRecord('scenario-1', 0.5),
                ScenarioRecord('scenario-2', 0.5),
            )
        ),
        intervention_data=instance.intervention_data._replace(
            interventions=interventions,
            intervention_time_windows=windows,
        ),
        time_window_violation_objective_id='window-penalty',
    )
    objectives = ObjectiveDeclaration(
        (
            VrpObjectiveFunction('target', 'test-target-objective', 'test-target-v1'),
            VrpObjectiveFunction(
                'window-penalty', 'test-window-objective', 'test-window-v1'
            ),
        )
    )
    first_route = replace(
        base.routes[0],
        start_time=start_time,
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'first'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('intervention', 'second'),
                frozenset(),
                second_wait,
                0.0,
                0.0,
                False,
            ),
        ),
    )
    return instance, objectives, replace(base, routes=(first_route, *base.routes[1:]))


def _analysis(
    solution: VrpSolution,
) -> VrpOperatorAnalysis:
    scenarios = []
    for scenario_id, probability, between_stop_travel in (
        ('scenario-1', 0.5, 10.0),
        ('scenario-2', 0.5, 12.0),
    ):
        vehicle_days = []
        for route_index, route in enumerate(solution.routes):
            elapsed = route.start_time
            stop_traces = []
            for stop_index, stop in enumerate(route.stops):
                if stop_index:
                    elapsed += between_stop_travel if route_index == 0 else 0.0
                arrival = elapsed
                if isinstance(stop, ActivityStop):
                    service_start = arrival + stop.pre_service_wait
                    service_finish = service_start
                    elapsed = service_finish + stop.wait
                    stop_traces.append(
                        StopScenarioEvaluation(
                            stop_index,
                            'activity',
                            'b' if stop.activity_ref.activity_id == 'first' else 'c',
                            arrival,
                            service_start,
                            service_finish,
                            elapsed,
                            None,
                            None,
                            stop.activity_ref,
                            None,
                            (),
                        )
                    )
                else:
                    assert isinstance(stop, DepotOnlyStop)
                    elapsed = arrival + stop.wait
                    stop_traces.append(
                        StopScenarioEvaluation(
                            stop_index,
                            'depot_only',
                            stop.node_id,
                            arrival,
                            None,
                            None,
                            elapsed,
                            None,
                            None,
                            None,
                            None,
                            (),
                        )
                    )
            vehicle_days.append(
                VehicleDayScenarioEvaluation(
                    route.vehicle_id,
                    route.day_index,
                    route.start_time,
                    elapsed,
                    tuple(stop_traces),
                    (),
                )
            )
        scenarios.append(
            VrpScenarioEvaluation(scenario_id, probability, tuple(vehicle_days))
        )
    return VrpOperatorAnalysis(True, tuple(scenarios), (), ())


def _assessor(
    instance: LoadedVrpInstance,
    _objectives: ObjectiveDeclaration,
    solution: VrpSolution,
) -> ApplicationEvaluation:
    score = _window_score(instance, solution, _analysis(solution))
    assert score is not None
    return ApplicationEvaluation(
        True,
        True,
        (-solution.routes[0].start_time, float(100 * score[0] + score[1])),
        _analysis(solution),
    )


def _base_evaluation(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    solution: VrpSolution,
) -> EvaluationRecord:
    return EvaluationRecord(
        ProcessingStatus.COMPLETED,
        _assessor(instance, objectives, solution),
        ApplicationAssessment.VALID,
    )


def test_schedule_repair_coordinates_start_and_wait_to_fit_every_scenario(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(tmp_path)
    operator = TimeWindowScheduleComplianceOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assessor),
        objective_id=objectives.objectives[0].objective_id,
        time_step=5.0,
        max_neighborhood_size=2,
        max_candidate_alternatives=32,
    )
    random_source = _SequenceRandomSource((0.99, 0.0, 0.99, 0.45))
    context = CandidateContext(
        base,
        2,
        True,
        None,
        cast(RandomSource, random_source),
        _base_evaluation(instance, objectives, base),
    )

    result = operator.call(context)

    assert result.candidates[0].m == 2
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.routes[0].start_time == 10.0
    assert cast(ActivityStop, changed.routes[0].stops[1]).pre_service_wait == 5.0
    changed_values = _assessor(instance, objectives, changed).objective_values
    base_values = _assessor(instance, objectives, base).objective_values
    assert changed_values is not None and base_values is not None
    assert cast(float, changed_values[1]) < cast(float, base_values[1])


def test_schedule_repair_falls_back_when_every_route_score_is_zero(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(
        tmp_path, start_time=10.0, second_wait=5.0
    )
    operator = TimeWindowScheduleComplianceOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assessor),
        objective_id=objectives.objectives[0].objective_id,
        time_step=5.0,
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            True,
            None,
            cast(RandomSource, _SequenceRandomSource((0.0,))),
            _base_evaluation(instance, objectives, base),
        )
    )

    assert result.candidates
    assert 1 <= result.candidates[0].m <= 2


def test_schedule_repair_uses_complete_infeasible_base_analysis_for_targeting(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(tmp_path)
    application_evaluation = _base_evaluation(
        instance, objectives, base
    ).application_evaluation
    assert application_evaluation is not None
    infeasible_base_evaluation = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        replace(application_evaluation, feasible=False),
        ApplicationAssessment.VALID,
    )
    operator = TimeWindowScheduleComplianceOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assessor),
        objective_id=objectives.objectives[0].objective_id,
        time_step=5.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=16,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            True,
            None,
            cast(RandomSource, Random(9)),
            infeasible_base_evaluation,
        )
    )

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.routes[0].start_time != base.routes[0].start_time or any(
        left.pre_service_wait != right.pre_service_wait
        for left, right in zip(
            base.routes[0].stops, changed.routes[0].stops, strict=True
        )
    )
    assert changed.routes[1] == base.routes[1]


def test_schedule_repair_filters_candidates_that_fail_requested_feasibility(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(tmp_path)

    def infeasible_assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        compliant_timing = (
            solution.routes[0].start_time == 10.0
            and cast(ActivityStop, solution.routes[0].stops[1]).pre_service_wait == 5.0
        )
        objective_values = _assessor(_instance, _objectives, solution).objective_values
        assert objective_values is not None
        return ApplicationEvaluation(
            True,
            solution == base or compliant_timing,
            objective_values,
            _analysis(solution),
        )

    operator = TimeWindowScheduleComplianceOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, infeasible_assessor),
        objective_id=objectives.objectives[0].objective_id,
        time_step=5.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            True,
            None,
            cast(RandomSource, _SequenceRandomSource((0.0,) * 256)),
            _base_evaluation(instance, objectives, base),
        )
    )

    assert result.candidates == ()
    assert not result.candidates


def test_schedule_repair_does_not_assess_input_without_base_evaluation(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(tmp_path)
    assessed: list[VrpSolution] = []

    def candidate_only_assessor(
        assessment_instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        assert solution != base
        assessed.append(solution)
        return _assessor(assessment_instance, declaration, solution)

    operator = TimeWindowScheduleComplianceOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, candidate_only_assessor),
        objective_id=objectives.objectives[0].objective_id,
        time_step=5.0,
        max_neighborhood_size=2,
        max_candidate_alternatives=4,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            True,
            None,
            cast(RandomSource, _SequenceRandomSource((0.0,) * 128)),
        )
    )

    assert result.candidates[0].m > 0
    assert result.candidates
    assert assessed
    assert base not in assessed


def test_schedule_repair_returns_distinct_ranked_batch_up_to_context_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(tmp_path)

    def tied_assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (0.0, 0.0), None)

    operator = TimeWindowScheduleComplianceOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, tied_assessor),
        objective_id=objectives.objectives[0].objective_id,
        time_step=5.0,
        max_neighborhood_size=2,
        max_candidate_alternatives=24,
    )
    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, Random(213)),
            _base_evaluation(instance, objectives, base),
            max_candidates=3,
        )
    )

    assert len(result.candidates) == 3
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(solutions)) == 3
    assert all(1 <= proposal.m <= 2 for proposal in result.candidates)


def test_schedule_repair_ranks_by_window_objective_before_target_objective(
    tmp_path: Path,
) -> None:
    instance, _base_objectives, base = _windowed_plan(tmp_path)
    instance = instance._replace(time_window_violation_objective_id='window-penalty')
    objectives = ObjectiveDeclaration(
        (
            VrpObjectiveFunction(
                'window-penalty', 'test-window-objective', 'test-window-v1'
            ),
            VrpObjectiveFunction('target', 'test-target-objective', 'test-target-v1'),
        )
    )
    assessed_values: dict[VrpSolution, tuple[float, float]] = {}

    def ranked_assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        start_time = solution.routes[0].start_time
        values = (0.0 if start_time == 10.0 else 1.0, -start_time)
        assessed_values[solution] = values
        return ApplicationEvaluation(True, True, values, _analysis(solution))

    base_evaluation = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        ApplicationEvaluation(True, True, (1.0, -20.0), _analysis(base)),
        ApplicationAssessment.VALID,
    )
    operator = TimeWindowScheduleComplianceOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, ranked_assessor),
        objective_id='target',
        time_step=5.0,
        max_neighborhood_size=1,
        max_candidate_alternatives=32,
    )
    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, Random(47)),
            base_evaluation,
            max_candidates=4,
        )
    )

    assert result.candidates
    assert assessed_values
    best_window = min(values[0] for values in assessed_values.values())
    best_target = min(
        values[1] for values in assessed_values.values() if values[0] == best_window
    )
    returned_values = tuple(
        assessed_values[cast(VrpSolution, proposal.candidate.solution)]
        for proposal in result.candidates
    )
    assert all(values == (best_window, best_target) for values in returned_values)
