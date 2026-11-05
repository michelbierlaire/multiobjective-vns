"""Tests for preference profiles and profile-guided Operators.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.callbacks import Operator
from multiobjective_vns.knapsack.instance import (
    KnapsackInstance,
    KnapsackObjective,
    KnapsackResource,
)
from multiobjective_vns.knapsack.operators.profile import (
    PreferenceProfile,
    ProfileCoreAddOperator,
    ProfileCoreExchangeOperator,
    ProfileCoreFlipOperator,
    ProfileCoreRemoveOperator,
    ProfiledCapacityRepairOperator,
)
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult


class FirstAlternativeRandomSource:
    component_id = 'test:random'
    configuration_id = 'test:random-v1'

    def random(self) -> float:
        return 0.0

    def capture_state(self) -> object:
        return None

    def restore_state(self, payload: object) -> None:
        assert payload is None


def make_instance() -> KnapsackInstance:
    return KnapsackInstance(
        problem_instance_id='teaching-v1',
        items=('item-1', 'item-2', 'item-3', 'item-4'),
        resources=(
            KnapsackResource('resource-1', 4, (2, 2, 1, 3)),
            KnapsackResource('resource-2', 4, (2, 1, 2, 3)),
        ),
        objectives=(
            KnapsackObjective('profit-1', 'Profit 1', (8, 5, 4, 9)),
            KnapsackObjective('profit-2', 'Profit 2', (2, 6, 4, 10)),
        ),
    )


def make_profile() -> PreferenceProfile:
    return PreferenceProfile(
        objective_weights=(0.5, 0.5),
        resource_prices=(0.0, 0.0),
        core_width=1,
    )


def call(
    operator: Operator,
    base: tuple[int, ...],
    *,
    k: int = 1,
    ensure_feasible: bool = True,
) -> OperatorResult:
    return operator.call(
        CandidateContext(
            base=base,
            k=k,
            ensure_feasible=ensure_feasible,
            operator_state=None,
            random_source=FirstAlternativeRandomSource(),
        )
    )


def candidate_solution(result: OperatorResult) -> tuple[int, ...] | None:
    if result.candidate is None:
        return None
    solution = result.candidate.solution
    assert isinstance(solution, tuple)
    return solution


def test_profile_remove_uses_low_selected_and_high_excluded_boundary_items() -> None:
    instance = make_instance()
    operator = ProfileCoreRemoveOperator(instance, make_profile())

    result = call(operator, (1, 1, 0, 0))

    assert result.m == 1
    assert candidate_solution(result) == (1, 0, 0, 0)


def test_profile_add_and_flip_use_only_boundary_positions() -> None:
    instance = make_instance()
    profile = make_profile()
    add_result = call(
        ProfileCoreAddOperator(instance, profile),
        (1, 0, 1, 0),
        ensure_feasible=False,
    )
    flip_result = call(
        ProfileCoreFlipOperator(instance, profile),
        (1, 1, 0, 0),
    )

    assert candidate_solution(add_result) == (1, 1, 1, 0)
    assert candidate_solution(flip_result) == (1, 0, 0, 0)


def test_profile_exchange_restricts_both_sides_to_boundary() -> None:
    operator = ProfileCoreExchangeOperator(make_instance(), make_profile())

    result = call(operator, (0, 0, 0, 1), ensure_feasible=False)

    assert result.m == 1
    assert candidate_solution(result) == (0, 1, 0, 0)


def test_profile_capacity_repair_removes_lowest_scoring_boundary_item() -> None:
    operator = ProfiledCapacityRepairOperator(make_instance(), make_profile())

    result = call(operator, (1, 1, 1, 0))

    assert result.m == 1
    assert candidate_solution(result) == (1, 0, 1, 0)


@pytest.mark.parametrize(
    'weights, prices, width',
    [
        ((), (0.0, 0.0), 1),
        ((0.5, 0.5), (), 1),
        ((0.5, 0.5), (0.0,), 1),
        ((0.4, 0.4), (0.0, 0.0), 1),
        ((0.5, -0.5), (0.0, 0.0), 1),
        ((0.5, 0.5), (0.0, float('inf')), 1),
    ],
)
def test_profile_rejects_invalid_configuration(
    weights: tuple[float, ...], prices: tuple[float, ...], width: int
) -> None:
    with pytest.raises((TypeError, ValueError)):
        profile = PreferenceProfile(weights, prices, width)
        ProfileCoreFlipOperator(make_instance(), profile)


def test_profile_rejects_boolean_or_nonpositive_width() -> None:
    with pytest.raises((TypeError, ValueError)):
        PreferenceProfile((0.5, 0.5), (0.0, 0.0), True)
    with pytest.raises((TypeError, ValueError)):
        PreferenceProfile((0.5, 0.5), (0.0, 0.0), 0)
