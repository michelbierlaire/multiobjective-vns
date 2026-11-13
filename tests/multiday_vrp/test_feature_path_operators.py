"""Tests for feature-specific path Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from random import Random
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    ArcTraversalEvaluation,
    LegScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import (
    EventRecord,
    EventTypeRecord,
    ObjectiveEventTypeRecord,
    VrpEventData,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.feature_path import (
    EventObjectiveRouteRerouteOperator,
    ScenarioDifferentialRerouteOperator,
    _event_legs,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveComparison
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _test_plan


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


def _analysis(
    solution: VrpSolution, *, variable_leg: bool = True
) -> VrpOperatorAnalysis:
    scenarios = []
    for scenario_number, multiplier in ((1, 1.0), (2, 1.0)):
        vehicle_days = []
        for route_index, route in enumerate(solution.routes):
            legs = []
            for leg_index, leg in enumerate(route.legs):
                duration = multiplier
                if (
                    variable_leg
                    and scenario_number == 2
                    and route_index == 1
                    and leg_index == 0
                ):
                    duration = 8.0
                traversals = tuple(
                    ArcTraversalEvaluation(arc_id, 0.0, duration, duration)
                    for arc_id in leg.arc_ids
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
                f'scenario-{scenario_number}',
                0.5,
                tuple(vehicle_days),
            )
        )
    return VrpOperatorAnalysis(True, tuple(scenarios), (), ())


def _assessor(
    _instance: object,
    _objectives: object,
    solution: VrpSolution,
) -> ApplicationEvaluation:
    return ApplicationEvaluation(
        True, True, (float(len(solution.routes)),), _analysis(solution)
    )


def _context(solution: VrpSolution, *, max_candidates: int = 1) -> CandidateContext:
    return CandidateContext(
        solution,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
        None,
        max_candidates,
    )


def test_scenario_differential_reroute_targets_variable_leg(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assessor(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        score = float(
            sum(
                sum(ord(character) for character in arc_id)
                for route in solution.routes
                for leg in route.legs
                for arc_id in leg.arc_ids
            )
        )
        return ApplicationEvaluation(True, True, (score,), _analysis(solution))

    operator = ScenarioDifferentialRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _SeededRandomSource(82)),
            max_candidates=4,
        )
    )

    assert len(result.candidates) == 4
    assert all(proposal.m == 1 for proposal in result.candidates)
    candidates = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(candidates)) == 4
    assert all(candidate.routes[0] == base.routes[0] for candidate in candidates)
    assert all(
        candidate.routes[1].legs[0] != base.routes[1].legs[0]
        for candidate in candidates
    )
    scores = tuple(
        sum(
            sum(ord(character) for character in arc_id)
            for route in candidate.routes
            for leg in route.legs
            for arc_id in leg.arc_ids
        )
        for candidate in candidates
    )
    assert scores == tuple(sorted(scores))


def test_scenario_differential_reroute_falls_back_when_ranges_are_zero(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assessor(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(
            True, True, (1.0,), _analysis(solution, variable_leg=False)
        )

    operator = ScenarioDifferentialRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.routes[0].legs[0] != base.routes[0].legs[0]
    assert changed.routes[1] == base.routes[1]


def test_scenario_differential_reroute_respects_feasibility(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assessor(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, False, (1.0,), _analysis(solution))

    operator = ScenarioDifferentialRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=4,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, _SeededRandomSource(7)),
        max_candidates=4,
    )

    assert operator.call(context).candidates == ()


def test_event_objective_reroute_targets_event_leg(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    target_arc = base.routes[0].legs[0].arc_ids[0]
    event_data = VrpEventData(
        (EventTypeRecord('signal', 'arc'),),
        (EventRecord('signal-1', 'signal', target_arc),),
        (
            ObjectiveEventTypeRecord(
                str(objectives.objectives[0].objective_id), 'signal'
            ),
        ),
    )
    instance = instance._replace(event_data=event_data)
    assessments = 0

    def assessor(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        arc_count = sum(
            len(leg.arc_ids) for route in solution.routes for leg in route.legs
        )
        return ApplicationEvaluation(
            True, True, (float(arc_count),), _analysis(solution)
        )

    operator = EventObjectiveRouteRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _SeededRandomSource(92)),
            max_candidates=4,
        )
    )

    assert result.candidates
    assert assessments == 8
    assert len(result.candidates) > 1
    assert all(proposal.m == 1 for proposal in result.candidates)
    candidates = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(candidates)) == len(candidates)
    assert all(
        candidate.routes[0].legs[0] != base.routes[0].legs[0]
        for candidate in candidates
    )
    assert all(candidate.routes[1] == base.routes[1] for candidate in candidates)
    objective = objectives.objectives[0]
    values = tuple(
        float(sum(len(leg.arc_ids) for route in candidate.routes for leg in route.legs))
        for candidate in candidates
    )
    assert all(
        objective.compare(left, right)
        in {ObjectiveComparison.LESS, ObjectiveComparison.EQUAL}
        for left, right in zip(values[:-1], values[1:], strict=True)
    )


def test_event_objective_node_occurrence_targets_only_its_counted_arrival_leg(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = instance._replace(
        event_data=VrpEventData(
            (EventTypeRecord('stop-event', 'node'),),
            (EventRecord('event-at-b', 'stop-event', 'b'),),
            (
                ObjectiveEventTypeRecord(
                    str(objectives.objectives[0].objective_id), 'stop-event'
                ),
            ),
        )
    )

    assert _event_legs(instance, base, objectives.objectives[0].objective_id) == (
        (0, 0),
    )
    instance = instance._replace(
        event_data=VrpEventData(
            (EventTypeRecord('depot-event', 'node'),),
            (EventRecord('event-at-a', 'depot-event', 'a'),),
            (
                ObjectiveEventTypeRecord(
                    str(objectives.objectives[0].objective_id), 'depot-event'
                ),
            ),
        )
    )
    assert _event_legs(instance, base, objectives.objectives[0].objective_id) == (
        (0, 0),
        (0, 2),
        (1, 0),
        (1, 2),
    )


def test_event_objective_reroute_uses_any_leg_when_no_event_is_present(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = EventObjectiveRouteRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assessor),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.routes[0].legs[0] != base.routes[0].legs[0]


def test_event_objective_reroute_can_change_two_distinct_event_legs(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    first_arc = base.routes[0].legs[0].arc_ids[0]
    second_arc = base.routes[1].legs[0].arc_ids[0]
    event_data = VrpEventData(
        (EventTypeRecord('signal', 'arc'),),
        (
            EventRecord('signal-1', 'signal', first_arc),
            EventRecord('signal-2', 'signal', second_arc),
        ),
        (
            ObjectiveEventTypeRecord(
                str(objectives.objectives[0].objective_id), 'signal'
            ),
        ),
    )
    instance = instance._replace(event_data=event_data)
    operator = EventObjectiveRouteRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assessor),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _SeededRandomSource(12)),
            max_candidates=4,
        )
    )

    assert result.candidates
    assert any(proposal.m == 2 for proposal in result.candidates)


def test_event_objective_reroute_respects_requested_feasibility(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    target_arc = base.routes[0].legs[0].arc_ids[0]
    instance = instance._replace(
        event_data=VrpEventData(
            (EventTypeRecord('signal', 'arc'),),
            (EventRecord('signal-1', 'signal', target_arc),),
            (
                ObjectiveEventTypeRecord(
                    str(objectives.objectives[0].objective_id), 'signal'
                ),
            ),
        )
    )
    operator = EventObjectiveRouteRerouteOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda *_args: ApplicationEvaluation(True, False, (1.0,)),
        ),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=4,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, _SeededRandomSource(7)),
        max_candidates=4,
    )

    assert operator.call(context).candidates == ()
