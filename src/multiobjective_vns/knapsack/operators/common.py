"""Shared validation, randomized sampling, and result helpers for Operators.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

from collections.abc import Iterable
from math import isfinite

from multiobjective_vns.knapsack.evaluation import knapsack_is_feasible
from multiobjective_vns.knapsack.instance import KnapsackInstance
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.values import require_integer


def require_base(
    context: CandidateContext, instance: KnapsackInstance
) -> tuple[int, ...]:
    """Validate and return a representation-valid base tuple."""
    base = context.base
    if not isinstance(base, tuple) or len(base) != instance.n_items:
        raise ValueError('Operator base must be an item-length tuple')
    if any(type(value) is not int or value not in (0, 1) for value in base):
        raise ValueError('Operator base must contain only binary integers')
    return base


def validate_positions(
    instance: KnapsackInstance, positions: tuple[int, ...] | None
) -> tuple[int, ...]:
    """Return unique eligible item positions or the complete item range."""
    if positions is None:
        return tuple(range(instance.n_items))
    if not isinstance(positions, tuple):
        raise TypeError('eligible_positions must be a tuple or None')
    for position in positions:
        require_integer(position, name='eligible position', minimum=0)
        if position >= instance.n_items:
            raise ValueError('eligible position is outside the item range')
    if len(set(positions)) != len(positions):
        raise ValueError('eligible positions must be unique')
    return positions


def randomized_sample(
    values: Iterable[int], count: int, context: CandidateContext
) -> tuple[int, ...]:
    """Return up to ``count`` values in a uniform order without replacement."""
    pool = list(values)
    selected_count = min(count, len(pool))
    for position in range(selected_count):
        remaining = len(pool) - position
        draw = context.random_source.random()
        if type(draw) is not float or not isfinite(draw) or not 0.0 <= draw < 1.0:
            raise ValueError(
                'RandomSource.random() must return a finite float in [0, 1)'
            )
        offset = min(int(draw * remaining), remaining - 1)
        selected_position = position + offset
        pool[position], pool[selected_position] = (
            pool[selected_position],
            pool[position],
        )
    return tuple(pool[:selected_count])


def result_for(solution: tuple[int, ...], modification_count: int) -> OperatorResult:
    """Create the candidate result for a positive modification count."""
    if modification_count == 0:
        return OperatorResult(candidates=())
    return OperatorResult(
        candidates=(
            CandidateProposal(Candidate(solution=solution), modification_count),
        )
    )


def finish_with_feasibility(
    instance: KnapsackInstance,
    solution: tuple[int, ...],
    modification_count: int,
    ensure_feasible: bool,
) -> OperatorResult:
    """Return a candidate only when changes occurred and feasibility is met."""
    if modification_count == 0:
        return OperatorResult(candidates=())
    if ensure_feasible and not knapsack_is_feasible(instance, solution):
        return OperatorResult(candidates=())
    return result_for(solution, modification_count)
