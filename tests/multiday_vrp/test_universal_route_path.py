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
from multiobjective_vns.objectives import ObjectiveDeclaration
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

    assert result.m == 1
    assert result.candidate is not None
    assert result.candidate.solution != base
    selected = result.candidate.solution
    assert isinstance(selected, VrpSolution)
    assert selected.routes[0].legs[0] != base.routes[0].legs[0]


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

    assert result.m == 1
    assert result.candidate is not None
    assert result.candidate.solution != base


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

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    selected = result.candidate.solution
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

    assert result.m == 1
    assert result.candidate is not None
    selected = result.candidate.solution
    assert isinstance(selected, VrpSolution)
    selected_evaluation = assessor(instance, objectives, selected)
    assert selected_evaluation.objective_values is not None
    selected_value = selected_evaluation.objective_values[0]
    assert selected_value == 1.0


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
