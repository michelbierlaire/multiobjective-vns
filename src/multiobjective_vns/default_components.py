"""Package-provided random, execution, and policy components.

Created: 2026-10-29T14:26:25+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import random
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import cast

from multiobjective_vns.callbacks import ApplicationEvaluator, StatePayload
from multiobjective_vns.config import MultiobjectiveVNSConfig
from multiobjective_vns.identifiers import ChoiceId
from multiobjective_vns.objectives import ObjectiveComparison
from multiobjective_vns.records.failures import ComponentFailure, FailureKind
from multiobjective_vns.records.policies import (
    ActivePoolDecision,
    ActivePoolRequest,
    ArchiveDecision,
    ArchiveRequest,
    BaseSelectionDecision,
    BaseSelectionRequest,
    BudgetDecision,
    BudgetRequest,
    ChoiceDescriptor,
    ChoiceSelectionRequest,
    FeedbackObservation,
)
from multiobjective_vns.records.results import ExecutionResult


class SeededRandomSource:
    """Deterministic pseudorandom stream initialized by an integer seed."""

    component_id = 'multiobjective-vns:random-source'
    configuration_id = 'multiobjective-vns:random-source-v1'

    def __init__(self, seed: int) -> None:
        self._generator = random.Random(seed)

    def random(self) -> float:
        """Return the next pseudorandom float in ``[0, 1)``."""
        return self._generator.random()

    def capture_state(self) -> StatePayload:
        """Return the generator's opaque continuation state."""
        return cast(StatePayload, self._generator.getstate())

    def restore_state(self, payload: StatePayload) -> None:
        """Restore a state previously returned by :meth:`capture_state`."""
        if not isinstance(payload, tuple):
            raise TypeError('random state must be a tuple')
        self._generator.setstate(cast(tuple[object, ...], payload))


class SequentialExecutor:
    """Invoke one application evaluator synchronously."""

    component_id = 'multiobjective-vns:executor'

    def execute(
        self,
        evaluator: ApplicationEvaluator,
        solution: object,
        objectives: object,
    ) -> ExecutionResult:
        """Return evaluator findings or a callback diagnostic."""
        try:
            result = evaluator.evaluate(solution, objectives)  # type: ignore[arg-type]
        except Exception as error:
            return ExecutionResult(
                None,
                ComponentFailure(
                    FailureKind.CALLBACK_EXCEPTION,
                    evaluator.component_id,
                    'application_evaluation',
                    f'ApplicationEvaluator.evaluate raised: {error}',
                ),
            )
        from multiobjective_vns.records.evaluation import ApplicationEvaluation

        if not isinstance(result, ApplicationEvaluation):
            return ExecutionResult(
                None,
                ComponentFailure(
                    FailureKind.CONTRACT_VIOLATION,
                    evaluator.component_id,
                    'application_evaluation',
                    'ApplicationEvaluator must return ApplicationEvaluation',
                ),
            )
        return ExecutionResult(result, None)


class ParetoArchivePolicy:
    """Apply exact Pareto admission and removal under declared objectives."""

    component_id = 'multiobjective-vns:archive-policy'
    configuration_id = 'multiobjective-vns:archive-policy-v1'

    def decide(self, request: ArchiveRequest) -> ArchiveDecision:
        """Return the exact nondominated archive decision."""
        dominated_by_existing = any(
            self._dominates(
                entry.objective_values,
                request.candidate.objective_values,
                request.objective_declaration,
            )
            for entry in request.current_archive
        )
        if dominated_by_existing:
            return ArchiveDecision(False, ())
        remove_ids = tuple(
            entry.solution_id
            for entry in request.current_archive
            if self._dominates(
                request.candidate.objective_values,
                entry.objective_values,
                request.objective_declaration,
            )
        )
        return ArchiveDecision(True, remove_ids)

    @staticmethod
    def _dominates(
        left: tuple[object, ...], right: tuple[object, ...], declaration: object
    ) -> bool:
        objectives = declaration.objectives  # type: ignore[attr-defined]
        comparisons = tuple(
            objective.compare(left_value, right_value)
            for objective, left_value, right_value in zip(
                objectives, left, right, strict=True
            )
        )
        return all(
            comparison in (ObjectiveComparison.LESS, ObjectiveComparison.EQUAL)
            for comparison in comparisons
        ) and any(comparison is ObjectiveComparison.LESS for comparison in comparisons)


class RetainingExplorationPolicy:
    """Retain eligible active records and add each newly registered record."""

    component_id = 'multiobjective-vns:exploration-policy'
    configuration_id = 'multiobjective-vns:exploration-policy-v1'

    def decide(self, request: ActivePoolRequest) -> ActivePoolDecision:
        """Reconcile eligibility or reduce membership to the Pareto archive."""
        if request.action.value == 'cleanup':
            return ActivePoolDecision(request.archive_ids)
        active = set(request.active_ids & request.eligible_ids)
        if (
            request.changed_solution_id is not None
            and request.changed_solution_id in request.eligible_ids
        ):
            active.add(request.changed_solution_id)
        active.update(request.archive_ids)
        return ActivePoolDecision(frozenset(active))


class LeastAttemptedBasePolicy:
    """Select a least-used eligible record, breaking ties at random."""

    component_id = 'multiobjective-vns:base-selection-policy'
    configuration_id = 'multiobjective-vns:base-selection-policy-v1'

    def select(self, request: BaseSelectionRequest) -> BaseSelectionDecision:
        """Choose an eligible solution with the fewest prior attempts."""
        minimum = min(
            request.attempt_counts[record.solution_id]
            for record in request.eligible_records
        )
        tied = tuple(
            record
            for record in request.eligible_records
            if request.attempt_counts[record.solution_id] == minimum
        )
        if len(tied) == 1:
            selected = tied[0]
        else:
            draw = request.random_source.random()
            selected = tied[min(int(draw * len(tied)), len(tied) - 1)]
        return BaseSelectionDecision(selected.solution_id)


class DefaultBudgetPolicy:
    """Permit work while independent engine ceilings allow it."""

    component_id = 'multiobjective-vns:budget-policy'
    configuration_id = 'multiobjective-vns:budget-policy-v1'

    def decide(self, request: BudgetRequest) -> BudgetDecision:
        """Return an authorization; hard limits remain engine-enforced."""
        del request
        return BudgetDecision(True)


class DefaultFeedbackPolicy:
    """Track difficulty-weighted rewards and sample from recent evidence."""

    component_id = 'multiobjective-vns:feedback-policy'
    configuration_id = 'multiobjective-vns:feedback-policy-v2'

    def __init__(self, config: MultiobjectiveVNSConfig) -> None:
        self._config = config
        self._attempts: defaultdict[ChoiceId, int] = defaultdict(int)
        self._admissions: defaultdict[ChoiceId, int] = defaultdict(int)
        self._m_counts: defaultdict[tuple[ChoiceId, int, int | None], int] = (
            defaultdict(int)
        )
        self._tracked: set[ChoiceId] = set()
        self._reward_history: deque[tuple[ChoiceId, float]] = deque()
        self._recent_attempts: defaultdict[ChoiceId, int] = defaultdict(int)
        self._recent_reward_totals: defaultdict[ChoiceId, float] = defaultdict(float)

    def observe(self, observation: FeedbackObservation) -> None:
        """Update counts for one candidate-attempt commit."""
        self._tracked.add(observation.choice_id)
        self._attempts[observation.choice_id] += 1
        if observation.outcomes and any(
            outcome.disposition is not None and outcome.disposition.value == 'archived'
            for outcome in observation.outcomes
        ):
            self._admissions[observation.choice_id] += 1
        self._m_counts[(observation.choice_id, observation.k, observation.m)] += 1
        reward = float(observation.archive_reward)
        self._reward_history.append((observation.choice_id, reward))
        self._recent_attempts[observation.choice_id] += 1
        self._recent_reward_totals[observation.choice_id] += reward
        while len(self._reward_history) > self._config.feedback_recent_attempt_window:
            expired_choice, expired_reward = self._reward_history.popleft()
            self._recent_attempts[expired_choice] -= 1
            self._recent_reward_totals[expired_choice] = max(
                0.0,
                self._recent_reward_totals[expired_choice] - expired_reward,
            )

    def distribution(self, request: ChoiceSelectionRequest) -> Mapping[ChoiceId, float]:
        """Return the configured random or performance-matching distribution."""
        count = len(request.choices)
        if self._config.feedback_selection_mode == 'uniform':
            return {choice.choice_id: 1.0 / count for choice in request.choices}

        scores: dict[ChoiceId, float] = {}
        for choice in request.choices:
            choice_id = choice.choice_id
            prior_reward = self._prior_reward(choice)
            prior_weight = float(self._config.feedback_prior_attempts)
            score = (
                self._recent_reward_totals[choice_id] + prior_weight * prior_reward
            ) / (self._recent_attempts[choice_id] + prior_weight)
            scores[choice_id] = score
        score_total = sum(scores.values())
        if score_total == 0.0:
            return {choice.choice_id: 1.0 / count for choice in request.choices}
        epsilon = float(self._config.feedback_epsilon)
        return {
            choice_id: epsilon / count + (1.0 - epsilon) * score / score_total
            for choice_id, score in scores.items()
        }

    def reset(self) -> None:
        """Clear counts while preserving the tracked choice portfolio."""
        self._attempts.clear()
        self._admissions.clear()
        self._m_counts.clear()
        self._reward_history.clear()
        self._recent_attempts.clear()
        self._recent_reward_totals.clear()

    def capture_state(self) -> object:
        """Capture tracked choices and all per-choice counts."""
        return (
            tuple(
                sorted(self._tracked, key=lambda item: item.to_json_value().__repr__())
            ),
            tuple((choice, count) for choice, count in self._attempts.items()),
            tuple((choice, count) for choice, count in self._admissions.items()),
            tuple(
                (choice, k, m, count)
                for (choice, k, m), count in self._m_counts.items()
            ),
            tuple(self._reward_history),
        )

    def restore_state(self, payload: object) -> None:
        """Restore a state produced by :meth:`capture_state`."""
        if not isinstance(payload, tuple) or len(payload) != 5:
            raise ValueError('invalid feedback state payload')
        tracked, attempts, admissions, m_counts, reward_history = payload
        self._tracked = set(cast(tuple[ChoiceId, ...], tracked))
        self._attempts = defaultdict(
            int, cast(tuple[tuple[ChoiceId, int], ...], attempts)
        )
        self._admissions = defaultdict(
            int, cast(tuple[tuple[ChoiceId, int], ...], admissions)
        )
        restored_m_counts = cast(
            tuple[tuple[ChoiceId, int, int | None, int], ...], m_counts
        )
        self._m_counts = defaultdict(
            int,
            {(choice_id, k, m): count for choice_id, k, m, count in restored_m_counts},
        )
        self._reward_history = deque(
            cast(tuple[tuple[ChoiceId, float], ...], reward_history)
        )
        if len(self._reward_history) > self._config.feedback_recent_attempt_window:
            raise ValueError('feedback history exceeds its configured window')
        self._recent_attempts = defaultdict(int)
        self._recent_reward_totals = defaultdict(float)
        for choice_id, reward in self._reward_history:
            if not isinstance(choice_id, ChoiceId) or not 0.0 <= reward <= 1.0:
                raise ValueError('invalid recent feedback reward')
            self._recent_attempts[choice_id] += 1
            self._recent_reward_totals[choice_id] += reward

    def statistics(
        self,
    ) -> tuple[tuple[ChoiceId, int, int, tuple[tuple[int, int | None, int], ...]], ...]:
        """Return counts for each tracked choice in stable choice order."""
        result = []
        for choice in sorted(self._tracked, key=lambda item: str(item.to_json_value())):
            counts = tuple(
                (k, m, count)
                for (choice_id, k, m), count in self._m_counts.items()
                if choice_id == choice
            )
            result.append(
                (choice, self._attempts[choice], self._admissions[choice], counts)
            )
        return tuple(result)

    def _prior_reward(self, descriptor: ChoiceDescriptor) -> float:
        if len(descriptor.operator_ids) == 1:
            return float(self._config.standalone_prior_reward)
        if descriptor.shared_group_ids:
            return float(self._config.grouped_composition_prior_reward)
        return float(self._config.ungrouped_composition_prior_reward)


DEFAULT_POLICIES: tuple[type[object], ...] = (
    ParetoArchivePolicy,
    RetainingExplorationPolicy,
    LeastAttemptedBasePolicy,
    DefaultBudgetPolicy,
    DefaultFeedbackPolicy,
)


__all__ = [
    'DEFAULT_POLICIES',
    'DefaultBudgetPolicy',
    'DefaultFeedbackPolicy',
    'LeastAttemptedBasePolicy',
    'ParetoArchivePolicy',
    'RetainingExplorationPolicy',
    'SeededRandomSource',
    'SequentialExecutor',
]
