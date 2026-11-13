"""Tests for the shared candidate, evaluation, and diagnostic records.

Created: 2026-10-29T13:18:36+00:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.identifiers import ChoiceId, CompositionId, SolutionId
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateOutcome,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
    ObjectiveBoundFinding,
)
from multiobjective_vns.records.failures import ComponentFailure, FailureKind
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    CompositionPosition,
    ProcessingStatus,
    SearchDisposition,
    SubmissionSource,
)


def test_operator_result_requires_candidate_proposal_records() -> None:
    proposal = CandidateProposal(Candidate(solution=(1, 0)), 2)
    assert OperatorResult(candidates=(proposal,)).candidates == (proposal,)
    assert OperatorResult(candidates=()).candidates == ()

    with pytest.raises(TypeError):
        OperatorResult(candidates=('not a proposal',))  # type: ignore[arg-type]


def test_candidate_can_wrap_none_as_an_application_solution() -> None:
    assert Candidate(solution=None).solution is None


def test_application_evaluation_validates_finding_nullability() -> None:
    analysis = ('resource loads', (1, 2))
    evaluation = ApplicationEvaluation(True, True, (1, 2), analysis=analysis)
    assert evaluation.feasible is True
    assert evaluation.analysis is analysis
    assert ApplicationEvaluation(False, None, None).feasible is None

    with pytest.raises(ValueError):
        ApplicationEvaluation(True, None, (1, 2))
    with pytest.raises(TypeError):
        ApplicationEvaluation(1, True, (1, 2))  # type: ignore[arg-type]


def test_candidate_context_carries_an_optional_base_evaluation() -> None:
    evaluation = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        ApplicationEvaluation(True, True, (1, 2)),
        ApplicationAssessment.VALID,
    )
    context = CandidateContext(
        base=('solution',),
        k=1,
        ensure_feasible=True,
        operator_state=None,
        random_source=object(),  # type: ignore[arg-type]
        base_evaluation=evaluation,
    )

    assert context.base_evaluation is evaluation
    intermediate_context = CandidateContext(
        base=('intermediate',),
        k=1,
        ensure_feasible=True,
        operator_state=None,
        random_source=object(),  # type: ignore[arg-type]
    )
    assert intermediate_context.base_evaluation is None


def test_shared_records_are_immutable_and_failure_is_structured() -> None:
    failure = ComponentFailure(
        kind=FailureKind.CALLBACK_EXCEPTION,
        source='operator-x',
        phase='operator_call',
        reason='operator raised',
    )
    assert failure.kind is FailureKind.CALLBACK_EXCEPTION
    with pytest.raises((AttributeError, TypeError)):
        failure.reason = 'changed'  # type: ignore[misc]
    assert ProcessingStatus.COMPLETED.value == 'completed'
    assert ApplicationAssessment.INVALID.value == 'invalid'


def test_failed_evaluation_record_requires_a_diagnostic() -> None:
    failure = ComponentFailure(
        kind=FailureKind.CALLBACK_EXCEPTION,
        source='evaluator',
        phase='application_evaluation',
        reason='callback raised',
    )
    record = EvaluationRecord(
        processing_status=ProcessingStatus.FAILED,
        application_evaluation=None,
        assessment=None,
        failures=(failure,),
    )
    assert record.failures == (failure,)

    with pytest.raises(ValueError):
        EvaluationRecord(
            processing_status=ProcessingStatus.FAILED,
            application_evaluation=None,
            assessment=None,
        )


def test_candidate_outcome_keeps_failure_separate_from_assessment() -> None:
    failure = ComponentFailure(
        kind=FailureKind.CALLBACK_EXCEPTION,
        source='identity',
        phase='identity_resolution',
        reason='comparison raised',
    )
    outcome = CandidateOutcome(
        source=SubmissionSource.INITIAL,
        submitted_solution=('x',),
        processing_status=ProcessingStatus.FAILED,
        failures=(failure,),
    )
    assert outcome.solution_id is None
    assert outcome.assessment is None

    with pytest.raises(ValueError):
        CandidateOutcome(
            source=SubmissionSource.INITIAL,
            submitted_solution=('x',),
            processing_status=ProcessingStatus.FAILED,
            failures=(failure,),
            assessment=ApplicationAssessment.INVALID,
        )


def test_candidate_outcomes_distinguish_initial_and_operator_fields() -> None:
    initial = CandidateOutcome(
        source=SubmissionSource.INITIAL,
        submitted_solution=(1, 0),
        processing_status=ProcessingStatus.COMPLETED,
        solution_id=SolutionId(1),
        assessment=ApplicationAssessment.VALID,
        disposition=SearchDisposition.ARCHIVED,
    )
    operator = CandidateOutcome(
        source=SubmissionSource.OPERATOR,
        submitted_solution=(0, 1),
        processing_status=ProcessingStatus.COMPLETED,
        choice_id=ChoiceId.for_operator('swap'),
        base_solution_id=SolutionId(1),
        k=1,
        m=1,
        candidate_index=1,
        solution_id=SolutionId(2),
        assessment=ApplicationAssessment.VALID,
        disposition=SearchDisposition.ARCHIVED,
    )

    assert initial.choice_id is None
    assert operator.component_position is None

    registered_composition = CandidateOutcome(
        source=SubmissionSource.OPERATOR,
        submitted_solution=(2, 3),
        processing_status=ProcessingStatus.COMPLETED,
        choice_id=ChoiceId.for_operator('repair-after-move'),
        component_position=CompositionPosition.SECOND,
        base_solution_id=SolutionId(1),
        k=1,
        m=1,
        candidate_index=1,
        solution_id=SolutionId(3),
        assessment=ApplicationAssessment.VALID,
        disposition=SearchDisposition.ARCHIVED,
    )
    assert registered_composition.component_position is CompositionPosition.SECOND


def test_objective_bound_finding_is_a_stable_record() -> None:
    finding = ObjectiveBoundFinding('cost', 12, 10)
    assert finding.objective_id == 'cost'
    assert finding.objective_value == 12
    assert finding.upper_bound == 10

    with pytest.raises(ValueError, match='candidate_index'):
        CandidateOutcome(
            source=SubmissionSource.OPERATOR,
            submitted_solution=(0, 1),
            processing_status=ProcessingStatus.COMPLETED,
            choice_id=ChoiceId.for_operator('swap'),
            base_solution_id=SolutionId(1),
            k=1,
            m=1,
            solution_id=SolutionId(4),
            assessment=ApplicationAssessment.VALID,
            disposition=SearchDisposition.ARCHIVED,
        )

    with pytest.raises(ValueError):
        CandidateOutcome(
            source=SubmissionSource.OPERATOR,
            submitted_solution=(0, 1),
            processing_status=ProcessingStatus.COMPLETED,
            choice_id=ChoiceId.for_composition(CompositionId('swap', 'repair')),
            component_position=CompositionPosition.FIRST,
            base_solution_id=SolutionId(1),
            k=1,
            m=1,
            candidate_index=1,
            solution_id=SolutionId(2),
            assessment=ApplicationAssessment.VALID,
            disposition=SearchDisposition.ARCHIVED,
        )
