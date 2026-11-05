"""Capacity-restoration Operator for knapsack vectors.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.knapsack.evaluation import knapsack_is_feasible
from multiobjective_vns.knapsack.instance import KnapsackInstance
from multiobjective_vns.knapsack.operators.common import (
    finish_with_feasibility,
    randomized_sample,
    require_base,
)
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult


class CapacityRepairOperator:
    """Remove selected items in random order, stopping at the first feasible vector."""

    def __init__(self, instance: KnapsackInstance) -> None:
        self._instance = instance

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply sampled removals and return the first feasible restoration.

        When the base is feasible, all sampled removals are applied. When the
        base is infeasible, processing stops at the first feasible vector.
        """
        base = require_base(context, self._instance)
        base_feasible = knapsack_is_feasible(self._instance, base)
        positions = randomized_sample(
            (index for index, value in enumerate(base) if value == 1),
            context.k,
            context,
        )
        working = list(base)
        modifications = 0
        for position in positions:
            working[position] = 0
            modifications += 1
            if not base_feasible and knapsack_is_feasible(
                self._instance, tuple(working)
            ):
                break

        return finish_with_feasibility(
            self._instance,
            tuple(working),
            modifications,
            context.ensure_feasible,
        )
