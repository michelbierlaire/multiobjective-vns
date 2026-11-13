"""Immutable algorithm configuration and per-invocation budget limits.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass
from typing import Literal

from multiobjective_vns.values import FiniteReal, finite_real, require_integer

FeasibilityMode = Literal['strict', 'relaxed']
FeedbackSelectionMode = Literal['uniform', 'performance_matching']


def _positive_real(value: FiniteReal, *, name: str) -> float:
    normalized = finite_real(value, name=name)
    if normalized <= 0:
        raise ValueError(f'{name} must be positive')
    return normalized


def _nonnegative_real(value: FiniteReal, *, name: str) -> float:
    normalized = finite_real(value, name=name)
    if normalized < 0:
        raise ValueError(f'{name} must be nonnegative')
    return normalized


def _probability(value: FiniteReal, *, name: str) -> float:
    normalized = finite_real(value, name=name)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f'{name} must be in [0, 1]')
    return normalized


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """Engine-enforced ceilings applied separately to each run invocation.

    :param max_wall_time_seconds: Positive elapsed-time ceiling.
    :param max_candidate_attempts: Maximum attempts in the invocation.
    :param max_evaluation_requests: Maximum new-solution evaluation requests.
    """

    max_wall_time_seconds: FiniteReal = 300.0
    max_candidate_attempts: int = 10000
    max_evaluation_requests: int = 10000

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'max_wall_time_seconds',
            _positive_real(self.max_wall_time_seconds, name='max_wall_time_seconds'),
        )
        for name in (
            'max_candidate_attempts',
            'max_evaluation_requests',
        ):
            require_integer(getattr(self, name), name=name, minimum=1)


@dataclass(frozen=True, slots=True)
class MultiobjectiveVNSConfig:
    """Built-in search settings shared by all invocations of a search.

    :param max_candidates_per_attempt: Required maximum number of ordered
        candidates requested from a choice in one attempt.
    :param budget_limits: Per-invocation hard limits.
    :param max_neighborhood_size: Optional run-level cap for Operator sizes.
    :param feasibility_mode: ``strict`` or ``relaxed`` search mode.
    :param max_infeasible_steps: Required positive limit in relaxed mode.
    :param exhaustive_identity_validation: Enable the diagnostic full scan.
    :param composition_selection_probability: Probability of selecting the
        composition category when both categories are available.
    :param feedback_selection_mode: Built-in choice-selection strategy.
    :param feedback_epsilon: Strictly positive per-choice probability floor.
    :param feedback_mu: Nonnegative softmax selection intensity.
    :param random_seed: Seed for the package-provided random source.
    """

    max_candidates_per_attempt: int
    budget_limits: BudgetLimits = BudgetLimits()
    max_neighborhood_size: int | None = None
    feasibility_mode: FeasibilityMode = 'strict'
    max_infeasible_steps: int | None = None
    exhaustive_identity_validation: bool = False
    composition_selection_probability: FiniteReal = 0.50
    feedback_selection_mode: FeedbackSelectionMode = 'performance_matching'
    feedback_epsilon: FiniteReal = 1e-6
    feedback_mu: FiniteReal = 1.0
    random_seed: int = 0

    def __post_init__(self) -> None:
        require_integer(
            self.max_candidates_per_attempt,
            name='max_candidates_per_attempt',
            minimum=1,
        )
        if not isinstance(self.budget_limits, BudgetLimits):
            raise TypeError('budget_limits must be a BudgetLimits record')
        if self.max_neighborhood_size is not None:
            require_integer(
                self.max_neighborhood_size,
                name='max_neighborhood_size',
                minimum=1,
            )
        if self.feasibility_mode not in ('strict', 'relaxed'):
            raise ValueError("feasibility_mode must be 'strict' or 'relaxed'")
        if self.max_infeasible_steps is not None:
            require_integer(
                self.max_infeasible_steps,
                name='max_infeasible_steps',
                minimum=1,
            )
        if self.feasibility_mode == 'strict' and self.max_infeasible_steps is not None:
            raise ValueError('strict mode requires max_infeasible_steps=None')
        if self.feasibility_mode == 'relaxed' and self.max_infeasible_steps is None:
            raise ValueError('relaxed mode requires max_infeasible_steps')
        if not isinstance(self.exhaustive_identity_validation, bool):
            raise TypeError('exhaustive_identity_validation must be bool')
        object.__setattr__(
            self,
            'composition_selection_probability',
            _probability(
                self.composition_selection_probability,
                name='composition_selection_probability',
            ),
        )
        if self.feedback_selection_mode not in ('uniform', 'performance_matching'):
            raise ValueError('unsupported feedback_selection_mode')

        epsilon = _positive_real(self.feedback_epsilon, name='feedback_epsilon')
        if epsilon >= 1.0:
            raise ValueError('feedback_epsilon must be in (0, 1)')
        object.__setattr__(self, 'feedback_epsilon', epsilon)
        object.__setattr__(
            self,
            'feedback_mu',
            _nonnegative_real(self.feedback_mu, name='feedback_mu'),
        )
        require_integer(self.random_seed, name='random_seed')
