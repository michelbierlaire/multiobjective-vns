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

    :param budget_limits: Per-invocation hard limits.
    :param max_neighborhood_size: Optional run-level cap for Operator sizes.
    :param feasibility_mode: ``strict`` or ``relaxed`` search mode.
    :param max_infeasible_steps: Required positive limit in relaxed mode.
    :param exhaustive_identity_validation: Enable the diagnostic full scan.
    :param composition_selection_probability: Probability of selecting the
        composition category when both categories are available.
    :param feedback_selection_mode: Built-in choice-selection strategy.
    :param feedback_epsilon: Strictly positive minimum choice probability.
    :param feedback_prior_attempts: Positive prior sample weight.
    :param feedback_admission_reward_floor: Positive reward for a new Pareto
        admission before stagnation adjustment.
    :param feedback_stagnation_scale: Stagnation count where the reward reaches
        halfway between its floor and one.
    :param feedback_recent_attempt_window: Number of committed attempts kept
        for feedback selection.
    :param standalone_prior_reward: Prior reward for standalone choices.
    :param grouped_composition_prior_reward: Prior reward for grouped pairs.
    :param ungrouped_composition_prior_reward: Prior reward for ungrouped pairs.
    :param random_seed: Seed for the package-provided random source.
    """

    budget_limits: BudgetLimits = BudgetLimits()
    max_neighborhood_size: int | None = None
    feasibility_mode: FeasibilityMode = 'strict'
    max_infeasible_steps: int | None = None
    exhaustive_identity_validation: bool = False
    composition_selection_probability: FiniteReal = 0.20
    feedback_selection_mode: FeedbackSelectionMode = 'performance_matching'
    feedback_epsilon: FiniteReal = 0.10
    feedback_prior_attempts: FiniteReal = 5.0
    feedback_admission_reward_floor: FiniteReal = 0.50
    feedback_stagnation_scale: int = 50
    feedback_recent_attempt_window: int = 500
    standalone_prior_reward: FiniteReal = 0.50
    grouped_composition_prior_reward: FiniteReal = 0.90
    ungrouped_composition_prior_reward: FiniteReal = 0.10
    random_seed: int = 0

    def __post_init__(self) -> None:
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
        if epsilon > 1.0:
            raise ValueError('feedback_epsilon must be in (0, 1]')
        object.__setattr__(self, 'feedback_epsilon', epsilon)
        object.__setattr__(
            self,
            'feedback_prior_attempts',
            _positive_real(
                self.feedback_prior_attempts, name='feedback_prior_attempts'
            ),
        )
        reward_floor = _positive_real(
            self.feedback_admission_reward_floor,
            name='feedback_admission_reward_floor',
        )
        if reward_floor > 1.0:
            raise ValueError('feedback_admission_reward_floor must be in (0, 1]')
        object.__setattr__(self, 'feedback_admission_reward_floor', reward_floor)
        require_integer(
            self.feedback_stagnation_scale,
            name='feedback_stagnation_scale',
            minimum=1,
        )
        require_integer(
            self.feedback_recent_attempt_window,
            name='feedback_recent_attempt_window',
            minimum=1,
        )
        for name in (
            'standalone_prior_reward',
            'grouped_composition_prior_reward',
            'ungrouped_composition_prior_reward',
        ):
            object.__setattr__(self, name, _probability(getattr(self, name), name=name))
        require_integer(self.random_seed, name='random_seed')
