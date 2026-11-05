"""Tests for shared policy requests, decisions, and observations.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.config import BudgetLimits
from multiobjective_vns.identifiers import ChoiceId, CompositionId, SolutionId
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)
from multiobjective_vns.records.candidates import CandidateOutcome
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
    SolutionRecord,
)
from multiobjective_vns.records.policies import (
    ActivePoolDecision,
    ActivePoolRequest,
    ArchiveDecision,
    ArchiveEntry,
    ArchiveRequest,
    BaseSelectionDecision,
    BaseSelectionRequest,
    BudgetDecision,
    BudgetRequest,
    ChoiceDescriptor,
    ChoiceSelectionRequest,
    FeedbackObservation,
    InvocationCounters,
    OperatorChoiceCategory,
    PoolAction,
)
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    ProcessingStatus,
    SearchDisposition,
    SubmissionSource,
)


class NumberObjective:
    def __init__(self, objective_id: str) -> None:
        self.objective_id = objective_id
        self.component_id = f'{objective_id}-component'
        self.configuration_id = f'{objective_id}-v1'

    def compare(self, left: object, right: object) -> ObjectiveComparison:
        return ObjectiveComparison.EQUAL if left == right else ObjectiveComparison.LESS

    def format(self, value: object) -> str:
        return str(value)

    def __str__(self) -> str:
        return self.objective_id


class DummyRandomSource:
    component_id = 'random'
    configuration_id = 'random-v1'

    def random(self) -> float:
        return 0.5

    def capture_state(self) -> object:
        return 0.5

    def restore_state(self, payload: object) -> None:
        assert payload == 0.5


def make_record(sequence: int) -> SolutionRecord:
    evaluation = ApplicationEvaluation(True, True, (sequence, sequence))
    evaluation_record = EvaluationRecord(
        processing_status=ProcessingStatus.COMPLETED,
        application_evaluation=evaluation,
        assessment=ApplicationAssessment.VALID,
    )
    return SolutionRecord(SolutionId(sequence), (sequence,), evaluation_record)


def test_archive_records_keep_candidate_and_removal_ids_explicit() -> None:
    candidate = ArchiveEntry(SolutionId(2), (1, 3))
    current = (ArchiveEntry(SolutionId(1), (2, 4)),)
    request = ArchiveRequest(
        candidate,
        current,
        ObjectiveDeclaration((NumberObjective('first'), NumberObjective('second'))),
    )
    decision = ArchiveDecision(admit=True, remove_ids=(SolutionId(1),))

    assert request.candidate == candidate
    assert decision.remove_ids == (SolutionId(1),)


def test_active_pool_request_and_decision_hold_solution_ids() -> None:
    one = make_record(1)
    two = make_record(2)
    request = ActivePoolRequest(
        registry_records=(one, two),
        eligible_ids=frozenset({one.solution_id, two.solution_id}),
        active_ids=frozenset({one.solution_id}),
        archive_ids=frozenset({one.solution_id}),
        action=PoolAction.RECONCILE,
        changed_solution_id=two.solution_id,
    )
    decision = ActivePoolDecision(frozenset({one.solution_id, two.solution_id}))

    assert request.archive_ids.issubset(request.eligible_ids)
    assert decision.active_ids == request.eligible_ids


def test_choice_and_feedback_records_are_typed_by_choice_and_size() -> None:
    choice_id = ChoiceId.for_composition(CompositionId('destroy', 'repair'))
    descriptor = ChoiceDescriptor(
        choice_id=choice_id,
        operator_ids=('destroy', 'repair'),
        current_k=1,
        max_neighborhood_size=6,
        feasibility_restoration=True,
        shared_group_ids=frozenset({'ruin-recreate'}),
        modification_count_definition='minimum component modification count',
    )
    request = ChoiceSelectionRequest(OperatorChoiceCategory.COMPOSITION, (descriptor,))
    observation = FeedbackObservation(
        choice_id=choice_id,
        k=2,
        m=0,
        outcomes=(),
        failures=(),
        stagnation_attempts=3,
        archive_reward=0.0,
    )

    assert request.choices == (descriptor,)
    assert request.category is OperatorChoiceCategory.COMPOSITION
    assert observation.m == 0
    with pytest.raises(ValueError, match='current_k'):
        ChoiceDescriptor(
            choice_id=choice_id,
            operator_ids=('destroy', 'repair'),
            current_k=7,
            max_neighborhood_size=6,
            feasibility_restoration=True,
            shared_group_ids=frozenset({'ruin-recreate'}),
            modification_count_definition='minimum component modification count',
        )
    with pytest.raises(ValueError):
        FeedbackObservation(
            choice_id,
            k=2,
            m=3,
            outcomes=(),
            failures=(),
            stagnation_attempts=0,
            archive_reward=0.0,
        )
    with pytest.raises(ValueError):
        FeedbackObservation(
            choice_id,
            k=1,
            m=0,
            outcomes=(),
            failures=(),
            stagnation_attempts=0,
            archive_reward=1.1,
        )
    with pytest.raises((TypeError, ValueError)):
        FeedbackObservation(
            choice_id,
            k=1,
            m=0,
            outcomes=(),
            failures=(),
            stagnation_attempts=True,
            archive_reward=0.0,
        )
    with pytest.raises(ValueError):
        ChoiceDescriptor(
            choice_id=choice_id,
            operator_ids=('repair', 'destroy'),
            current_k=1,
            max_neighborhood_size=6,
            feasibility_restoration=True,
            shared_group_ids=frozenset(),
            modification_count_definition='minimum component count',
        )


def test_choice_selection_request_rejects_choices_outside_its_category() -> None:
    descriptor = ChoiceDescriptor(
        choice_id=ChoiceId.for_operator('swap'),
        operator_ids=('swap',),
        current_k=1,
        max_neighborhood_size=2,
        feasibility_restoration=False,
        shared_group_ids=frozenset(),
        modification_count_definition='changed item decisions',
    )

    with pytest.raises(ValueError, match='category'):
        ChoiceSelectionRequest(OperatorChoiceCategory.COMPOSITION, (descriptor,))


def test_budget_records_validate_counters_and_denial_shape() -> None:
    counters = InvocationCounters(2, 3)
    request = BudgetRequest(
        counters=counters,
        limits=BudgetLimits(),
        elapsed_seconds=2,
    )
    assert request.elapsed_seconds == 2.0
    assert BudgetDecision(True).allowed

    with pytest.raises((TypeError, ValueError)):
        BudgetDecision('yes')  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        InvocationCounters(-1, 0)
    with pytest.raises(ValueError):
        BudgetRequest(
            counters,
            BudgetLimits(),
            elapsed_seconds=-1,
        )


def test_base_selection_request_copies_attempt_counts_read_only() -> None:
    record = make_record(1)
    request = BaseSelectionRequest(
        eligible_records=(record,),
        attempt_counts={record.solution_id: 4},
        previous_base_id=None,
        previous_attempt_admitted=None,
        random_source=DummyRandomSource(),
    )
    decision = BaseSelectionDecision(record.solution_id)

    assert request.attempt_counts[record.solution_id] == 4
    assert decision.solution_id == record.solution_id
    with pytest.raises(TypeError):
        request.attempt_counts[record.solution_id] = 5  # type: ignore[index]


def test_base_selection_request_contains_only_valid_feasible_records() -> None:
    evaluation_record = EvaluationRecord(
        processing_status=ProcessingStatus.COMPLETED,
        application_evaluation=ApplicationEvaluation(True, False, (1, 1)),
        assessment=ApplicationAssessment.INFEASIBLE,
    )
    record = SolutionRecord(SolutionId(9), (9,), evaluation_record)

    with pytest.raises(ValueError):
        BaseSelectionRequest(
            eligible_records=(record,),
            attempt_counts={record.solution_id: 0},
            previous_base_id=None,
            previous_attempt_admitted=None,
            random_source=DummyRandomSource(),
        )


def test_feedback_observation_has_at_most_one_submitted_candidate() -> None:
    choice_id = ChoiceId.for_operator('swap')
    outcome = CandidateOutcome(
        source=SubmissionSource.OPERATOR,
        submitted_solution=(1,),
        processing_status=ProcessingStatus.COMPLETED,
        choice_id=choice_id,
        base_solution_id=SolutionId(0),
        k=1,
        m=1,
        solution_id=SolutionId(1),
        assessment=ApplicationAssessment.VALID,
        disposition=SearchDisposition.ARCHIVED,
    )
    with pytest.raises(ValueError):
        FeedbackObservation(
            choice_id,
            k=1,
            m=1,
            outcomes=(outcome, outcome),
            failures=(),
            stagnation_attempts=0,
            archive_reward=0.0,
        )

    with pytest.raises(ValueError):
        FeedbackObservation(
            ChoiceId.for_operator('other'),
            k=1,
            m=1,
            outcomes=(outcome,),
            failures=(),
            stagnation_attempts=0,
            archive_reward=0.0,
        )
