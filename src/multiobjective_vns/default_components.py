"""Package-provided random, execution, and policy components.

Created: 2026-10-29T14:26:25+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Mapping
from math import exp
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
    ChoiceSelectionRequest,
    FeedbackObservation,
    OperatorChoiceCategory,
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
    configuration_id = 'multiobjective-vns:archive-policy-v2'

    def decide(self, request: ArchiveRequest) -> ArchiveDecision:
        """Keep one representative for each nondominated objective vector."""
        comparisons = tuple(
            (
                entry.solution_id,
                self._compare_vectors(
                    entry.objective_values,
                    request.candidate.objective_values,
                    request.objective_declaration,
                ),
            )
            for entry in request.current_archive
        )
        if any(
            self._dominates_comparisons(vector_comparisons)
            or all(
                comparison is ObjectiveComparison.EQUAL
                for comparison in vector_comparisons
            )
            for _, vector_comparisons in comparisons
        ):
            return ArchiveDecision(False, ())
        remove_ids = tuple(
            solution_id
            for solution_id, vector_comparisons in comparisons
            if self._dominates_comparisons(
                tuple(self._reverse(comparison) for comparison in vector_comparisons)
            )
        )
        return ArchiveDecision(True, remove_ids)

    @staticmethod
    def _compare_vectors(
        left: tuple[object, ...], right: tuple[object, ...], declaration: object
    ) -> tuple[ObjectiveComparison, ...]:
        objectives = declaration.objectives  # type: ignore[attr-defined]
        return tuple(
            objective.compare(left_value, right_value)
            for objective, left_value, right_value in zip(
                objectives, left, right, strict=True
            )
        )

    @staticmethod
    def _dominates_comparisons(
        comparisons: tuple[ObjectiveComparison, ...],
    ) -> bool:
        return all(
            comparison in (ObjectiveComparison.LESS, ObjectiveComparison.EQUAL)
            for comparison in comparisons
        ) and any(comparison is ObjectiveComparison.LESS for comparison in comparisons)

    @staticmethod
    def _reverse(comparison: ObjectiveComparison) -> ObjectiveComparison:
        if comparison is ObjectiveComparison.LESS:
            return ObjectiveComparison.GREATER
        if comparison is ObjectiveComparison.GREATER:
            return ObjectiveComparison.LESS
        return ObjectiveComparison.EQUAL


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
    """Track admission scores and sample with a softmax probability floor."""

    component_id = 'multiobjective-vns:feedback-policy'
    configuration_id = 'multiobjective-vns:feedback-policy-v4'

    def __init__(self, config: MultiobjectiveVNSConfig) -> None:
        self._config = config
        self._attempts: defaultdict[ChoiceId, int] = defaultdict(int)
        self._admitting_attempts: defaultdict[ChoiceId, int] = defaultdict(int)
        self._admissions: defaultdict[ChoiceId, int] = defaultdict(int)
        self._candidate_counts: defaultdict[ChoiceId, int] = defaultdict(int)
        self._m_counts: defaultdict[tuple[ChoiceId, int, int | None], int] = (
            defaultdict(int)
        )
        self._tracked: set[ChoiceId] = set()
        self._scores: defaultdict[ChoiceId, int] = defaultdict(int)

    def observe(self, observation: FeedbackObservation) -> None:
        """Update one choice score and its committed invocation statistics."""
        self._tracked.add(observation.choice_id)
        self._attempts[observation.choice_id] += 1
        if observation.archive_admission_count:
            self._admitting_attempts[observation.choice_id] += 1
            self._admissions[observation.choice_id] += (
                observation.archive_admission_count
            )
            self._scores[observation.choice_id] += 1
        else:
            self._scores[observation.choice_id] = max(
                0, self._scores[observation.choice_id] - 1
            )
        if observation.modification_counts is None:
            self._m_counts[(observation.choice_id, observation.k, None)] += 1
        elif not observation.modification_counts:
            self._m_counts[(observation.choice_id, observation.k, 0)] += 1
        else:
            self._candidate_counts[observation.choice_id] += len(
                observation.modification_counts
            )
            for count in observation.modification_counts:
                self._m_counts[(observation.choice_id, observation.k, count)] += 1

    def distribution(self, request: ChoiceSelectionRequest) -> Mapping[ChoiceId, float]:
        """Return a uniform or softmax distribution with the configured floor."""
        count = len(request.choices)
        if self._config.feedback_selection_mode == 'uniform':
            return {choice.choice_id: 1.0 / count for choice in request.choices}

        epsilon = float(self._config.feedback_epsilon)
        if count * epsilon >= 1.0:
            raise ValueError(
                'feedback_epsilon must be strictly below 1 / n '
                f'for performance matching (n={count})'
            )

        scores: dict[ChoiceId, int] = {}
        for choice in request.choices:
            choice_id = choice.choice_id
            group_prior = int(
                request.category is OperatorChoiceCategory.COMPOSITION
                and bool(choice.shared_group_ids)
            )
            scores[choice_id] = self._scores[choice_id] + group_prior

        maximum_score = max(scores.values())
        mu = float(self._config.feedback_mu)
        weights: dict[ChoiceId, float] = {}
        for choice_id, score in scores.items():
            score_gap = maximum_score - score
            if score_gap == 0 or mu == 0.0:
                weights[choice_id] = 1.0
                continue
            try:
                exponent = -mu * score_gap
            except OverflowError:
                weights[choice_id] = 0.0
                continue
            weights[choice_id] = 0.0 if exponent < -745.0 else exp(exponent)

        weight_total = sum(weights.values())
        return {
            choice_id: epsilon + (1.0 - count * epsilon) * weight / weight_total
            for choice_id, weight in weights.items()
        }

    def reset(self) -> None:
        """Clear counts while preserving the tracked choice portfolio."""
        self._attempts.clear()
        self._admitting_attempts.clear()
        self._admissions.clear()
        self._candidate_counts.clear()
        self._m_counts.clear()
        self._scores = defaultdict(int, {choice: 0 for choice in self._tracked})

    def capture_state(self) -> object:
        """Capture scores, counters, and modification-count frequencies."""
        return (
            tuple(sorted(self._tracked, key=lambda item: str(item.to_json_value()))),
            tuple(sorted(self._scores.items(), key=lambda item: str(item[0]))),
            tuple(sorted(self._attempts.items(), key=lambda item: str(item[0]))),
            tuple(
                sorted(self._admitting_attempts.items(), key=lambda item: str(item[0]))
            ),
            tuple(sorted(self._admissions.items(), key=lambda item: str(item[0]))),
            tuple(
                sorted(self._candidate_counts.items(), key=lambda item: str(item[0]))
            ),
            tuple(
                (choice, k, m, count)
                for (choice, k, m), count in sorted(
                    self._m_counts.items(),
                    key=lambda item: (str(item[0][0]), item[0][1], str(item[0][2])),
                )
            ),
        )

    def restore_state(self, payload: object) -> None:
        """Restore a state produced by :meth:`capture_state`."""
        if not isinstance(payload, tuple) or len(payload) != 7:
            raise ValueError('invalid feedback state payload')
        tracked, scores, attempts, admitting, admissions, candidate_counts, m_counts = (
            payload
        )
        self._tracked = set(cast(tuple[ChoiceId, ...], tracked))
        self._scores = defaultdict(int, cast(tuple[tuple[ChoiceId, int], ...], scores))
        self._attempts = defaultdict(
            int, cast(tuple[tuple[ChoiceId, int], ...], attempts)
        )
        self._admitting_attempts = defaultdict(
            int, cast(tuple[tuple[ChoiceId, int], ...], admitting)
        )
        self._admissions = defaultdict(
            int, cast(tuple[tuple[ChoiceId, int], ...], admissions)
        )
        self._candidate_counts = defaultdict(
            int, cast(tuple[tuple[ChoiceId, int], ...], candidate_counts)
        )
        restored_m_counts = cast(
            tuple[tuple[ChoiceId, int, int | None, int], ...], m_counts
        )
        self._m_counts = defaultdict(
            int,
            {(choice_id, k, m): count for choice_id, k, m, count in restored_m_counts},
        )
        if not self._tracked.issuperset(self._scores):
            raise ValueError('feedback scores contain an untracked choice')
        if any(score < 0 for score in self._scores.values()):
            raise ValueError('feedback scores must be nonnegative')

    def statistics(
        self,
    ) -> tuple[
        tuple[
            ChoiceId,
            int,
            int,
            int,
            int,
            tuple[tuple[int, int | None, int], ...],
            int,
        ],
        ...,
    ]:
        """Return counts for each tracked choice in stable choice order."""
        result = []
        for choice in sorted(self._tracked, key=lambda item: str(item.to_json_value())):
            counts = tuple(
                (k, m, count)
                for (choice_id, k, m), count in self._m_counts.items()
                if choice_id == choice
            )
            result.append(
                (
                    choice,
                    self._attempts[choice],
                    self._admitting_attempts[choice],
                    self._admissions[choice],
                    self._candidate_counts[choice],
                    counts,
                    self._scores[choice],
                )
            )
        return tuple(result)


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
