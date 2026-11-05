"""Tests for application evaluation dispatch and normalization.

Created: 2026-10-29T14:01:00+00:00
Author: Michel Bierlaire, EPFL
"""

from typing import cast

from multiobjective_vns.callbacks import ApplicationEvaluator
from multiobjective_vns.evaluation import EvaluationProcessor
from multiobjective_vns.identifiers import SolutionId
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.records.failures import FailureKind
from multiobjective_vns.records.results import ExecutionResult
from multiobjective_vns.records.status import ApplicationAssessment, ProcessingStatus


class Objective:
    def __init__(self, name: str) -> None:
        self.objective_id = name
        self.component_id = f'{name}-comparison'
        self.configuration_id = f'{name}-v1'

    def compare(self, left: object, right: object) -> ObjectiveComparison:
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


class Application:
    component_id = 'application'
    configuration_id = 'application-v1'

    def __init__(self, result: object) -> None:
        self.result = result

    def evaluate(
        self, solution: object, objectives: ObjectiveDeclaration
    ) -> ApplicationEvaluation:
        return cast(ApplicationEvaluation, self.result)


class InlineExecutor:
    component_id = 'executor'

    def execute(
        self,
        evaluator: ApplicationEvaluator,
        solution: object,
        objectives: ObjectiveDeclaration,
    ) -> ExecutionResult:
        try:
            result = evaluator.evaluate(solution, objectives)
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
        return ExecutionResult(result, None)


def processor_for(result: object) -> EvaluationProcessor:
    return EvaluationProcessor(
        application_evaluator=Application(result),
        executor=InlineExecutor(),
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost'), Objective('distance'))
        ),
    )


def test_valid_application_result_becomes_a_valid_evaluation_record() -> None:
    analysis = ('loads', (2, 3))
    processor = processor_for(
        ApplicationEvaluation(True, True, (1, 2), analysis=analysis)
    )

    record = processor.evaluate(('solution',), SolutionId(3))

    assert record.processing_status is ProcessingStatus.COMPLETED
    assert record.assessment is ApplicationAssessment.VALID
    assert record.failures == ()
    assert record.application_evaluation is not None
    assert record.application_evaluation.analysis is analysis


def test_unusable_objective_values_are_normalized_to_invalid() -> None:
    analysis = ('partial diagnostics',)
    processor = processor_for(
        ApplicationEvaluation(
            True,
            True,
            cast(tuple[int, int], 'bad-vector'),
            analysis=analysis,
        )
    )

    record = processor.evaluate(('solution',), SolutionId(3))

    assert record.processing_status is ProcessingStatus.COMPLETED
    assert record.assessment is ApplicationAssessment.INVALID
    assert record.application_evaluation is not None
    assert record.application_evaluation.objective_values is None
    assert record.application_evaluation.analysis is analysis
    assert record.failures[0].kind is FailureKind.CONTRACT_VIOLATION


def test_malformed_classification_result_is_unclassifiable() -> None:
    processor = processor_for(
        {'representation_valid': True, 'feasible': None, 'objective_values': (1, 2)}
    )

    record = processor.evaluate(('solution',), SolutionId(3))

    assert record.processing_status is ProcessingStatus.FAILED
    assert record.application_evaluation is None
    assert record.assessment is None
    assert record.failures[0].kind is FailureKind.CONTRACT_VIOLATION
