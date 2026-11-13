"""Tests for identity-first evaluation and candidate registry processing.

Created: 2026-10-29T13:52:20+00:00
Author: Michel Bierlaire, EPFL
"""

from types import SimpleNamespace
from typing import cast

from multiobjective_vns.callbacks import ApplicationEvaluator
from multiobjective_vns.candidate_processing import (
    CandidateProcessingResult,
    CandidateProcessor,
)
from multiobjective_vns.evaluation import EvaluationProcessor
from multiobjective_vns.identifiers import ChoiceId, SolutionId
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)
from multiobjective_vns.records.candidates import Candidate
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.records.failures import FailureKind
from multiobjective_vns.records.results import ExecutionResult
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    ProcessingStatus,
    SearchDisposition,
    SubmissionSource,
)
from multiobjective_vns.registry import SolutionRegistry


class Objective:
    def __init__(self, objective_id: str, *, fail: bool = False) -> None:
        self.objective_id = objective_id
        self.component_id = f'{objective_id}-component'
        self.configuration_id = f'{objective_id}-v1'
        self.fail = fail

    def compare(self, left: object, right: object) -> ObjectiveComparison:
        if self.fail:
            raise RuntimeError('objective comparison failed')
        return (
            ObjectiveComparison.LESS
            if left < right  # type: ignore[operator]
            else ObjectiveComparison.GREATER
            if left > right  # type: ignore[operator]
            else ObjectiveComparison.EQUAL
        )

    def format(self, value: object) -> str:
        return str(value)

    def __str__(self) -> str:
        return self.objective_id


class Identity:
    component_id = 'identity'
    configuration_id = 'identity-v1'

    def equivalent(self, left: object, right: object) -> bool:
        return left == right


class RaisingIdentity(Identity):
    def equivalent(self, left: object, right: object) -> bool:
        raise RuntimeError('identity callback failed')


class Evaluator:
    component_id = 'evaluator'
    configuration_id = 'evaluator-v1'

    def __init__(self, result: object | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = 0

    def evaluate(
        self, solution: object, objectives: ObjectiveDeclaration
    ) -> ApplicationEvaluation:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return cast(ApplicationEvaluation, self.result)


class Executor:
    component_id = 'executor'

    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        evaluator: ApplicationEvaluator,
        solution: object,
        objectives: ObjectiveDeclaration,
    ) -> ExecutionResult:
        self.calls += 1
        try:
            evaluation = evaluator.evaluate(solution, objectives)
        except Exception as error:
            from multiobjective_vns.records.failures import ComponentFailure

            return ExecutionResult(
                None,
                ComponentFailure(
                    FailureKind.CALLBACK_EXCEPTION,
                    evaluator.component_id,
                    'application_evaluation',
                    str(error),
                ),
            )
        return ExecutionResult(evaluation, None)


class RaisingExecutor(Executor):
    def execute(
        self,
        evaluator: ApplicationEvaluator,
        solution: object,
        objectives: ObjectiveDeclaration,
    ) -> ExecutionResult:
        raise OSError('executor unavailable')


class KeyedIdentity(Identity):
    def equivalent(self, left: object, right: object) -> bool:
        return cast(tuple[str, str], left)[1] == cast(tuple[str, str], right)[1]

    def lookup_key(self, solution: object) -> str:
        return cast(tuple[str, str], solution)[0]


def processor_for(
    evaluator: Evaluator,
    *,
    objectives: tuple[Objective, Objective] | None = None,
    exhaustive_identity_validation: bool = False,
    executor: Executor | None = None,
) -> tuple[CandidateProcessor, SolutionRegistry, Executor]:
    declaration = ObjectiveDeclaration(
        objectives or (Objective('one'), Objective('two'))
    )
    executor = executor or Executor()
    identity = Identity()
    registry = SolutionRegistry(
        identity, exhaustive_identity_validation=exhaustive_identity_validation
    )
    processor = CandidateProcessor(
        evaluation_processor=EvaluationProcessor(
            application_evaluator=evaluator,
            executor=executor,
            objective_declaration=declaration,
        )
    )
    return processor, registry, executor


def process_initial(
    processor: CandidateProcessor, registry: SolutionRegistry, solution: object
) -> CandidateProcessingResult:
    return processor.process(
        registry,
        Candidate(solution),
        source=SubmissionSource.INITIAL,
    )


def test_new_valid_solution_is_evaluated_and_registered_once() -> None:
    evaluator = Evaluator(ApplicationEvaluation(True, True, (3, 4)))
    processor, registry, executor = processor_for(evaluator)

    result = process_initial(processor, registry, ('solution',))

    assert result.evaluation_requested is True
    assert result.outcome.solution_id == SolutionId(0)
    assert result.outcome.assessment is ApplicationAssessment.VALID
    assert result.outcome.disposition is None
    assert (
        result.registry.records[0].evaluation_record.application_evaluation
        == evaluator.result
    )
    assert result.registry.records[0].archive_status is None
    assert executor.calls == evaluator.calls == 1


def test_duplicate_reuses_completed_or_failed_record_without_executor_call() -> None:
    evaluator = Evaluator(ApplicationEvaluation(True, False, (3, 4)))
    processor, registry, executor = processor_for(evaluator)
    first = process_initial(processor, registry, ('solution',))
    second = processor.process(
        first.registry,
        Candidate(('solution',), metadata={'proposal': 2}),
        source=SubmissionSource.OPERATOR,
        choice_id=ChoiceId.for_operator('swap'),
        base_solution_id=SolutionId(0),
        k=1,
        m=1,
        candidate_index=1,
    )

    assert second.outcome.solution_id == first.outcome.solution_id
    assert second.outcome.processing_status is ProcessingStatus.COMPLETED
    assert second.outcome.assessment is ApplicationAssessment.INFEASIBLE
    assert second.outcome.disposition is SearchDisposition.DUPLICATE
    assert second.outcome.metadata == {'proposal': 2}
    assert second.evaluation_requested is False
    assert executor.calls == 1


def test_invalid_representation_is_a_completed_invalid_assessment() -> None:
    evaluator = Evaluator(ApplicationEvaluation(False, None, None))
    processor, registry, _ = processor_for(evaluator)

    result = process_initial(processor, registry, ('malformed',))

    assert result.outcome.processing_status is ProcessingStatus.COMPLETED
    assert result.outcome.assessment is ApplicationAssessment.INVALID
    assert result.outcome.failures == ()


def test_unusable_objective_vector_is_normalized_and_reported() -> None:
    evaluator = Evaluator(ApplicationEvaluation(True, True, 'wrong'))  # type: ignore[arg-type]
    processor, registry, _ = processor_for(evaluator)

    result = process_initial(processor, registry, ('solution',))

    assert result.outcome.processing_status is ProcessingStatus.COMPLETED
    assert result.outcome.assessment is ApplicationAssessment.INVALID
    application_evaluation = result.registry.records[
        0
    ].evaluation_record.application_evaluation
    assert application_evaluation is not None
    assert application_evaluation.objective_values is None
    assert result.outcome.failures[0].kind is FailureKind.CONTRACT_VIOLATION


def test_malformed_classification_fields_create_failed_registry_record() -> None:
    evaluator = Evaluator(
        SimpleNamespace(
            representation_valid=True, feasible=None, objective_values=(1, 2)
        )
    )
    processor, registry, _ = processor_for(evaluator)

    result = process_initial(processor, registry, ('solution',))

    assert result.outcome.processing_status is ProcessingStatus.FAILED
    assert result.outcome.assessment is None
    assert result.outcome.disposition is SearchDisposition.REGISTERED
    assert result.registry.records[0].evaluation_record.application_evaluation is None
    assert result.outcome.failures[0].kind is FailureKind.CONTRACT_VIOLATION


def test_evaluator_exception_is_stored_and_duplicate_reuses_failure() -> None:
    evaluator = Evaluator(error=RuntimeError('evaluator error'))
    processor, registry, executor = processor_for(evaluator)
    first = process_initial(processor, registry, ('solution',))
    second = process_initial(processor, first.registry, ('solution',))

    assert first.outcome.processing_status is ProcessingStatus.FAILED
    assert first.outcome.disposition is SearchDisposition.REGISTERED
    assert second.outcome.processing_status is ProcessingStatus.FAILED
    assert second.outcome.disposition is SearchDisposition.DUPLICATE
    assert second.outcome.failures == first.outcome.failures
    assert second.evaluation_requested is False
    assert executor.calls == 1


def test_operator_outcome_retains_choice_and_base_metadata() -> None:
    evaluator = Evaluator(ApplicationEvaluation(True, True, (1, 2)))
    processor, registry, _ = processor_for(evaluator)
    result = processor.process(
        registry,
        Candidate(('solution',), metadata='move description'),
        source=SubmissionSource.OPERATOR,
        choice_id=ChoiceId.for_operator('swap'),
        base_solution_id=SolutionId(4),
        k=2,
        m=1,
        candidate_index=3,
    )

    assert result.outcome.k == 2
    assert result.outcome.m == 1
    assert result.outcome.candidate_index == 3
    assert result.outcome.base_solution_id == SolutionId(4)
    assert result.outcome.metadata == 'move description'
    assert result.outcome.solution_id == SolutionId(0)


def test_executor_exception_is_a_stored_infrastructure_failure() -> None:
    evaluator = Evaluator(ApplicationEvaluation(True, True, (1, 2)))
    processor, registry, _ = processor_for(evaluator, executor=RaisingExecutor())

    result = process_initial(processor, registry, ('solution',))

    failure = result.outcome.failures[0]
    assert result.outcome.processing_status is ProcessingStatus.FAILED
    assert failure.kind is FailureKind.INFRASTRUCTURE_FAILURE
    assert failure.source == 'executor'
    assert failure.phase == 'execution'
    assert result.registry.records[0].evaluation_record.failures == (failure,)


def test_objective_comparison_exception_normalizes_vector_to_invalid() -> None:
    evaluator = Evaluator(ApplicationEvaluation(True, True, (1, 2)))
    processor, registry, _ = processor_for(
        evaluator,
        objectives=(Objective('failing', fail=True), Objective('second')),
    )

    result = process_initial(processor, registry, ('solution',))

    application_evaluation = result.registry.records[
        0
    ].evaluation_record.application_evaluation
    assert application_evaluation is not None
    assert application_evaluation.objective_values is None
    assert result.outcome.processing_status is ProcessingStatus.COMPLETED
    assert result.outcome.assessment is ApplicationAssessment.INVALID
    assert result.outcome.failures[0].kind is FailureKind.CALLBACK_EXCEPTION
    assert result.outcome.failures[0].source == 'failing-component'


def test_exhaustive_key_mismatch_is_a_duplicate_with_identity_diagnostic() -> None:
    evaluator = Evaluator(ApplicationEvaluation(True, True, (1, 2)))
    processor, _, executor = processor_for(evaluator)
    registry = SolutionRegistry(KeyedIdentity(), exhaustive_identity_validation=True)
    first = processor.process(
        registry,
        Candidate(('first-key', 'same-solution')),
        source=SubmissionSource.INITIAL,
    )

    second = processor.process(
        first.registry,
        Candidate(('different-key', 'same-solution')),
        source=SubmissionSource.INITIAL,
    )

    assert second.outcome.solution_id == first.outcome.solution_id
    assert second.outcome.disposition is SearchDisposition.DUPLICATE
    assert second.outcome.processing_status is ProcessingStatus.COMPLETED
    assert second.outcome.failures[0].kind is FailureKind.CONTRACT_VIOLATION
    assert second.invocation_diagnostics == second.outcome.failures
    assert second.evaluation_requested is False
    assert executor.calls == 1


def test_identity_failure_rejects_submission_before_evaluation() -> None:
    evaluator = Evaluator(ApplicationEvaluation(True, True, (1, 2)))
    processor, initial_registry, executor = processor_for(evaluator)
    first = process_initial(processor, initial_registry, ('known-solution',))
    registry = SolutionRegistry(RaisingIdentity(), records=first.registry.records)

    result = process_initial(processor, registry, ('solution',))

    assert result.outcome.processing_status is ProcessingStatus.FAILED
    assert result.outcome.solution_id is None
    assert result.outcome.assessment is None
    assert result.outcome.disposition is None
    assert result.invocation_diagnostics == result.outcome.failures
    assert result.evaluation_requested is False
    assert result.registry.records == first.registry.records
    assert executor.calls == evaluator.calls == 1
