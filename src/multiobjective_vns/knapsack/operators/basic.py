"""Single-bit flip, add, and remove Operators for knapsack vectors.

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


class FlipItemsOperator:
    """Flip up to ``k`` randomly ordered item decisions."""

    def __init__(
        self,
        instance: KnapsackInstance,
        *,
        eligible_positions: tuple[int, ...] | None = None,
    ) -> None:
        self._instance = instance
        self._eligible_positions = validate_positions(instance, eligible_positions)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Flip sampled bits, skipping infeasible changes from feasible bases."""
        base = require_base(context, self._instance)
        positions = randomized_sample(self._eligible_positions, context.k, context)
        working = list(base)
        base_feasible = knapsack_is_feasible(self._instance, base)
        modifications = 0
        for position in positions:
            working[position] = 1 - working[position]
            candidate = tuple(working)
            if (
                context.ensure_feasible
                and base_feasible
                and not knapsack_is_feasible(self._instance, candidate)
            ):
                working[position] = base[position]
                continue
            modifications += 1
        return finish_with_feasibility(
            self._instance,
            tuple(working),
            modifications,
            context.ensure_feasible,
        )


class AddItemsOperator:
    """Add up to ``k`` randomly ordered currently unselected items."""

    def __init__(
        self,
        instance: KnapsackInstance,
        *,
        eligible_positions: tuple[int, ...] | None = None,
    ) -> None:
        self._instance = instance
        self._eligible_positions = validate_positions(instance, eligible_positions)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Add sampled items, filtering capacity violations when requested."""
        base = require_base(context, self._instance)
        base_feasible = knapsack_is_feasible(self._instance, base)
        if context.ensure_feasible and not base_feasible:
            return OperatorResult(candidates=())

        positions = randomized_sample(
            (index for index in self._eligible_positions if base[index] == 0),
            context.k,
            context,
        )
        working = list(base)
        modifications = 0
        for position in positions:
            working[position] = 1
            if context.ensure_feasible and not knapsack_is_feasible(
                self._instance, tuple(working)
            ):
                working[position] = 0
                continue
            modifications += 1
        return finish_with_feasibility(
            self._instance,
            tuple(working),
            modifications,
            context.ensure_feasible,
        )


class RemoveItemsOperator:
    """Remove up to ``k`` randomly ordered currently selected items."""

    def __init__(
        self,
        instance: KnapsackInstance,
        *,
        eligible_positions: tuple[int, ...] | None = None,
    ) -> None:
        self._instance = instance
        self._eligible_positions = validate_positions(instance, eligible_positions)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Remove sampled items and enforce feasibility on the final result."""
        base = require_base(context, self._instance)
        positions = randomized_sample(
            (index for index in self._eligible_positions if base[index] == 1),
            context.k,
            context,
        )
        working = list(base)
        for position in positions:
            working[position] = 0
        return finish_with_feasibility(
            self._instance,
            tuple(working),
            len(positions),
            context.ensure_feasible,
        )
