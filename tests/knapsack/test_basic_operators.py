"""Tests for randomized bit-change and exchange Operators.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

from collections.abc import Iterable

from multiobjective_vns.callbacks import Operator
from multiobjective_vns.knapsack.instance import (
    KnapsackInstance,
    KnapsackObjective,
    KnapsackResource,
)
from multiobjective_vns.knapsack.operators.basic import (
    AddItemsOperator,
    FlipItemsOperator,
    RemoveItemsOperator,
)
from multiobjective_vns.knapsack.operators.exchange import ExchangeItemsOperator
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    OperatorResult,
)


class SequenceRandomSource:
    component_id = 'test:random'
    configuration_id = 'test:random-v1'

    def __init__(self, values: Iterable[float] = ()) -> None:
        self._values = iter(values)

    def random(self) -> float:
        return next(self._values, 0.0)

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


def call_operator(
    operator: Operator,
    base: tuple[int, ...],
    *,
    k: int,
    ensure_feasible: bool,
    random_values: Iterable[float] = (),
) -> OperatorResult:
    context = CandidateContext(
        base=base,
        k=k,
        ensure_feasible=ensure_feasible,
        operator_state=None,
        random_source=SequenceRandomSource(random_values),
    )
    return operator.call(context)


def assert_result(
    result: OperatorResult, *, m: int, solution: tuple[int, ...] | None
) -> None:
    assert result.m == m
    if solution is None:
        assert result.candidate is None
    else:
        assert isinstance(result.candidate, Candidate)
        assert result.candidate.solution == solution


def test_flip_skips_infeasible_change_and_counts_retained_changes() -> None:
    operator = FlipItemsOperator(make_instance())
    result = call_operator(
        operator,
        (1, 1, 0, 0),
        k=2,
        ensure_feasible=True,
        random_values=(0.6, 0.0),
    )

    assert_result(result, m=1, solution=(1, 0, 0, 0))


def test_add_skips_infeasible_addition_but_unfiltered_call_returns_it() -> None:
    operator = AddItemsOperator(make_instance())

    filtered = call_operator(operator, (1, 1, 0, 0), k=1, ensure_feasible=True)
    unfiltered = call_operator(operator, (1, 1, 0, 0), k=1, ensure_feasible=False)

    assert_result(filtered, m=0, solution=None)
    assert_result(unfiltered, m=1, solution=(1, 1, 1, 0))


def test_add_cannot_restore_an_infeasible_base() -> None:
    result = call_operator(
        AddItemsOperator(make_instance()),
        (1, 1, 1, 0),
        k=1,
        ensure_feasible=True,
    )

    assert_result(result, m=0, solution=None)


def test_remove_keeps_changes_and_checks_final_feasibility() -> None:
    operator = RemoveItemsOperator(make_instance())
    repaired = call_operator(operator, (1, 1, 1, 0), k=1, ensure_feasible=True)
    several = call_operator(operator, (1, 1, 0, 0), k=2, ensure_feasible=True)

    assert_result(repaired, m=1, solution=(0, 1, 1, 0))
    assert_result(several, m=2, solution=(0, 0, 0, 0))


def test_exchange_pairs_randomized_selected_and_unselected_items() -> None:
    result = call_operator(
        ExchangeItemsOperator(make_instance()),
        (1, 1, 0, 0),
        k=1,
        ensure_feasible=True,
    )

    assert_result(result, m=1, solution=(0, 1, 1, 0))


def test_exchange_with_one_full_side_has_no_alternative() -> None:
    result = call_operator(
        ExchangeItemsOperator(make_instance()),
        (1, 1, 1, 1),
        k=1,
        ensure_feasible=False,
    )

    assert_result(result, m=0, solution=None)
