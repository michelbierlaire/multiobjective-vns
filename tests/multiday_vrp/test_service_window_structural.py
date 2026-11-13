"""Tests for time-window-focused route structural Operators.

Created: 2026-11-06T13:24:40+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

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
    PackageRecord,
    PackageTimeWindowRecord,
    PrecedenceRecord,
    VehicleOvernightDepotRecord,
    VehicleRecord,
    VrpFeatureProfile,
)
from multiobjective_vns.multiday_vrp.operators import service_window_common
from multiobjective_vns.multiday_vrp.operators import (
    service_window_structural as structural_module,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.path_search import PathSearchIndex
from multiobjective_vns.multiday_vrp.operators.service_window_common import (
    _block_slack,
    _Bundle,
    _bundle_slack,
    _route_bundles,
    _WindowStructuralOperator,
    select_window_first_candidates,
)
from multiobjective_vns.multiday_vrp.operators.service_window_ejection_chain import (
    WindowPrioritizedEjectionChainConsolidationOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_structural import (
    SlackGuidedOrOptRelocationOperator,
    WindowFeasibleSwapStarOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteLeg,
    RouteStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
)
from multiobjective_vns.records.status import ApplicationAssessment, ProcessingStatus

from .test_vehicle_route_assignment import _service_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 100000)

    def random(self) -> float:
        return next(self._draws)


class _UpperRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.99, 0.0) * 100000)

    def random(self) -> float:
        return next(self._draws)


class _SequenceRandomSource:
    def __init__(self, draws: tuple[float, ...]) -> None:
        self._draws: Iterator[float] = iter(draws)

    def random(self) -> float:
        return next(self._draws)


def _windowed_problem(
    root: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, solution = _service_plan(root)
    windows = tuple(
        InterventionTimeWindowRecord(intervention_id, 1, 1, 0.0, 100.0)
        for intervention_id in ('job-1', 'job-3')
    )
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            intervention_time_windows=windows
        ),
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
    return instance, objectives, solution


def _assess(
    _instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    _solution: VrpSolution,
) -> ApplicationEvaluation:
    return ApplicationEvaluation(True, True, tuple(0.0 for _ in objectives.objectives))


def _context(
    solution: VrpSolution,
    *,
    k: int = 1,
    ensure_feasible: bool = False,
    base_evaluation: EvaluationRecord | None = None,
    random_source: RandomSource | None = None,
) -> CandidateContext:
    return CandidateContext(
        solution,
        k,
        ensure_feasible,
        None,
        cast(RandomSource, random_source or _ZeroRandomSource()),
        base_evaluation,
    )


def _base_evaluation(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    solution: VrpSolution,
    service_starts: dict[str, float],
    *,
    unavailable: frozenset[str] = frozenset(),
) -> EvaluationRecord:
    node_by_intervention = {
        item.intervention_id: item.node_id
        for item in instance.intervention_data.interventions
    }
    scenarios = tuple(
        VrpScenarioEvaluation(
            scenario.scenario_id,
            scenario.probability,
            tuple(
                VehicleDayScenarioEvaluation(
                    route.vehicle_id,
                    route.day_index,
                    route.start_time,
                    100.0,
                    tuple(
                        StopScenarioEvaluation(
                            stop_index,
                            'activity',
                            node_by_intervention[stop.activity_ref.activity_id],
                            0.0,
                            None
                            if stop.activity_ref.activity_id in unavailable
                            else service_starts.get(
                                stop.activity_ref.activity_id, 10.0
                            ),
                            None
                            if stop.activity_ref.activity_id in unavailable
                            else service_starts.get(
                                stop.activity_ref.activity_id, 10.0
                            ),
                            10.0,
                            None,
                            None,
                            stop.activity_ref,
                            None,
                            (),
                        )
                        for stop_index, stop in enumerate(route.stops)
                        if isinstance(stop, ActivityStop)
                    ),
                    (),
                )
                for route in solution.routes
            ),
        )
        for scenario in instance.scenario_bank.scenarios
    )
    analysis = VrpOperatorAnalysis(True, scenarios, (), ())
    evaluation = ApplicationEvaluation(
        True,
        True,
        tuple(0.0 for _ in objectives.objectives),
        analysis,
    )
    return EvaluationRecord(
        ProcessingStatus.COMPLETED,
        evaluation,
        ApplicationAssessment.VALID,
    )


def _activity_location(
    solution: VrpSolution,
    activity_id: str,
) -> tuple[str, int]:
    return next(
        (route.vehicle_id, stop_index)
        for route in solution.routes
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop)
        and stop.activity_ref.activity_id == activity_id
    )


def _windowed_activity_ids(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> frozenset[str]:
    windowed = {
        item.intervention_id
        for item in instance.intervention_data.intervention_time_windows
    }
    return frozenset(
        stop.activity_ref.activity_id
        for route in solution.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop) and stop.activity_ref.activity_id in windowed
    )


def test_window_first_selection_prefers_zero_then_smallest_penalty() -> None:
    class _Objective:
        def compare(self, left: object, right: object) -> ObjectiveComparison:
            return (
                ObjectiveComparison.LESS
                if cast(float, left) < cast(float, right)
                else ObjectiveComparison.GREATER
                if cast(float, left) > cast(float, right)
                else ObjectiveComparison.EQUAL
            )

    candidates = (
        ('positive-a', (1.0, 2.0)),
        ('zero', (3.0, 0.0)),
        ('positive-b', (0.0, 1.0)),
    )
    objective = cast(VrpObjectiveFunction, _Objective())
    retained = select_window_first_candidates(
        candidates, objective, window_objective_position=1
    )
    assert retained == (('zero', (3.0, 0.0)),)

    positive_only = select_window_first_candidates(
        (candidates[0], candidates[2]),
        objective,
        window_objective_position=1,
    )
    assert positive_only == (candidates[2],)


def test_slack_guided_or_opt_moves_a_windowed_activity(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    operator = SlackGuidedOrOptRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=3,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert result.candidates[0].m == 1
    assert candidate != base
    assert _windowed_activity_ids(instance, candidate) == {'job-1', 'job-3'}
    assert _activity_location(candidate, 'job-1') != _activity_location(base, 'job-1')


def test_slack_guided_or_opt_returns_empty_without_windowed_blocks(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            intervention_time_windows=()
        )
    )
    operator = SlackGuidedOrOptRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=3,
    )

    result = operator.call(_context(base))

    assert result.candidates == ()


def test_slack_guided_or_opt_targets_the_smallest_known_plan_slack(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)

    def assess(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        feasible = _activity_location(solution, 'job-1') == ('vehicle-1', 0)
        return ApplicationEvaluation(
            True, feasible, tuple(0.0 for _ in declaration.objectives)
        )

    operator = SlackGuidedOrOptRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=2,
        max_arc_expansion_steps=100,
    )
    context = _context(
        base,
        ensure_feasible=True,
        base_evaluation=_base_evaluation(
            instance,
            objectives,
            base,
            {'job-1': 40.0, 'job-3': 5.0},
        ),
    )

    result = operator.call(context)

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert _activity_location(candidate, 'job-1') == ('vehicle-1', 0)
    assert _activity_location(candidate, 'job-3') != _activity_location(base, 'job-3')


def test_slack_guided_or_opt_treats_a_block_with_missing_slack_as_unknown(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    windows = (
        *instance.intervention_data.intervention_time_windows,
        InterventionTimeWindowRecord('job-2', 1, 1, 0.0, 100.0),
    )
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            intervention_time_windows=windows
        )
    )

    analysis_record = _base_evaluation(
        instance,
        objectives,
        base,
        {'job-1': 40.0},
        unavailable=frozenset({'job-2', 'job-3'}),
    )
    evaluation = analysis_record.application_evaluation
    assert evaluation is not None
    analysis = cast(VrpOperatorAnalysis, evaluation.analysis)
    assert _block_slack(instance, base, 0, 0, 2, analysis) is None
    assert _block_slack(instance, base, 0, 0, 1, analysis) == 40.0

    def assess(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        feasible = _activity_location(solution, 'job-1') == ('vehicle-1', 0)
        return ApplicationEvaluation(
            True, feasible, tuple(0.0 for _ in declaration.objectives)
        )

    operator = SlackGuidedOrOptRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=2,
        max_arc_expansion_steps=100,
    )
    context = _context(
        base,
        k=2,
        ensure_feasible=True,
        random_source=cast(RandomSource, _UpperRandomSource()),
        base_evaluation=analysis_record,
    )

    result = operator.call(context)

    assert result.candidates
    proposal = result.candidates[0]
    assert proposal.m == 2
    candidate = cast(VrpSolution, proposal.candidate.solution)
    assert _activity_location(candidate, 'job-1') == ('vehicle-1', 0)
    job_3_location = _activity_location(candidate, 'job-3')
    job_4_location = _activity_location(candidate, 'job-4')
    assert job_3_location[0] == job_4_location[0]
    assert job_4_location[1] == job_3_location[1] + 1
    assert job_3_location != _activity_location(base, 'job-3')


def test_slack_guided_or_opt_moves_exactly_a_length_k_block(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)

    def assess(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        job_1_location = _activity_location(solution, 'job-1')
        job_2_location = _activity_location(solution, 'job-2')
        moved_together = (
            job_1_location[0] == job_2_location[0]
            and job_2_location[1] == job_1_location[1] + 1
            and job_1_location != _activity_location(base, 'job-1')
        )
        return ApplicationEvaluation(
            True,
            moved_together,
            tuple(0.0 for _ in declaration.objectives),
        )

    operator = SlackGuidedOrOptRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=2,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, k=2))

    assert result.candidates
    proposal = result.candidates[0]
    assert proposal.m == 2
    candidate = cast(VrpSolution, proposal.candidate.solution)
    job_1_location = _activity_location(candidate, 'job-1')
    job_2_location = _activity_location(candidate, 'job-2')
    assert job_1_location[0] == job_2_location[0]
    assert job_2_location[1] == job_1_location[1] + 1
    assert job_1_location != _activity_location(base, 'job-1')


def test_window_feasible_swap_star_exchanges_repair_units(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    operator = WindowFeasibleSwapStarOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert result.candidates[0].m == 1
    assert candidate != base
    assert _windowed_activity_ids(instance, candidate) == {'job-1', 'job-3'}


def test_window_feasible_swap_star_applies_up_to_k_distinct_exchanges(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    operator = WindowFeasibleSwapStarOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, k=2))

    assert result.candidates
    assert result.candidates[0].m == 2
    assert cast(VrpSolution, result.candidates[0].candidate.solution) != base


def test_window_feasible_swap_star_prefers_lowest_known_unit_slack(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)

    def assess(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        job_3_moved = _activity_location(solution, 'job-3') != _activity_location(
            base, 'job-3'
        )
        return ApplicationEvaluation(
            True, job_3_moved, tuple(0.0 for _ in declaration.objectives)
        )

    operator = WindowFeasibleSwapStarOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=4,
        max_paths_per_leg=2,
        max_arc_expansion_steps=100,
    )
    context = _context(
        base,
        ensure_feasible=True,
        base_evaluation=_base_evaluation(
            instance, objectives, base, {'job-1': 40.0, 'job-3': 5.0}
        ),
    )

    result = operator.call(context)

    assert result.candidates
    assert result.candidates[0].m == 1
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert _activity_location(candidate, 'job-3') != _activity_location(base, 'job-3')


def test_window_feasible_swap_star_treats_missing_unit_slack_as_unknown(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    windows = (
        *instance.intervention_data.intervention_time_windows,
        InterventionTimeWindowRecord('job-2', 1, 1, 0.0, 100.0),
    )
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            intervention_time_windows=windows
        )
    )
    analysis_record = _base_evaluation(
        instance,
        objectives,
        base,
        {'job-1': 40.0},
        unavailable=frozenset({'job-2'}),
    )
    evaluation = analysis_record.application_evaluation
    assert evaluation is not None
    analysis = cast(VrpOperatorAnalysis, evaluation.analysis)
    bundle = _Bundle(
        frozenset(
            {
                ('activity', 'intervention', 'job-1'),
                ('activity', 'intervention', 'job-2'),
            }
        ),
        base.routes[0].stops,
    )

    slack = _bundle_slack(
        instance,
        base,
        0,
        bundle,
        _context(base, base_evaluation=analysis_record),
        analysis,
        unknown_if_incomplete=True,
    )

    assert slack is None


def test_window_feasible_swap_star_uses_complete_units_on_mixed_routes(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    instance = instance._replace(
        activity_relations=instance.activity_relations._replace(
            precedence=(
                PrecedenceRecord('intervention', 'job-2', 'intervention', 'job-4'),
            )
        )
    )
    operator = WindowFeasibleSwapStarOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=4,
        max_paths_per_leg=1,
        max_arc_expansion_steps=20,
    )

    result = operator.call(_context(base))

    assert result.candidates
    assert result.candidates[0].m == 1


def test_window_feasible_swap_star_requires_distinct_vehicles(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    same_vehicle = replace(
        base,
        routes=(base.routes[0], replace(base.routes[1], vehicle_id='vehicle-1')),
    )
    operator = WindowFeasibleSwapStarOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
    )

    result = operator.call(_context(same_vehicle))

    assert result.candidates == ()


def test_window_feasible_swap_star_excludes_the_former_insertion_index(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    operator = WindowFeasibleSwapStarOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=4,
        max_paths_per_leg=1,
        max_arc_expansion_steps=20,
    )

    result = operator.call(_context(base))

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert _activity_location(candidate, 'job-1')[1] == 1
    assert _activity_location(candidate, 'job-3')[1] == 1


def test_window_feasible_swap_star_preserves_tour_end_marker(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            interventions=tuple(
                item._replace(node_id='a') if item.intervention_id == 'job-1' else item
                for item in instance.intervention_data.interventions
            )
        )
    )
    first_route = base.routes[0]
    first_stop = replace(first_route.stops[0], tour_end=True)
    base = replace(
        base,
        routes=(
            replace(
                first_route,
                stops=(first_stop, first_route.stops[1]),
                legs=(RouteLeg(()), RouteLeg(('a-c',)), RouteLeg(('c-a',))),
            ),
            base.routes[1],
        ),
    )
    operator = WindowFeasibleSwapStarOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=4,
        max_paths_per_leg=1,
        max_arc_expansion_steps=20,
    )

    result = operator.call(_context(base))

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    moved_job_1 = next(
        stop
        for route in candidate.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'job-1'
    )
    assert moved_job_1.tour_end is True


def test_window_feasible_swap_star_places_start_depot_pickup_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    instance = instance._replace(
        package_data=instance.package_data._replace(
            packages=(
                PackageRecord('package-1', 'a', 'b', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
            ),
            package_time_windows=(
                PackageTimeWindowRecord('package-1', 'pickup', 1, 0.0, 100.0),
            ),
        )
    )
    package_stops = (
        ActivityStop(
            ActivityRef('pickup', 'package-1'), frozenset(), 0.0, 0.0, 0.0, False
        ),
        ActivityStop(
            ActivityRef('delivery', 'package-1'), frozenset(), 0.0, 0.0, 0.0, False
        ),
    )
    base = replace(
        base,
        routes=(
            replace(
                base.routes[0],
                stops=package_stops,
                legs=(RouteLeg(()), RouteLeg(('a-b',)), RouteLeg(('b-a',))),
            ),
            base.routes[1],
        ),
    )
    operator = WindowFeasibleSwapStarOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
        max_paths_per_leg=1,
        max_arc_expansion_steps=20,
    )
    monkeypatch.setattr(
        structural_module,
        '_sample_integer_indices',
        lambda _population, _sample, _random_source: (1,),
    )

    result = operator.call(
        _context(
            base,
            random_source=cast(RandomSource, _UpperRandomSource()),
        )
    )

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    destination = next(
        route for route in candidate.routes if route.vehicle_id == 'vehicle-2'
    )
    assert isinstance(destination.stops[0], ActivityStop)
    assert destination.stops[0].activity_ref == ActivityRef('pickup', 'package-1')
    assert any(
        isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('delivery', 'package-1')
        for stop in destination.stops[1:]
    )


def test_window_feasible_swap_star_caps_complete_alternatives_per_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    added_interventions = (
        InterventionRecord('job-5', 'f', 0.0, 1),
        InterventionRecord('job-6', 'g', 0.0, 1),
    )
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            interventions=(
                *instance.intervention_data.interventions,
                *added_interventions,
            )
        )
    )
    first_route, second_route = base.routes
    base = replace(
        base,
        routes=(
            replace(
                first_route,
                stops=(
                    *first_route.stops,
                    ActivityStop(
                        ActivityRef('intervention', 'job-5'),
                        frozenset(),
                        0.0,
                        0.0,
                        0.0,
                        False,
                    ),
                ),
                legs=(
                    *first_route.legs[:2],
                    RouteLeg(('c-f',)),
                    RouteLeg(('f-a',)),
                ),
            ),
            replace(
                second_route,
                stops=(
                    *second_route.stops,
                    ActivityStop(
                        ActivityRef('intervention', 'job-6'),
                        frozenset(),
                        0.0,
                        0.0,
                        0.0,
                        False,
                    ),
                ),
                legs=(
                    *second_route.legs[:2],
                    RouteLeg(('e-g',)),
                    RouteLeg(('g-a',)),
                ),
            ),
        ),
    )
    generated = 0

    def fake_rebuild(
        _instance: LoadedVrpInstance,
        solution: VrpSolution,
        replacements: Mapping[int, tuple[RouteStop, ...]],
        *,
        random_source: RandomSource,
        max_paths_per_leg: int,
        max_arc_expansion_steps: int,
        max_candidate_alternatives: int,
        path_search_index: PathSearchIndex | None = None,
    ) -> tuple[VrpSolution, ...]:
        nonlocal generated
        routes = list(solution.routes)
        for route_index, stops in replacements.items():
            routes[route_index] = replace(routes[route_index], stops=stops)
        candidates: list[VrpSolution] = []
        route_index = next(iter(replacements))
        for _ in range(max_candidate_alternatives):
            route = routes[route_index]
            routes[route_index] = replace(
                route,
                legs=(RouteLeg((f'fake-path-{generated}',)), *route.legs[1:]),
            )
            candidates.append(replace(solution, routes=tuple(routes)))
            generated += 1
        return tuple(candidates)

    def fake_assess(
        _self: _WindowStructuralOperator,
        _candidate: VrpSolution,
        *,
        ensure_feasible: bool,
    ) -> tuple[object, ...]:
        return (0.0, 0.0)

    monkeypatch.setattr(service_window_common, 'rebuild_changed_routes', fake_rebuild)
    monkeypatch.setattr(_WindowStructuralOperator, '_assess', fake_assess)
    monkeypatch.setattr(
        structural_module,
        '_sample_integer_indices',
        lambda _population, sample_size, _random_source: (4, 8)[:sample_size],
    )
    operator = WindowFeasibleSwapStarOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=2,
    )

    result = operator.call(_context(base))

    assert result.candidates
    assert generated <= 2


def test_window_feasible_swap_star_enforces_requested_feasibility(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)

    def infeasible_assess(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(
            True, False, tuple(0.0 for _ in declaration.objectives)
        )

    operator = WindowFeasibleSwapStarOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, infeasible_assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=2,
        max_paths_per_leg=1,
        max_arc_expansion_steps=20,
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.candidates == ()


def test_ejection_chain_consolidation_removes_a_source_route(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    operator = WindowPrioritizedEjectionChainConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=2,
        max_ejection_chain_depth=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert result.candidates[0].m == 1
    assert len(candidate.routes) == len(base.routes) - 1
    assert _windowed_activity_ids(instance, candidate) == {'job-1', 'job-3'}


def test_ejection_chain_reassigns_a_displaced_unit_to_a_third_route(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    first, second = base.routes
    all_stops = (*first.stops, *second.stops)
    three_routes = (
        replace(
            first,
            stops=(all_stops[0],),
            legs=(RouteLeg(('a-b',)), RouteLeg(('b-a',))),
        ),
        replace(
            second,
            stops=(all_stops[1], all_stops[2]),
            legs=(RouteLeg(('a-c',)), RouteLeg(('c-d',)), RouteLeg(('d-a',))),
        ),
        replace(
            second,
            vehicle_id='vehicle-3',
            stops=(all_stops[3],),
            legs=(RouteLeg(('a-e',)), RouteLeg(('e-a',))),
        ),
    )
    base = replace(base, routes=three_routes)
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=(
                *instance.fleet_data.vehicles,
                VehicleRecord('vehicle-3', 100.0, 1, 'depot-a', 1),
            ),
            vehicle_overnight_depots=(
                *instance.fleet_data.vehicle_overnight_depots,
                VehicleOvernightDepotRecord('vehicle-3', 'depot-a'),
            ),
        )
    )

    def capacity_and_compatibility_assessor(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        jobs_by_vehicle = {
            route.vehicle_id: tuple(
                stop.activity_ref.activity_id
                for stop in route.stops
                if isinstance(stop, ActivityStop)
            )
            for route in solution.routes
        }
        feasible = all(len(jobs) <= 2 for jobs in jobs_by_vehicle.values()) and (
            'job-1' not in jobs_by_vehicle.get('vehicle-3', ())
        )
        return ApplicationEvaluation(
            True,
            feasible,
            (float(len(solution.routes)), 0.0)
            + tuple(0.0 for _ in declaration.objectives[2:]),
        )

    operator = WindowPrioritizedEjectionChainConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, capacity_and_compatibility_assessor),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_ejection_chain_depth=1,
        max_candidate_alternatives=16,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert result.candidates[0].m == 1
    assert len(candidate.routes) == 2
    jobs_by_vehicle = {
        route.vehicle_id: tuple(
            stop.activity_ref.activity_id
            for stop in route.stops
            if isinstance(stop, ActivityStop)
        )
        for route in candidate.routes
    }
    assert 'job-1' in jobs_by_vehicle['vehicle-2']
    assert 'job-1' not in jobs_by_vehicle.get('vehicle-3', ())
    assert sorted(job for jobs in jobs_by_vehicle.values() for job in jobs) == [
        'job-1',
        'job-2',
        'job-3',
        'job-4',
    ]


@pytest.mark.parametrize('k', (0, 3))
def test_ejection_chain_rejects_invalid_size(tmp_path: Path, k: int) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    operator = WindowPrioritizedEjectionChainConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=2,
        max_ejection_chain_depth=2,
    )

    with pytest.raises(ValueError):
        operator.call(_context(base, k=k))


def test_ejection_chain_orders_units_uniformly_without_complete_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    operator = WindowPrioritizedEjectionChainConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
    )
    units = tuple(
        _Bundle(
            frozenset({keys}),
            tuple(
                stop
                for stop in base.routes[0].stops
                if isinstance(stop, ActivityStop)
                and ('activity', stop.activity_ref.kind, stop.activity_ref.activity_id)
                == keys
            ),
        )
        for keys in (
            ('activity', 'intervention', 'job-1'),
            ('activity', 'intervention', 'job-2'),
        )
    )

    def unexpected_count(*_args: object, **_kwargs: object) -> int:
        raise AssertionError('direct placements require complete base analysis')

    monkeypatch.setattr(
        WindowPrioritizedEjectionChainConsolidationOperator,
        '_count_direct_feasible_placements',
        unexpected_count,
    )
    ordered = operator._order_source_units(
        base,
        0,
        units,
        (1,),
        _context(base, random_source=cast(RandomSource, _UpperRandomSource())),
    )

    assert ordered[0].keys == units[1].keys


def test_ejection_chain_unknown_slack_joins_known_slack_tie(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            intervention_time_windows=(
                *instance.intervention_data.intervention_time_windows,
                InterventionTimeWindowRecord('job-2', 1, 1, 0.0, 100.0),
            )
        )
    )
    operator = WindowPrioritizedEjectionChainConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
    )
    units = tuple(
        _Bundle(
            frozenset({('activity', 'intervention', activity_id)}),
            (stop,),
        )
        for activity_id, stop in zip(
            ('job-1', 'job-2'), base.routes[0].stops, strict=True
        )
    )
    analysis_record = _base_evaluation(
        instance,
        objectives,
        base,
        {'job-1': 10.0},
        unavailable=frozenset({'job-2'}),
    )
    monkeypatch.setattr(
        WindowPrioritizedEjectionChainConsolidationOperator,
        '_count_direct_feasible_placements',
        lambda *_a, **_k: 1,
    )

    ordered = operator._order_source_units(
        base,
        0,
        units,
        (1,),
        _context(
            base,
            base_evaluation=analysis_record,
            random_source=cast(RandomSource, _UpperRandomSource()),
        ),
    )

    assert ordered[0].keys == units[1].keys


def test_ejection_chain_bounds_direct_count_by_candidate_batch_size(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    assessment_count = 0

    def count_assess(
        instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessment_count
        assessment_count += 1
        return _assess(instance, declaration, solution)

    operator = WindowPrioritizedEjectionChainConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, count_assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=20,
    )
    bundle = _Bundle(
        frozenset({('activity', 'intervention', 'job-1')}),
        (base.routes[0].stops[0],),
    )
    context = _context(
        base,
        base_evaluation=_base_evaluation(
            instance,
            objectives,
            base,
            {'job-1': 10.0, 'job-2': 11.0},
        ),
    )

    feasible_count = operator._count_direct_feasible_placements(
        base, 0, bundle, (1,), context
    )

    assert feasible_count == 1
    assert assessment_count == 1


def test_ejection_chain_recomputes_unit_order_after_each_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    operator = WindowPrioritizedEjectionChainConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
        max_paths_per_leg=1,
        max_arc_expansion_steps=20,
    )
    original_order = (
        WindowPrioritizedEjectionChainConsolidationOperator._order_source_units
    )
    order_bases: list[VrpSolution] = []

    def recording_order(
        self: WindowPrioritizedEjectionChainConsolidationOperator,
        plan: VrpSolution,
        source_index: int,
        units: tuple[_Bundle, ...],
        receivers: tuple[int, ...],
        context: CandidateContext,
        *,
        reference_base: VrpSolution | None = None,
    ) -> tuple[_Bundle, ...]:
        order_bases.append(plan)
        return original_order(
            self,
            plan,
            source_index,
            units,
            receivers,
            context,
            reference_base=reference_base,
        )

    monkeypatch.setattr(
        WindowPrioritizedEjectionChainConsolidationOperator,
        '_order_source_units',
        recording_order,
    )

    analysis_record = _base_evaluation(
        instance,
        objectives,
        base,
        {'job-1': 10.0, 'job-2': 11.0, 'job-3': 12.0, 'job-4': 13.0},
    )
    operator._consolidation_candidates(
        base,
        _context(base, base_evaluation=analysis_record),
    )

    assert len(order_bases) >= 2
    assert order_bases[0] == base
    assert order_bases[1] != base


def test_ejection_chain_keeps_contained_units_on_a_mixed_route(
    tmp_path: Path,
) -> None:
    instance, _objectives, base = _windowed_problem(tmp_path)
    instance = instance._replace(
        activity_relations=instance.activity_relations._replace(
            precedence=(
                PrecedenceRecord('intervention', 'job-2', 'intervention', 'job-4'),
            )
        )
    )

    ejectable = _route_bundles(instance, base, 0)

    assert tuple(bundle.keys for bundle in ejectable) == (
        frozenset({('activity', 'intervention', 'job-1')}),
    )


def test_ejection_chain_rejects_a_source_with_a_cross_route_unit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    instance = instance._replace(
        activity_relations=instance.activity_relations._replace(
            precedence=(
                PrecedenceRecord('intervention', 'job-2', 'intervention', 'job-4'),
            )
        )
    )
    operator = WindowPrioritizedEjectionChainConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
    )

    assert operator._consolidation_candidates(base, _context(base)) == ()


def test_ejection_chain_considers_each_sampled_source_route(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    operator = WindowPrioritizedEjectionChainConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=2,
        max_paths_per_leg=1,
        max_arc_expansion_steps=20,
    )

    candidates = operator._consolidation_candidates(base, _context(base))

    assert len(candidates) == 2
    assert {candidate.routes[0].vehicle_id for candidate, _values in candidates} == {
        'vehicle-1',
        'vehicle-2',
    }


def test_ejection_chain_places_start_depot_pickups_first(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    instance = instance._replace(
        package_data=instance.package_data._replace(
            packages=(
                PackageRecord('package-1', 'a', 'b', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
            ),
            package_time_windows=(
                PackageTimeWindowRecord('package-1', 'pickup', 1, 0.0, 100.0),
            ),
        )
    )
    package_stops = (
        ActivityStop(
            ActivityRef('pickup', 'package-1'), frozenset(), 0.0, 0.0, 0.0, False
        ),
        ActivityStop(
            ActivityRef('delivery', 'package-1'), frozenset(), 0.0, 0.0, 0.0, False
        ),
    )
    base = replace(
        base,
        routes=(
            replace(
                base.routes[0],
                stops=package_stops,
                legs=(RouteLeg(()), RouteLeg(('a-b',)), RouteLeg(('b-a',))),
            ),
            base.routes[1],
        ),
    )
    operator = WindowPrioritizedEjectionChainConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
    )
    package_bundle = _Bundle(frozenset({('package', 'package-1')}), package_stops)

    alternatives = operator._place_unit_with_ejections(
        base,
        package_bundle,
        0,
        (1,),
        _context(base, random_source=cast(RandomSource, _UpperRandomSource())),
    )

    assert alternatives
    first_receiver_stop = alternatives[0].routes[1].stops[0]
    assert isinstance(first_receiver_stop, ActivityStop)
    assert first_receiver_stop.activity_ref == ActivityRef('pickup', 'package-1')


def test_ejection_chain_preserves_the_selected_insertion_position(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_problem(tmp_path)
    first, second = base.routes
    route_set = (
        replace(
            first,
            stops=(first.stops[0],),
            legs=(RouteLeg(('a-b',)), RouteLeg(('b-a',))),
        ),
        replace(
            second,
            stops=second.stops,
            legs=(RouteLeg(('a-d',)), RouteLeg(('d-e',)), RouteLeg(('e-a',))),
        ),
        replace(
            second,
            vehicle_id='vehicle-3',
            stops=(first.stops[1],),
            legs=(RouteLeg(('a-c',)), RouteLeg(('c-a',))),
        ),
    )
    base = replace(base, routes=route_set)
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=(
                *instance.fleet_data.vehicles,
                VehicleRecord('vehicle-3', 100.0, 1, 'depot-a', 1),
            ),
            vehicle_overnight_depots=(
                *instance.fleet_data.vehicle_overnight_depots,
                VehicleOvernightDepotRecord('vehicle-3', 'depot-a'),
            ),
        )
    )
    operator = WindowPrioritizedEjectionChainConsolidationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assess),
        window_objective_id='window-penalty',
        max_neighborhood_size=1,
        max_ejection_chain_depth=1,
        max_candidate_alternatives=4,
    )
    source_bundle = _Bundle(
        frozenset({('activity', 'intervention', 'job-1')}), (first.stops[0],)
    )

    alternatives = operator._place_unit_with_ejections(
        base,
        source_bundle,
        0,
        (1, 2),
        _context(
            base,
            random_source=cast(
                RandomSource,
                _SequenceRandomSource((0.0, 0.0, 0.99, 0.0, 0.0)),
            ),
        ),
    )

    assert len(alternatives) >= 2
    receiver_jobs = tuple(
        stop.activity_ref.activity_id
        for stop in alternatives[1].routes[1].stops
        if isinstance(stop, ActivityStop)
    )
    assert receiver_jobs[:2] == ('job-1', 'job-3')
