"""Exact-size binary flip Operator for knapsack vectors.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.knapsack.evaluation import knapsack_is_feasible
from multiobjective_vns.knapsack.instance import KnapsackInstance
from multiobjective_vns.knapsack.operators.common import (
    randomized_sample,
    require_base,
    result_for,
)
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult


class ExactFlipOperator:
    """Flip exactly ``k`` distinct item decisions or return no candidate."""

    def __init__(self, instance: KnapsackInstance) -> None:
        self._instance = instance

    def call(self, context: CandidateContext) -> OperatorResult:
        """Sample and flip a size-``k`` subset, then check final feasibility."""
        base = require_base(context, self._instance)
        positions = randomized_sample(range(self._instance.n_items), context.k, context)
        if len(positions) != context.k:
            raise ValueError('exact_flip requires k not to exceed the item count')

        working = list(base)
        for position in positions:
            working[position] = 1 - working[position]
        result = tuple(working)
        if context.ensure_feasible and not knapsack_is_feasible(self._instance, result):
            return OperatorResult(candidates=())
        return result_for(result, context.k)
