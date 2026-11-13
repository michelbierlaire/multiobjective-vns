"""Tests for service-window-guided route-path Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from random import Random
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    StopScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
    WindowScenarioAssessment,
)
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    InterventionTimeWindowRecord,
    LoadedVrpInstance,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.feature_path import (
    TimeWindowGuidedRouteRerouteOperator,
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
from multiobjective_vns.records.status import ApplicationAssessment, ProcessingStatus

from .test_universal_route_order import _always_assess, _test_plan


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


def _windowed_plan(
    tmp_path: Path,
    *,
    windowed_interventions: tuple[str, ...] = (
        'intervention-0',
        'intervention-1',
        'intervention-2',
    ),
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _test_plan(tmp_path)
    intervention_nodes = (
        ('intervention-0', 'b'),
        ('intervention-1', 'c'),
        ('intervention-2', 'd'),
    )
    interventions = tuple(
        InterventionRecord(intervention_id, node_id, 1.0, 1)
        for intervention_id, node_id in intervention_nodes
    )
    windows = tuple(
        InterventionTimeWindowRecord(intervention_id, 1, 0, 0.0, 10.0)
        for intervention_id in windowed_interventions
    )
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            interventions=interventions,
            intervention_time_windows=windows,
        ),
        time_window_violation_objective_id=objectives.objectives[0].objective_id,
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'intervention-0'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
            ActivityStop(
                ActivityRef('intervention', 'intervention-1'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
        legs=(RouteLeg(('a-b',)), RouteLeg(('b-c',)), RouteLeg(('c-a',))),
    )
    routes[1] = replace(
        routes[1],
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'intervention-2'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
        legs=(RouteLeg(('a-d',)), RouteLeg(('d-a',))),
    )
    return instance, objectives, replace(base, routes=tuple(routes))


def _window_analysis(
    solution: VrpSolution,
    objective_id: str,
    guidance_by_route: tuple[tuple[float, ...], ...],
) -> VrpOperatorAnalysis:
    vehicle_days = []
    for route_index, route in enumerate(solution.routes):
        stop_nodes = ('b', 'c') if route_index == 0 else ('d',)
        stops = tuple(
            StopScenarioEvaluation(
                stop_index,
                'activity',
                stop_nodes[stop_index],
                0.0,
                0.0,
                1.0,
                1.0,
                None,
                None,
                route.stops[stop_index].activity_ref,
                None,
                (
                    WindowScenarioAssessment(
                        objective_id,
                        0,
                        0.0,
                        guidance_by_route[route_index][stop_index],
                        guidance_by_route[route_index][stop_index],
                    ),
                )
                if guidance_by_route[route_index][stop_index] > 0.0
                else (),
            )
            for stop_index in range(len(route.stops))
        )
        vehicle_days.append(
            VehicleDayScenarioEvaluation(
                route.vehicle_id, route.day_index, 0.0, 10.0, stops, ()
            )
        )
    scenario = VrpScenarioEvaluation('scenario-1', 1.0, tuple(vehicle_days))
    return VrpOperatorAnalysis(True, (scenario,), (), ())


def _base_record(analysis: VrpOperatorAnalysis) -> EvaluationRecord:
    return EvaluationRecord(
        ProcessingStatus.COMPLETED,
        ApplicationEvaluation(True, True, (0.0,), analysis),
        ApplicationAssessment.VALID,
    )


def test_window_guided_reroute_uses_any_leg_without_positive_guidance(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(tmp_path)
    operator = TimeWindowGuidedRouteRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )

    result = operator.call(context)

    assert result.candidates[0].m == 1
    assert result.candidates


def test_window_guided_reroute_uses_base_analysis_target_and_returns_ranked_paths(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(tmp_path)
    objective_id = objectives.objectives[0].objective_id
    analysis = _window_analysis(base, objective_id, ((1.0, 2.0), (5.0,)))
    base_record = _base_record(analysis)
    assessments: list[VrpSolution] = []

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        assert solution != base
        assessments.append(solution)
        path_length = sum(
            len(leg.arc_ids) for route in solution.routes for leg in route.legs
        )
        return ApplicationEvaluation(True, True, (float(path_length),))

    operator = TimeWindowGuidedRouteRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        objective_id=objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base=base,
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, _SeededRandomSource(22)),
        base_evaluation=base_record,
        max_candidates=3,
    )

    result = operator.call(context)

    assert assessments
    assert len(result.candidates) == 3
    candidates = tuple(proposal.candidate.solution for proposal in result.candidates)
    assert all(isinstance(candidate, VrpSolution) for candidate in candidates)
    assert all(candidate.routes[0] == base.routes[0] for candidate in candidates)
    assert all(
        candidate.routes[1].legs[0] != base.routes[1].legs[0]
        for candidate in candidates
    )
    assert all(proposal.m == 1 for proposal in result.candidates)
    values = tuple(
        sum(len(leg.arc_ids) for route in candidate.routes for leg in route.legs)
        for candidate in candidates
    )
    assert values == tuple(sorted(values))


def test_window_guided_reroute_falls_back_to_windowed_activity_and_counts_k(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(
        tmp_path, windowed_interventions=('intervention-1',)
    )
    operator = TimeWindowGuidedRouteRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=2,
        max_candidate_alternatives=4,
        max_paths_per_leg=4,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base=base,
        k=2,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, _ZeroRandomSource()),
    )

    result = operator.call(context)

    assert result.candidates
    candidate = result.candidates[0].candidate.solution
    assert isinstance(candidate, VrpSolution)
    assert candidate.routes[0].legs[0] != base.routes[0].legs[0]
    assert candidate.routes[0].legs[1] != base.routes[0].legs[1]
    assert candidate.routes[1] == base.routes[1]
    assert result.candidates[0].m == 2


def test_window_guided_reroute_returns_empty_without_windowed_activities(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _windowed_plan(tmp_path, windowed_interventions=())
    operator = TimeWindowGuidedRouteRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
        )
    )

    assert result.candidates == ()
