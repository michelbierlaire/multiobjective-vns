"""Tests for exact-size and capacity-repair Operators.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

from typing import cast

from multiobjective_vns.callbacks import Operator
from multiobjective_vns.knapsack.instance import (
    KnapsackInstance,
    KnapsackObjective,
    KnapsackResource,
)
from multiobjective_vns.knapsack.operators.exact_flip import ExactFlipOperator
from multiobjective_vns.knapsack.operators.repair import CapacityRepairOperator
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult


class PreserveOrderRandomSource:
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


def run(
    operator: Operator,
    base: tuple[int, ...],
    *,
    k: int,
    ensure_feasible: bool,
) -> OperatorResult:
    context = CandidateContext(
        base=base,
        k=k,
        ensure_feasible=ensure_feasible,
        operator_state=None,
        random_source=PreserveOrderRandomSource(),
    )
    return operator.call(context)


def result_solution(result: OperatorResult) -> tuple[int, ...] | None:
    if not result.candidates:
        return None
    return cast(tuple[int, ...], result.candidates[0].candidate.solution)


def test_exact_flip_changes_exactly_k_distinct_bits() -> None:
    result = run(
        ExactFlipOperator(make_instance()),
        (1, 1, 0, 0),
        k=2,
        ensure_feasible=False,
    )

    assert result.candidates[0].m == 2
    assert result_solution(result) == (0, 0, 0, 0)


def test_exact_flip_discards_infeasible_final_vector_when_requested() -> None:
    result = run(
        ExactFlipOperator(make_instance()),
        (1, 1, 1, 1),
        k=1,
        ensure_feasible=True,
    )

    assert result.candidates == ()


def test_capacity_repair_stops_at_first_feasible_removal() -> None:
    result = run(
        CapacityRepairOperator(make_instance()),
        (1, 1, 1, 0),
        k=2,
        ensure_feasible=True,
    )

    assert result.candidates[0].m == 1
    assert result_solution(result) == (0, 1, 1, 0)


def test_capacity_repair_unfiltered_call_returns_infeasible_sample() -> None:
    result = run(
        CapacityRepairOperator(make_instance()),
        (1, 1, 1, 1),
        k=1,
        ensure_feasible=False,
    )

    assert result.candidates[0].m == 1
    assert result_solution(result) == (0, 1, 1, 1)


def test_capacity_repair_from_feasible_base_retains_all_sampled_removals() -> None:
    result = run(
        CapacityRepairOperator(make_instance()),
        (1, 1, 0, 0),
        k=2,
        ensure_feasible=True,
    )

    assert result.candidates[0].m == 2
    assert result_solution(result) == (0, 0, 0, 0)
