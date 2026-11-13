"""Disjoint one-for-one exchange Operator for knapsack solutions.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.knapsack.evaluation import knapsack_is_feasible
from multiobjective_vns.knapsack.instance import KnapsackInstance
from multiobjective_vns.knapsack.operators.common import (
    finish_with_feasibility,
    randomized_sample,
    require_base,
    validate_positions,
)
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult


class ExchangeItemsOperator:
    """Swap up to ``k`` disjoint selected and unselected item pairs."""

    def __init__(
        self,
        instance: KnapsackInstance,
        *,
        eligible_positions: tuple[int, ...] | None = None,
    ) -> None:
        self._instance = instance
        self._eligible_positions = validate_positions(instance, eligible_positions)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply randomized exchanges and retain feasible intermediate changes.

        For a feasible base with ``ensure_feasible=True``, each pair is tested
        against the current working solution and skipped if it exceeds capacity.
        """
        base = require_base(context, self._instance)
        selected = (index for index in self._eligible_positions if base[index] == 1)
        unselected = (index for index in self._eligible_positions if base[index] == 0)
        selected_order = randomized_sample(selected, context.k, context)
        unselected_order = randomized_sample(unselected, context.k, context)
        pair_count = min(len(selected_order), len(unselected_order))
        if pair_count == 0:
            return OperatorResult(candidates=())

        base_feasible = knapsack_is_feasible(self._instance, base)
        working = list(base)
        modifications = 0
        for removed, added in zip(
            selected_order[:pair_count],
            unselected_order[:pair_count],
            strict=True,
        ):
            working[removed] = 0
            working[added] = 1
            if (
                context.ensure_feasible
                and base_feasible
                and not knapsack_is_feasible(self._instance, tuple(working))
            ):
                working[removed] = 1
                working[added] = 0
                continue
            modifications += 1

        return finish_with_feasibility(
            self._instance,
            tuple(working),
            modifications,
            context.ensure_feasible,
        )
