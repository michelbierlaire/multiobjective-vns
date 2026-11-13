"""Tests for knapsack identity, codec, objectives, and evaluation callbacks.

Created: 2026-10-29T17:44:53+00:00
Author: Michel Bierlaire, EPFL
"""

from typing import cast

import pytest

from multiobjective_vns.knapsack.codec import KnapsackSolutionCodec
from multiobjective_vns.knapsack.evaluation import KnapsackApplicationEvaluator
from multiobjective_vns.knapsack.identity import KnapsackSolutionIdentity
from multiobjective_vns.knapsack.instance import (
    KnapsackInstance,
    KnapsackObjective,
    KnapsackResource,
)
from multiobjective_vns.knapsack.objectives import build_objective_declaration


@pytest.fixture
def instance() -> KnapsackInstance:
    return KnapsackInstance(
        problem_instance_id='tiny-v1',
        items=('a', 'b', 'c'),
        resources=(KnapsackResource('weight', 3, (1, 2, 3)),),
        objectives=(
            KnapsackObjective('profit-1', 'Profit 1', (2, 5, 7)),
            KnapsackObjective('profit-2', 'Profit 2', (4, 3, 8)),
        ),
    )


def test_solution_identity_uses_exact_tuple_values() -> None:
    identity = KnapsackSolutionIdentity()

    assert identity.equivalent((0, 1), (0, 1))
    assert not identity.equivalent((0, 1), (1, 0))
    assert not identity.equivalent((1,), (True,))
    assert identity.lookup_key((0, 2, 0)) == (0, 2, 0)


def test_solution_identity_requires_integer_tuples() -> None:
    identity = KnapsackSolutionIdentity()

    with pytest.raises(TypeError):
        identity.equivalent([0, 1], [0, 1])
    with pytest.raises(TypeError):
        identity.lookup_key((0, '1'))


def test_solution_codec_round_trips_valid_and_malformed_tuples() -> None:
    codec = KnapsackSolutionCodec()
    valid = (0, 1, 0)
    malformed = (0, 2)
    malformed_boolean = (False, True)

    assert codec.restore(codec.capture(valid)) == valid
    assert codec.restore(codec.capture(malformed)) == malformed
    assert codec.restore(codec.capture(malformed_boolean)) == malformed_boolean
    assert codec.capture(valid) == b'[0,1,0]'


def test_solution_codec_rejects_values_outside_its_tuple_contract() -> None:
    codec = KnapsackSolutionCodec()

    with pytest.raises(TypeError):
        codec.capture([0, 1])
    with pytest.raises(TypeError):
        codec.restore(cast(bytes, [0, 1]))
    with pytest.raises(ValueError):
        codec.restore(b'{bad json')


def test_objective_declaration_minimizes_negated_profit(
    instance: KnapsackInstance,
) -> None:
    declaration = build_objective_declaration(instance)
    first, second = declaration.objectives

    assert first.compare(-5.0, -7.0).value == 'greater'
    assert second.compare(-7.0, -7.0).value == 'equal'
    assert str(first) == 'Profit 1'
    assert first.format(-7.0) == '-7'
    assert first.component_id != second.component_id


def test_evaluator_returns_values_for_feasible_and_infeasible_tuples(
    instance: KnapsackInstance,
) -> None:
    declaration = build_objective_declaration(instance)
    evaluator = KnapsackApplicationEvaluator(instance)

    feasible = evaluator.evaluate((1, 1, 0), declaration)
    infeasible = evaluator.evaluate((1, 0, 1), declaration)

    assert feasible.representation_valid is True
    assert feasible.feasible is True
    assert feasible.objective_values == (-7.0, -7.0)
    assert infeasible.representation_valid is True
    assert infeasible.feasible is False
    assert infeasible.objective_values == (-9.0, -12.0)


@pytest.mark.parametrize('solution', [(1, 0), (1, 2, 0), (True, 0, 0), [1, 1, 0]])
def test_evaluator_reports_invalid_representations(
    instance: KnapsackInstance, solution: object
) -> None:
    evaluation = KnapsackApplicationEvaluator(instance).evaluate(
        solution, build_objective_declaration(instance)
    )

    assert evaluation.representation_valid is False
    assert evaluation.feasible is None
    assert evaluation.objective_values is None


def test_evaluator_checks_objective_order_matches_instance(
    instance: KnapsackInstance,
) -> None:
    declaration = build_objective_declaration(instance)
    evaluator = KnapsackApplicationEvaluator(instance)
    reversed_declaration = type(declaration)(tuple(reversed(declaration.objectives)))

    with pytest.raises(ValueError, match='objective order'):
        evaluator.evaluate((1, 0, 0), reversed_declaration)
