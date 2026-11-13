"""Tests for depot-visit insertion and removal Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from random import Random
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    LoadedVrpInstance,
)
from multiobjective_vns.multiday_vrp.operators import depot_visit as depot_visit_module
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.depot_visit import (
    DepotVisitOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


def _context(base: VrpSolution) -> CandidateContext:
    return CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )


def _random_context(
    base: VrpSolution, seed: int, *, max_candidates: int
) -> CandidateContext:
    return CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, Random(seed)),
        max_candidates=max_candidates,
    )


def test_depot_visit_insertion_adds_depot_stop_and_paths(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='insert',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert (
        sum(
            type(stop) is DepotOnlyStop
            for route in changed.routes
            for stop in route.stops
        )
        == sum(
            type(stop) is DepotOnlyStop for route in base.routes for stop in route.stops
        )
        + 1
    )
    inserted = tuple(
        stop
        for route in changed.routes
        for stop in route.stops
        if type(stop) is DepotOnlyStop
        and all(
            type(stop) is not DepotOnlyStop or stop != original
            for original_route in base.routes
            for original in original_route.stops
        )
    )
    assert len(inserted) == 1
    assert inserted[0].pre_service_wait == 0.0
    assert inserted[0].wait == 0.0
    assert inserted[0].break_duration == 0.0
    assert inserted[0].tour_end is False


def test_depot_visit_insertion_returns_multiple_randomized_proposals(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='insert',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = _random_context(base, 73, max_candidates=4)

    result = operator.call(context)

    assert len(result.candidates) == 4
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    original_stop_count = sum(len(route.stops) for route in base.routes)
    assert len(set(solutions)) == len(solutions)
    assert all(proposal.m == 1 for proposal in result.candidates)
    assert all(
        sum(len(route.stops) for route in solution.routes) == original_stop_count + 1
        for solution in solutions
    )


def test_depot_visit_insertion_returns_objective_ranked_proposals(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        score = sum(
            sum(ord(character) for arc_id in leg.arc_ids for character in arc_id)
            for route in candidate.routes
            for leg in route.legs
        )
        return ApplicationEvaluation(True, True, (float(score),))

    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='insert',
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = _random_context(base, 79, max_candidates=5)

    result = operator.call(context)

    scores = tuple(
        sum(
            sum(ord(character) for arc_id in leg.arc_ids for character in arc_id)
            for route in cast(VrpSolution, proposal.candidate.solution).routes
            for leg in route.legs
        )
        for proposal in result.candidates
    )
    assert len(result.candidates) == 5
    assert scores == tuple(sorted(scores))


def test_depot_visit_insertion_returns_two_sequential_insertions_when_k_is_two(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='insert',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, Random(83)),
        max_candidates=3,
    )

    result = operator.call(context)

    original_stop_count = sum(len(route.stops) for route in base.routes)
    assert result.candidates
    assert len(result.candidates) <= 3
    assert all(proposal.m == 2 for proposal in result.candidates)
    assert all(
        sum(
            len(route.stops)
            for route in cast(VrpSolution, proposal.candidate.solution).routes
        )
        == original_stop_count + 2
        for proposal in result.candidates
    )


def test_depot_visit_insertion_respects_feasibility_and_candidate_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    assessments = 0

    def infeasible_assessment(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return ApplicationEvaluation(True, False, (0.0,))

    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, infeasible_assessment),
        mode='insert',
        max_neighborhood_size=1,
        max_candidate_alternatives=3,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, Random(89)),
        max_candidates=8,
    )

    result = operator.call(context)

    assert result.candidates == ()
    assert assessments <= 3


def test_depot_visit_removal_removes_intermediate_depot_stop(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    route = base.routes[0]
    base = replace(
        base,
        routes=(
            replace(
                route,
                stops=(replace(route.stops[0], tour_end=True), *route.stops[1:]),
            ),
            *base.routes[1:],
        ),
    )
    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='remove',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert (
        sum(
            type(stop) is DepotOnlyStop
            for route in changed.routes
            for stop in route.stops
        )
        == sum(
            type(stop) is DepotOnlyStop for route in base.routes for stop in route.stops
        )
        - 1
    )
    assert (
        sum(stop.tour_end for route in changed.routes for stop in route.stops)
        == sum(stop.tour_end for route in base.routes for stop in route.stops) - 1
    )


def test_depot_visit_removal_returns_multiple_randomized_proposals(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='remove',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = _random_context(base, 103, max_candidates=4)

    result = operator.call(context)

    assert len(result.candidates) == 4
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    original_stop_count = sum(len(route.stops) for route in base.routes)
    assert len(set(solutions)) == len(solutions)
    assert all(proposal.m == 1 for proposal in result.candidates)
    assert all(
        sum(len(route.stops) for route in solution.routes) == original_stop_count - 1
        for solution in solutions
    )


def test_depot_visit_removal_returns_objective_ranked_proposals(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        score = sum(
            len(leg.arc_ids) for route in candidate.routes for leg in route.legs
        )
        return ApplicationEvaluation(True, True, (float(score),))

    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='remove',
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = _random_context(base, 107, max_candidates=5)

    result = operator.call(context)

    scores = tuple(
        sum(
            len(leg.arc_ids)
            for route in cast(VrpSolution, proposal.candidate.solution).routes
            for leg in route.legs
        )
        for proposal in result.candidates
    )
    assert len(result.candidates) == 5
    assert scores == tuple(sorted(scores))


def test_depot_visit_removal_randomizes_objective_ties(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='remove',
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    candidate_context = _random_context(base, 127, max_candidates=8)
    candidates = operator._removal_candidates(base, (0, 0, 'b'), candidate_context)
    tied_candidates = tuple((solution, (0.0,)) for solution, _values in candidates)

    def ordered(seed: int) -> tuple[VrpSolution, ...]:
        context = _random_context(base, seed, max_candidates=8)
        return tuple(
            candidate
            for candidate, _values in depot_visit_module._order_candidates(
                tied_candidates,
                objectives,
                0,
                context,
            )
        )

    assert len(tied_candidates) >= 3
    assert ordered(131) != ordered(137)


def test_depot_visit_removal_returns_two_sequential_removals_when_k_is_two(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='remove',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, Random(109)),
        max_candidates=3,
    )

    result = operator.call(context)

    original_stop_count = sum(len(route.stops) for route in base.routes)
    assert result.candidates
    assert len(result.candidates) <= 3
    assert all(proposal.m == 2 for proposal in result.candidates)
    assert all(
        sum(
            len(route.stops)
            for route in cast(VrpSolution, proposal.candidate.solution).routes
        )
        == original_stop_count - 2
        for proposal in result.candidates
    )


def test_depot_visit_removal_respects_feasibility_and_candidate_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    assessments = 0

    def infeasible_assessment(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return ApplicationEvaluation(True, False, (0.0,))

    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, infeasible_assessment),
        mode='remove',
        max_neighborhood_size=1,
        max_candidate_alternatives=2,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, Random(113)),
        max_candidates=8,
    )

    result = operator.call(context)

    assert result.candidates == ()
    assert assessments == 2


def test_depot_visit_removal_requires_a_sampled_replacement_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='remove',
        max_neighborhood_size=1,
        max_arc_expansion_steps=500,
    )
    context = _context(base)

    def sampled_path(
        _instance: LoadedVrpInstance,
        origin: str,
        destination: str,
        **_limits: int,
    ) -> tuple[tuple[str, ...], ...]:
        return ((),) if (origin, destination) == ('a', 'c') else ()

    monkeypatch.setattr(depot_visit_module, 'sample_loopless_paths', sampled_path)

    assert operator._targets(base, context) == ((0, 0, 'b'),)


def test_depot_visit_removal_moves_boundary_to_last_same_depot_stop(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            interventions=(InterventionRecord('intervention-1', 'b', 0.0, 1),)
        )
    )
    route = base.routes[0]
    activity = ActivityStop(
        ActivityRef('intervention', 'intervention-1'),
        frozenset(),
        0.0,
        0.0,
        0.0,
        False,
    )
    base = replace(
        base,
        routes=(
            replace(
                route,
                stops=(route.stops[0], activity, route.stops[1]),
                legs=(
                    route.legs[0],
                    RouteLeg(()),
                    route.legs[1],
                    route.legs[2],
                ),
            ),
            *base.routes[1:],
        ),
    )
    marked_route = base.routes[0]
    base = replace(
        base,
        routes=(
            replace(
                marked_route,
                stops=(
                    replace(marked_route.stops[0], tour_end=True),
                    *marked_route.stops[1:],
                ),
            ),
            *base.routes[1:],
        ),
    )
    operator = DepotVisitOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='remove',
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    migrated = changed.routes[0].stops[0]
    assert isinstance(migrated, ActivityStop)
    assert migrated.tour_end is True
