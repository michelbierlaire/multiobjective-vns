"""Tests for deterministic built-in services and policy defaults.

Created: 2026-10-29T14:26:25+00:00
Author: Michel Bierlaire, EPFL
"""

import math

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
    config = MultiobjectiveVNSConfig(max_candidates_per_attempt=1)
    policy = DefaultFeedbackPolicy(config)
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
    assert sum(probabilities.values()) == pytest.approx(1.0)
    assert all(
        probability >= config.feedback_epsilon for probability in probabilities.values()
    )
    assert probabilities[choices[0].choice_id] == pytest.approx(0.5)
    assert probabilities[choices[1].choice_id] == pytest.approx(0.5)


def test_performance_matching_uses_group_prior_and_unit_score_updates() -> None:
    config = MultiobjectiveVNSConfig(
        max_candidates_per_attempt=1,
        feedback_selection_mode='performance_matching',
        feedback_epsilon=0.1,
        feedback_mu=math.log(2.0),
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
    assert prior_distribution[grouped_id] == pytest.approx(0.1 + 0.8 * (2 / 3))
    assert prior_distribution[ungrouped_id] == pytest.approx(0.1 + 0.8 * (1 / 3))

    outcome = CandidateOutcome(
        source=SubmissionSource.OPERATOR,
        submitted_solution=(3, 7),
        processing_status=ProcessingStatus.COMPLETED,
        choice_id=grouped_id,
        component_position=CompositionPosition.SECOND,
        base_solution_id=SolutionId(0),
        k=1,
        m=1,
        candidate_index=1,
        solution_id=SolutionId(1),
        assessment=ApplicationAssessment.VALID,
        disposition=SearchDisposition.ARCHIVED,
    )
    policy.observe(FeedbackObservation(grouped_id, 1, (1,), (outcome,), (), 1))
    grouped_success_distribution = policy.distribution(request)
    assert grouped_success_distribution[grouped_id] == pytest.approx(0.74)
    assert grouped_success_distribution[ungrouped_id] == pytest.approx(0.26)

    ungrouped_outcome = CandidateOutcome(
        source=SubmissionSource.OPERATOR,
        submitted_solution=(4, 6),
        processing_status=ProcessingStatus.COMPLETED,
        choice_id=ungrouped_id,
        component_position=CompositionPosition.SECOND,
        base_solution_id=SolutionId(0),
        k=1,
        m=1,
        candidate_index=1,
        solution_id=SolutionId(2),
        assessment=ApplicationAssessment.VALID,
        disposition=SearchDisposition.ARCHIVED,
    )
    for _ in range(3):
        policy.observe(
            FeedbackObservation(ungrouped_id, 1, (1,), (ungrouped_outcome,), (), 1)
        )
    updated_distribution = policy.distribution(request)

    assert updated_distribution[grouped_id] == pytest.approx(0.1 + 0.8 / 3)
    assert updated_distribution[ungrouped_id] == pytest.approx(0.1 + 0.8 * (2 / 3))
    assert updated_distribution[ungrouped_id] > updated_distribution[grouped_id]
    payload = policy.capture_state()
    restored_policy = DefaultFeedbackPolicy(config)
    restored_policy.restore_state(payload)
    assert restored_policy.distribution(request) == updated_distribution


def test_feedback_floor_is_per_choice_and_epsilon_must_be_below_one_over_n() -> None:
    config = MultiobjectiveVNSConfig(
        max_candidates_per_attempt=1,
        feedback_selection_mode='performance_matching',
        feedback_epsilon=0.1,
        feedback_mu=100.0,
    )
    favored_id = ChoiceId.for_operator('favored')
    other_id = ChoiceId.for_operator('other')
    choices = (
        ChoiceDescriptor(favored_id, ('favored',), 1, 1, False, frozenset(), 'units'),
        ChoiceDescriptor(other_id, ('other',), 1, 1, False, frozenset(), 'units'),
    )
    request = ChoiceSelectionRequest(OperatorChoiceCategory.STANDALONE, choices)
    policy = DefaultFeedbackPolicy(config)
    admitted = CandidateOutcome(
        source=SubmissionSource.OPERATOR,
        submitted_solution=(1,),
        processing_status=ProcessingStatus.COMPLETED,
        choice_id=favored_id,
        base_solution_id=SolutionId(0),
        k=1,
        m=1,
        candidate_index=1,
        solution_id=SolutionId(1),
        assessment=ApplicationAssessment.VALID,
        disposition=SearchDisposition.ARCHIVED,
    )
    policy.observe(FeedbackObservation(favored_id, 1, (1,), (admitted,), (), 1))

    probabilities = policy.distribution(request)

    assert probabilities[favored_id] == pytest.approx(0.9)
    assert probabilities[other_id] == pytest.approx(0.1)
    assert min(probabilities.values()) == pytest.approx(config.feedback_epsilon)

    invalid_floor_policy = DefaultFeedbackPolicy(
        MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            feedback_epsilon=0.5,
        )
    )
    with pytest.raises(ValueError, match='strictly below 1 / n'):
        invalid_floor_policy.distribution(request)


def test_zero_feedback_intensity_gives_uniform_probability_after_score_changes() -> (
    None
):
    first_id = ChoiceId.for_operator('first')
    second_id = ChoiceId.for_operator('second')
    choices = (
        ChoiceDescriptor(first_id, ('first',), 1, 1, False, frozenset(), 'units'),
        ChoiceDescriptor(second_id, ('second',), 1, 1, False, frozenset(), 'units'),
    )
    request = ChoiceSelectionRequest(OperatorChoiceCategory.STANDALONE, choices)
    policy = DefaultFeedbackPolicy(
        MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            feedback_epsilon=0.1,
            feedback_mu=0.0,
        )
    )
    admitted = CandidateOutcome(
        source=SubmissionSource.OPERATOR,
        submitted_solution=(1,),
        processing_status=ProcessingStatus.COMPLETED,
        choice_id=first_id,
        base_solution_id=SolutionId(0),
        k=1,
        m=1,
        candidate_index=1,
        solution_id=SolutionId(1),
        assessment=ApplicationAssessment.VALID,
        disposition=SearchDisposition.ARCHIVED,
    )
    policy.observe(FeedbackObservation(first_id, 1, (1,), (admitted,), (), 1))

    probabilities = policy.distribution(request)

    assert probabilities == {
        first_id: pytest.approx(0.5),
        second_id: pytest.approx(0.5),
    }


def test_feedback_decrements_without_admission_and_reset_clears_scores() -> None:
    config = MultiobjectiveVNSConfig(
        max_candidates_per_attempt=1,
        feedback_epsilon=0.1,
    )
    first_id = ChoiceId.for_operator('first')
    second_id = ChoiceId.for_operator('second')
    choices = (
        ChoiceDescriptor(first_id, ('first',), 1, 1, False, frozenset(), 'units'),
        ChoiceDescriptor(second_id, ('second',), 1, 1, False, frozenset(), 'units'),
    )
    request = ChoiceSelectionRequest(OperatorChoiceCategory.STANDALONE, choices)
    policy = DefaultFeedbackPolicy(config)

    def observe(choice_id: ChoiceId, solution_id: int, admitted: bool) -> None:
        outcome = CandidateOutcome(
            source=SubmissionSource.OPERATOR,
            submitted_solution=(solution_id,),
            processing_status=ProcessingStatus.COMPLETED,
            choice_id=choice_id,
            base_solution_id=SolutionId(0),
            k=1,
            m=1,
            candidate_index=1,
            solution_id=SolutionId(solution_id),
            assessment=ApplicationAssessment.VALID,
            disposition=(
                SearchDisposition.ARCHIVED if admitted else SearchDisposition.DOMINATED
            ),
        )
        policy.observe(
            FeedbackObservation(
                choice_id,
                1,
                (1,),
                (outcome,),
                (),
                int(admitted),
            )
        )

    observe(first_id, 1, True)
    first_favored = policy.distribution(request)
    assert first_favored[first_id] > first_favored[second_id]

    observe(first_id, 2, False)
    balanced = policy.distribution(request)
    assert balanced[first_id] == pytest.approx(0.5)
    assert balanced[second_id] == pytest.approx(0.5)

    observe(second_id, 3, True)
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
        MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            feedback_selection_mode='uniform',
        )
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
            limits=MultiobjectiveVNSConfig(max_candidates_per_attempt=1).budget_limits,
            elapsed_seconds=0.0,
        )
    )

    assert decision.allowed is True
