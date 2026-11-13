"""Tests for explicit multi-tour split and merge Operators.

Created: 2026-11-04T16:47:56+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from random import Random
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    LoadedVrpInstance,
)
from multiobjective_vns.multiday_vrp.operators import (
    MergeToursOperator,
    SplitTourOperator,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def random(self) -> float:
        return 0.0


def _context(
    base: VrpSolution,
    *,
    k: int = 1,
    ensure_feasible: bool = False,
) -> CandidateContext:
    return CandidateContext(
        base,
        k,
        ensure_feasible,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )


def _set_tour_limit(instance: LoadedVrpInstance, maximum: int) -> LoadedVrpInstance:
    vehicles = tuple(
        vehicle._replace(max_tours_per_day=maximum)
        for vehicle in instance.fleet_data.vehicles
    )
    return instance._replace(fleet_data=instance.fleet_data._replace(vehicles=vehicles))


def _mark_all_depot_stops(solution: VrpSolution) -> VrpSolution:
    routes = tuple(
        replace(
            route,
            stops=tuple(replace(stop, tour_end=True) for stop in route.stops),
        )
        for route in solution.routes
    )
    return replace(solution, routes=routes)


def _tour_count(solution: VrpSolution) -> int:
    return sum(
        1 + sum(stop.tour_end for stop in route.stops) for route in solution.routes
    )


def _use_intervention_at_depot(
    instance: LoadedVrpInstance, base: VrpSolution
) -> tuple[LoadedVrpInstance, VrpSolution]:
    intervention = InterventionRecord('intervention-1', 'b', 0.0, 1)
    intervention_data = instance.intervention_data._replace(
        interventions=(intervention,)
    )
    route = base.routes[0]
    stops = (
        ActivityStop(
            ActivityRef('intervention', 'intervention-1'),
            frozenset(),
            0.0,
            0.0,
            0.0,
            False,
        ),
        *route.stops[1:],
    )
    routes = (replace(route, stops=stops), *base.routes[1:])
    return (
        instance._replace(intervention_data=intervention_data),
        replace(base, routes=routes),
    )


def test_split_tour_marks_an_existing_intermediate_depot_visit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 2)
    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=3,
        max_candidate_alternatives=1,
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert _tour_count(changed) == _tour_count(base) + 1
    assert any(stop.tour_end for stop in changed.routes[0].stops)


def test_split_tour_can_insert_and_mark_a_new_depot_visit(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 5)
    base = _mark_all_depot_stops(base)
    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert _tour_count(changed) == _tour_count(base) + 1
    assert (
        sum(len(route.stops) for route in changed.routes)
        == sum(len(route.stops) for route in base.routes) + 1
    )
    assert sum(stop.tour_end for route in changed.routes for stop in route.stops) == (
        sum(stop.tour_end for route in base.routes for stop in route.stops) + 1
    )


def test_split_tour_can_mark_an_activity_stop_at_a_depot(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance, base = _use_intervention_at_depot(instance, base)
    instance = _set_tour_limit(instance, 2)
    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
    )

    result = operator.call(_context(base))

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert isinstance(changed.routes[0].stops[0], ActivityStop)
    assert changed.routes[0].stops[0].tour_end is True


def test_split_tour_returns_no_move_at_the_vehicle_tour_limit(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 1)
    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
    )

    result = operator.call(_context(base))

    assert result.candidates == ()
    assert not result.candidates


def test_split_tour_returns_multiple_randomized_proposals(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 5)
    base = _mark_all_depot_stops(base)
    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, Random(13)),
        max_candidates=4,
    )

    result = operator.call(context)

    assert len(result.candidates) == 4
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(solutions)) == len(solutions)
    assert all(proposal.m == 1 for proposal in result.candidates)
    assert all(_tour_count(solution) == _tour_count(base) + 1 for solution in solutions)


def test_split_tour_returns_objective_ranked_proposals(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 5)
    base = _mark_all_depot_stops(base)

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        value = sum(
            (route_index + 1)
            * sum(
                stop_index + 1
                for stop_index, stop in enumerate(route.stops)
                if stop.tour_end
            )
            for route_index, route in enumerate(candidate.routes)
        )
        return ApplicationEvaluation(True, True, (float(value),))

    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, Random(19)),
        max_candidates=5,
    )

    result = operator.call(context)

    assert len(result.candidates) == 5
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    scores = tuple(
        sum(
            (route_index + 1)
            * sum(
                stop_index + 1
                for stop_index, stop in enumerate(route.stops)
                if stop.tour_end
            )
            for route_index, route in enumerate(solution.routes)
        )
        for solution in solutions
    )
    assert scores == tuple(sorted(scores))


def test_split_tour_randomizes_objective_ties(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 5)
    base = _mark_all_depot_stops(base)

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (0.0,))

    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )

    def run(seed: int) -> tuple[VrpSolution, ...]:
        context = CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, Random(seed)),
            max_candidates=5,
        )
        result = operator.call(context)
        return tuple(
            cast(VrpSolution, proposal.candidate.solution)
            for proposal in result.candidates
        )

    first = run(31)
    second = run(37)

    assert len(first) == len(second) == 5
    assert first != second


def test_split_tour_returns_two_sequential_splits_when_k_is_two(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 4)
    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, Random(23)),
        max_candidates=3,
    )

    result = operator.call(context)

    assert result.candidates
    assert len(result.candidates) == 3
    for proposal in result.candidates:
        candidate = cast(VrpSolution, proposal.candidate.solution)
        assert proposal.m == 2
        assert _tour_count(candidate) == _tour_count(base) + 2


def test_split_tour_respects_feasibility_and_candidate_limit(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 5)
    base = _mark_all_depot_stops(base)
    assessments = 0

    def infeasible_assessment(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return ApplicationEvaluation(True, False, (0.0,))

    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, infeasible_assessment),
        max_neighborhood_size=1,
        max_candidate_alternatives=3,
        max_paths_per_leg=4,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, Random(29)),
        max_candidates=8,
    )

    result = operator.call(context)

    assert result.candidates == ()
    assert assessments <= 3


def test_merge_tours_clears_boundary_and_preserves_depot_stop(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 3)
    base = _mark_all_depot_stops(base)
    original_stops = base.routes[0].stops
    operator = MergeToursOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert _tour_count(changed) == _tour_count(base) - 1
    assert len(changed.routes[0].stops) == len(original_stops)
    assert (
        tuple(replace(stop, tour_end=True) for stop in changed.routes[0].stops)
        == original_stops
    )


def test_merge_tours_can_clear_boundary_on_activity_at_depot(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance, base = _use_intervention_at_depot(instance, base)
    instance = _set_tour_limit(instance, 3)
    route = base.routes[0]
    stops = (replace(route.stops[0], tour_end=True), *route.stops[1:])
    base = replace(base, routes=(replace(route, stops=stops), *base.routes[1:]))
    operator = MergeToursOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
    )

    result = operator.call(_context(base))

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert isinstance(changed.routes[0].stops[0], ActivityStop)
    assert changed.routes[0].stops[0].tour_end is False


def test_merge_tours_objective_variant_selects_best_boundary(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 2)
    base = _mark_all_depot_stops(base)

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(
            True,
            True,
            (float(candidate.routes[0].stops[0].tour_end),),
        )

    operator = MergeToursOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=8,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.routes[0].stops[0].tour_end is False


def test_merge_tours_returns_multiple_randomized_proposals(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    base = _mark_all_depot_stops(base)
    operator = MergeToursOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, Random(43)),
        max_candidates=3,
    )

    result = operator.call(context)

    assert len(result.candidates) == 3
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(solutions)) == len(solutions)
    assert all(proposal.m == 1 for proposal in result.candidates)
    assert all(_tour_count(solution) == _tour_count(base) - 1 for solution in solutions)
    assert all(
        tuple(
            replace(stop, tour_end=original.tour_end)
            for stop, original in zip(
                changed_route.stops, original_route.stops, strict=True
            )
        )
        == original_route.stops
        for solution in solutions
        for changed_route, original_route in zip(
            solution.routes, base.routes, strict=True
        )
    )


def test_merge_tours_returns_objective_ranked_proposals(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    base = _mark_all_depot_stops(base)

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        score = sum(
            (2 ** (route_index * 2 + stop_index)) * stop.tour_end
            for route_index, route in enumerate(candidate.routes)
            for stop_index, stop in enumerate(route.stops)
        )
        return ApplicationEvaluation(True, True, (float(score),))

    operator = MergeToursOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=8,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, Random(47)),
        max_candidates=4,
    )

    result = operator.call(context)

    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    scores = tuple(
        sum(
            (2 ** (route_index * 2 + stop_index)) * stop.tour_end
            for route_index, route in enumerate(solution.routes)
            for stop_index, stop in enumerate(route.stops)
        )
        for solution in solutions
    )
    assert len(result.candidates) == 4
    assert scores == tuple(sorted(scores))


def test_merge_tours_returns_two_sequential_merges_when_k_is_two(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    base = _mark_all_depot_stops(base)
    operator = MergeToursOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, Random(53)),
        max_candidates=3,
    )

    result = operator.call(context)

    assert len(result.candidates) == 3
    for proposal in result.candidates:
        solution = cast(VrpSolution, proposal.candidate.solution)
        assert proposal.m == 2
        assert _tour_count(solution) == _tour_count(base) - 2


def test_merge_tours_respects_feasibility_and_candidate_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    base = _mark_all_depot_stops(base)
    assessments = 0

    def infeasible_assessment(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return ApplicationEvaluation(True, False, (0.0,))

    operator = MergeToursOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, infeasible_assessment),
        max_neighborhood_size=1,
        max_candidate_alternatives=2,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, Random(59)),
        max_candidates=8,
    )

    result = operator.call(context)

    assert result.candidates == ()
    assert assessments == 2
