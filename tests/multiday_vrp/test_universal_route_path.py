"""Tests for generic route-path Operators.

Created: 2026-11-03T20:40:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.route_path import (
    AdditivePathCostCallback,
    AdditivePathCostRequest,
    ObjectiveSpecificShortestPathOperator,
    RouteLegRerouteOperator,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _always_assess, _context, _test_plan


def test_route_leg_reroute_selects_a_different_path(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = RouteLegRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.candidates
    assert result.candidates[0].m == 1
    assert result.candidates[0].candidate.solution != base
    selected = result.candidates[0].candidate.solution
    assert isinstance(selected, VrpSolution)
    assert selected.routes[0].legs[0] != base.routes[0].legs[0]


def test_route_leg_reroute_returns_sampled_paths_up_to_context_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = RouteLegRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, max_candidates=4))

    assert len(result.candidates) == 4
    candidates = tuple(proposal.candidate.solution for proposal in result.candidates)
    assert all(candidate != base for candidate in candidates)
    assert len(set(candidates)) == len(candidates)
    assert all(proposal.m == 1 for proposal in result.candidates)
    assert all(
        candidate.routes[0].legs[0] != base.routes[0].legs[0]
        for candidate in candidates
    )


def test_route_leg_reroute_applies_successful_legs_sequentially(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = RouteLegRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, k=2))

    assert result.candidates
    proposal = result.candidates[0]
    assert proposal.m == 2
    candidate = proposal.candidate.solution
    assert isinstance(candidate, VrpSolution)
    assert candidate.routes[0].legs[0] != base.routes[0].legs[0]
    assert candidate.routes[0].legs[1] != base.routes[0].legs[1]


def test_restoration_reroute_accepts_an_infeasible_base(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, solution != base, (0.0,))

    operator = RouteLegRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.candidates
    assert result.candidates[0].m == 1
    assert result.candidates[0].candidate.solution != base


def test_objective_specific_operator_uses_registered_additive_costs(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    objective_id = objectives.objectives[0].objective_id
    seen_requests: list[AdditivePathCostRequest] = []

    def cost(arc_id: str, request: AdditivePathCostRequest) -> float:
        seen_requests.append(request)
        if arc_id == 'a-b':
            return 10.0
        return 1.0

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (0.0,))

    operator = ObjectiveSpecificShortestPathOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objective_id,
        additive_path_cost=cost,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, max_candidates=4))

    assert len(result.candidates) == 1
    assert result.candidates
    assert result.candidates[0].m == 1
    selected = result.candidates[0].candidate.solution
    assert isinstance(selected, VrpSolution)
    assert selected.routes[0].legs[0].arc_ids != ('a-b',)
    assert seen_requests
    assert all(request.plan == base for request in seen_requests)


def test_objective_specific_operator_ranks_sampled_complete_plans(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        value = (
            100.0
            if any(
                'a-b' in leg.arc_ids for route in solution.routes for leg in route.legs
            )
            else 1.0
        )
        return ApplicationEvaluation(True, True, (value,))

    operator = ObjectiveSpecificShortestPathOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        additive_path_cost=None,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.candidates
    assert result.candidates[0].m == 1
    selected = result.candidates[0].candidate.solution
    assert isinstance(selected, VrpSolution)
    selected_evaluation = assessor(instance, objectives, selected)
    assert selected_evaluation.objective_values is not None
    selected_value = selected_evaluation.objective_values[0]
    assert selected_value == 1.0


def test_objective_specific_operator_returns_ranked_batch_up_to_context_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        path = solution.routes[0].legs[0].arc_ids
        value = float(sum(ord(character) for arc_id in path for character in arc_id))
        return ApplicationEvaluation(True, True, (value,))

    operator = ObjectiveSpecificShortestPathOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        objective_id=objectives.objectives[0].objective_id,
        additive_path_cost=None,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, max_candidates=4))

    assert len(result.candidates) == 4
    values: list[object] = []
    solutions: list[VrpSolution] = []
    for proposal in result.candidates:
        candidate = proposal.candidate.solution
        assert isinstance(candidate, VrpSolution)
        solutions.append(candidate)
        evaluation = assessor(instance, objectives, candidate)
        assert evaluation.objective_values is not None
        values.append(evaluation.objective_values[0])
        assert proposal.m == 1
    assert len(set(solutions)) == len(solutions)
    objective = objectives.objectives[0]
    assert all(
        objective.compare(left, right) is not ObjectiveComparison.GREATER
        for left, right in zip(values[:-1], values[1:], strict=True)
    )


def test_route_leg_reroute_returns_objective_ranked_candidates(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    objective_id = objectives.objectives[0].objective_id

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        path_length = len(solution.routes[0].legs[0].arc_ids)
        return ApplicationEvaluation(True, True, (float(path_length),))

    operator = RouteLegRerouteOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        max_neighborhood_size=1,
        objective_id=objective_id,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base, max_candidates=4))

    assert len(result.candidates) == 4
    values: list[object] = []
    for proposal in result.candidates:
        candidate = proposal.candidate.solution
        assert isinstance(candidate, VrpSolution)
        evaluation = assessor(instance, objectives, candidate)
        assert evaluation.objective_values is not None
        values.append(evaluation.objective_values[0])
        assert proposal.m == 1
    objective = objectives.objectives[0]
    assert all(
        objective.compare(left, right) is not ObjectiveComparison.GREATER
        for left, right in zip(values[:-1], values[1:], strict=True)
    )


def test_exact_path_operator_rejects_negative_additive_cost(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def cost(_arc_id: str, _request: AdditivePathCostRequest) -> float:
        return -1.0

    operator = ObjectiveSpecificShortestPathOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        objective_id=objectives.objectives[0].objective_id,
        additive_path_cost=cast(AdditivePathCostCallback, cost),
        max_neighborhood_size=1,
    )

    with pytest.raises(ValueError, match='nonnegative'):
        operator.call(_context(base))
