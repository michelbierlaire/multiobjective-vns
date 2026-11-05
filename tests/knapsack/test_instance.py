"""Tests for immutable multiobjective knapsack instance records.

Created: 2026-10-29T17:42:44+00:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.knapsack.instance import (
    KnapsackInstance,
    KnapsackObjective,
    KnapsackResource,
)


def make_instance() -> KnapsackInstance:
    return KnapsackInstance(
        problem_instance_id='small-v1',
        items=('a', 'b'),
        resources=(
            KnapsackResource(
                resource_id='weight',
                capacity=3,
                consumption=(1, 2),
            ),
        ),
        objectives=(
            KnapsackObjective(
                objective_id='profit-1',
                name='Profit 1',
                contribution=(2, 5),
            ),
            KnapsackObjective(
                objective_id='profit-2',
                name='Profit 2',
                contribution=(4, 3),
            ),
        ),
    )


def test_instance_normalizes_numeric_data_and_exposes_dimensions() -> None:
    instance = make_instance()

    assert instance.items == ('a', 'b')
    assert instance.resources[0].capacity == 3.0
    assert instance.resources[0].consumption == (1.0, 2.0)
    assert instance.objectives[0].contribution == (2.0, 5.0)
    assert instance.n_items == 2
    assert instance.n_resources == 1
    assert instance.n_objectives == 2


def test_instance_records_are_immutable() -> None:
    instance = make_instance()

    with pytest.raises((AttributeError, TypeError)):
        instance.items = ('b', 'a')  # type: ignore[misc]


@pytest.mark.parametrize(
    'items',
    [(), ('a', 'a'), ('', 'b'), ['a', 'b']],
)
def test_instance_rejects_invalid_item_catalog(items: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        KnapsackInstance(
            problem_instance_id='small-v1',
            items=items,  # type: ignore[arg-type]
            resources=(KnapsackResource('weight', 3, (1, 2)),),
            objectives=(
                KnapsackObjective('profit-1', 'Profit 1', (2, 5)),
                KnapsackObjective('profit-2', 'Profit 2', (4, 3)),
            ),
        )


def test_resource_and_objective_vectors_must_match_item_count() -> None:
    with pytest.raises(ValueError):
        KnapsackInstance(
            problem_instance_id='small-v1',
            items=('a', 'b'),
            resources=(KnapsackResource('weight', 3, (1,)),),
            objectives=(
                KnapsackObjective('profit-1', 'Profit 1', (2, 5)),
                KnapsackObjective('profit-2', 'Profit 2', (4, 3)),
            ),
        )

    with pytest.raises(ValueError):
        KnapsackInstance(
            problem_instance_id='small-v1',
            items=('a', 'b'),
            resources=(KnapsackResource('weight', 3, (1, 2)),),
            objectives=(
                KnapsackObjective('profit-1', 'Profit 1', (2,)),
                KnapsackObjective('profit-2', 'Profit 2', (4, 3)),
            ),
        )


@pytest.mark.parametrize('value', [True, float('inf'), float('nan'), -1])
def test_resource_values_must_be_nonnegative_finite_numbers(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        KnapsackResource('weight', value, (1, 2))  # type: ignore[arg-type]


def test_objective_coefficients_may_be_negative() -> None:
    objective = KnapsackObjective('cost', 'Cost', (-2, 3.5))

    assert objective.contribution == (-2.0, 3.5)


def test_instance_requires_at_least_one_resource_and_two_objectives() -> None:
    with pytest.raises(ValueError):
        KnapsackInstance(
            problem_instance_id='small-v1',
            items=('a',),
            resources=(),
            objectives=(
                KnapsackObjective('profit-1', 'Profit 1', (2,)),
                KnapsackObjective('profit-2', 'Profit 2', (4,)),
            ),
        )

    with pytest.raises(ValueError):
        KnapsackInstance(
            problem_instance_id='small-v1',
            items=('a',),
            resources=(KnapsackResource('weight', 3, (1,)),),
            objectives=(KnapsackObjective('profit-1', 'Profit 1', (2,)),),
        )
