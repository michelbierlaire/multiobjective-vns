"""Tests for deterministic built-in services and policy defaults.

Created: 2026-10-29T14:26:25+00:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.config import MultiobjectiveVNSConfig
from multiobjective_vns.default_components import (
    DefaultBudgetPolicy,
    DefaultFeedbackPolicy,
    SeededRandomSource,
)
from multiobjective_vns.identifiers import ChoiceId, CompositionId, SolutionId
from multiobjective_vns.records.candidates import CandidateOutcome
from multiobjective_vns.records.policies import (
    BudgetRequest,
    ChoiceDescriptor,
    ChoiceSelectionRequest,
    FeedbackObservation,
    InvocationCounters,
    OperatorChoiceCategory,
)
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    CompositionPosition,
    ProcessingStatus,
    SearchDisposition,
    SubmissionSource,
)


def test_seeded_random_source_restores_its_stream() -> None:
    source = SeededRandomSource(17)
    baseline = source.capture_state()
    first = source.random()
    source.restore_state(baseline)

    assert source.random() == first


def test_default_feedback_policy_returns_a_complete_probability_distribution() -> None:
    policy = DefaultFeedbackPolicy(MultiobjectiveVNSConfig())
    choices = (
        ChoiceDescriptor(
            ChoiceId.for_operator('first'),
            ('first',),
            1,
            2,
            False,
            frozenset(),
            'size units',
        ),
        ChoiceDescriptor(
            ChoiceId.for_operator('second'),
            ('second',),
            1,
            1,
            False,
            frozenset(),
            'size units',
        ),
    )

    probabilities = policy.distribution(
        ChoiceSelectionRequest(OperatorChoiceCategory.STANDALONE, choices)
    )

    assert set(probabilities) == {choice.choice_id for choice in choices}
    assert sum(probabilities.values()) == 1.0
    assert all(probability >= 0.05 for probability in probabilities.values())


def test_performance_matching_uses_group_priors_and_pair_success_feedback() -> None:
    config = MultiobjectiveVNSConfig(
        feedback_selection_mode='performance_matching',
        feedback_epsilon=0.1,
        feedback_prior_attempts=5,
        standalone_prior_reward=0.5,
        grouped_composition_prior_reward=0.9,
        ungrouped_composition_prior_reward=0.1,
    )
    grouped_id = ChoiceId.for_composition(CompositionId('first', 'second'))
    ungrouped_id = ChoiceId.for_composition(CompositionId('second', 'first'))
    choices = (
        ChoiceDescriptor(
            grouped_id,
            ('first', 'second'),
            1,
            1,
            False,
            frozenset(('promising-pair',)),
            'combined size units',
        ),
        ChoiceDescriptor(
            ungrouped_id,
            ('second', 'first'),
            1,
            1,
            False,
            frozenset(),
            'combined size units',
        ),
    )
    request = ChoiceSelectionRequest(OperatorChoiceCategory.COMPOSITION, choices)
    policy = DefaultFeedbackPolicy(config)

    prior_distribution = policy.distribution(request)
    assert prior_distribution[grouped_id] == pytest.approx(0.86)
    assert prior_distribution[ungrouped_id] == pytest.approx(0.14)

    outcome = CandidateOutcome(
        source=SubmissionSource.OPERATOR,
        submitted_solution=(3, 7),
        processing_status=ProcessingStatus.COMPLETED,
        choice_id=grouped_id,
        component_position=CompositionPosition.SECOND,
        base_solution_id=SolutionId(0),
        k=1,
        m=1,
        solution_id=SolutionId(1),
        assessment=ApplicationAssessment.VALID,
        disposition=SearchDisposition.ARCHIVED,
    )
    policy.observe(
        FeedbackObservation(
            grouped_id,
            1,
            1,
            (outcome,),
            (),
            stagnation_attempts=50,
            archive_reward=0.99,
        )
    )
    updated_distribution = policy.distribution(request)

    assert updated_distribution[grouped_id] > prior_distribution[grouped_id]
    payload = policy.capture_state()
    restored_policy = DefaultFeedbackPolicy(config)
    restored_policy.restore_state(payload)
    assert restored_policy.distribution(request) == updated_distribution


def test_feedback_uses_recent_rewards_and_reset_clears_the_window() -> None:
    config = MultiobjectiveVNSConfig(
        feedback_prior_attempts=1,
        feedback_recent_attempt_window=2,
        standalone_prior_reward=0.5,
    )
    first_id = ChoiceId.for_operator('first')
    second_id = ChoiceId.for_operator('second')
    choices = (
        ChoiceDescriptor(first_id, ('first',), 1, 1, False, frozenset(), 'units'),
        ChoiceDescriptor(second_id, ('second',), 1, 1, False, frozenset(), 'units'),
    )
    request = ChoiceSelectionRequest(OperatorChoiceCategory.STANDALONE, choices)
    policy = DefaultFeedbackPolicy(config)

    def observe(choice_id: ChoiceId, solution_id: int, reward: float) -> None:
        outcome = CandidateOutcome(
            source=SubmissionSource.OPERATOR,
            submitted_solution=(solution_id,),
            processing_status=ProcessingStatus.COMPLETED,
            choice_id=choice_id,
            base_solution_id=SolutionId(0),
            k=1,
            m=1,
            solution_id=SolutionId(solution_id),
            assessment=ApplicationAssessment.VALID,
            disposition=SearchDisposition.ARCHIVED,
        )
        policy.observe(
            FeedbackObservation(
                choice_id,
                1,
                1,
                (outcome,),
                (),
                stagnation_attempts=1,
                archive_reward=reward,
            )
        )

    observe(first_id, 1, 0.9)
    observe(first_id, 2, 0.9)
    first_favored = policy.distribution(request)
    assert first_favored[first_id] > first_favored[second_id]

    observe(second_id, 3, 0.9)
    observe(second_id, 4, 0.9)
    second_favored = policy.distribution(request)
    assert second_favored[second_id] > second_favored[first_id]

    restored_policy = DefaultFeedbackPolicy(config)
    restored_policy.restore_state(policy.capture_state())
    assert restored_policy.distribution(request) == second_favored

    policy.reset()
    reset_distribution = policy.distribution(request)
    assert reset_distribution[first_id] == pytest.approx(0.5)
    assert reset_distribution[second_id] == pytest.approx(0.5)


def test_uniform_feedback_ignores_operator_group_hints() -> None:
    policy = DefaultFeedbackPolicy(
        MultiobjectiveVNSConfig(feedback_selection_mode='uniform')
    )
    choices = (
        ChoiceDescriptor(
            ChoiceId.for_operator('standalone'),
            ('standalone',),
            1,
            1,
            False,
            frozenset(),
            'size units',
        ),
        ChoiceDescriptor(
            ChoiceId.for_composition(CompositionId('first', 'second')),
            ('first', 'second'),
            1,
            1,
            False,
            frozenset(('promising-pair',)),
            'combined size units',
        ),
    )

    standalone_choices = (choices[0],)
    composition_choices = (choices[1],)
    standalone_probabilities = policy.distribution(
        ChoiceSelectionRequest(OperatorChoiceCategory.STANDALONE, standalone_choices)
    )
    composition_probabilities = policy.distribution(
        ChoiceSelectionRequest(OperatorChoiceCategory.COMPOSITION, composition_choices)
    )

    assert standalone_probabilities == {choices[0].choice_id: 1.0}
    assert composition_probabilities == {choices[1].choice_id: 1.0}


def test_default_budget_policy_allows_work_until_engine_ceiling() -> None:
    decision = DefaultBudgetPolicy().decide(
        BudgetRequest(
            counters=InvocationCounters(),
            limits=MultiobjectiveVNSConfig().budget_limits,
            elapsed_seconds=0.0,
        )
    )

    assert decision.allowed is True
