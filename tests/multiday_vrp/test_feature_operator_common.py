"""Tests for shared feature-specific Operator helpers.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _test_plan


@dataclass(frozen=True)
class _Objective:
    objective_id: str = 'cost'
    component_id: str = 'cost'
    configuration_id: str = 'cost-v1'

    def compare(self, left: object, right: object) -> ObjectiveComparison:
        if left < right:  # type: ignore[operator]
            return ObjectiveComparison.LESS
        if left > right:  # type: ignore[operator]
            return ObjectiveComparison.GREATER
        return ObjectiveComparison.EQUAL

    def format(self, value: object) -> str:
        return str(value)

    def __str__(self) -> str:
        return 'Cost'


def test_assess_complete_candidate_returns_application_evaluation(
    tmp_path: Path,
) -> None:
    from multiobjective_vns.multiday_vrp.operators.feature_common import (
        assess_complete_candidate,
    )

    instance, objectives, solution = _test_plan(tmp_path)

    def assessor(*_args: object) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (3.0,))

    result = assess_complete_candidate(
        instance,
        objectives,
        solution,
        assessor,  # type: ignore[arg-type]
        ensure_feasible=True,
    )

    assert result is not None
    assert result.objective_values == (3.0,)


def test_assess_complete_candidate_filters_invalid_and_infeasible(
    tmp_path: Path,
) -> None:
    from multiobjective_vns.multiday_vrp.operators.feature_common import (
        assess_complete_candidate,
    )

    instance, objectives, solution = _test_plan(tmp_path)

    def invalid(*_args: object) -> ApplicationEvaluation:
        return ApplicationEvaluation(False, None, None)

    def infeasible(*_args: object) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, False, (3.0,))

    assert (
        assess_complete_candidate(
            instance,
            objectives,
            solution,
            invalid,  # type: ignore[arg-type]
            ensure_feasible=False,
        )
        is None
    )
    assert (
        assess_complete_candidate(
            instance,
            objectives,
            solution,
            infeasible,  # type: ignore[arg-type]
            ensure_feasible=True,
        )
        is None
    )


def test_ranked_candidates_uses_declared_objective_comparison(
    tmp_path: Path,
) -> None:
    from multiobjective_vns.multiday_vrp.operators.feature_common import (
        choose_best_candidate,
    )

    instance, _objectives, _solution = _test_plan(tmp_path)
    objectives = ObjectiveDeclaration((_Objective(),))
    candidates = (('first', (9.0,)), ('second', (2.0,)), ('third', (5.0,)))

    selected = choose_best_candidate(candidates, objectives, 0)

    assert selected == ('second', (2.0,))


def test_objective_index_rejects_unknown_objective() -> None:
    from multiobjective_vns.multiday_vrp.operators.feature_common import (
        objective_position,
    )

    with pytest.raises(ValueError, match='not declared'):
        objective_position(ObjectiveDeclaration((_Objective(),)), 'missing')


def test_complete_candidate_requires_valid_assessor_result(
    tmp_path: Path,
) -> None:
    from multiobjective_vns.multiday_vrp.operators.feature_common import (
        assess_complete_candidate,
    )

    instance, objectives, solution = _test_plan(tmp_path)

    def malformed(*_args: object) -> object:
        return object()

    with pytest.raises(TypeError, match='ApplicationEvaluation'):
        assess_complete_candidate(
            instance,
            objectives,
            solution,
            malformed,  # type: ignore[arg-type]
            ensure_feasible=False,
        )
