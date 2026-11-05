"""Dispatch and normalize one application's evaluation of a solution.

Created: 2026-10-29T14:03:00+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import replace

from multiobjective_vns.callbacks import ApplicationEvaluator
from multiobjective_vns.components import ComponentIdentity
from multiobjective_vns.identifiers import SolutionId
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
)
from multiobjective_vns.records.failures import ComponentFailure, FailureKind
from multiobjective_vns.records.results import ExecutionResult
from multiobjective_vns.records.status import ApplicationAssessment, ProcessingStatus
from multiobjective_vns.services import Executor


class EvaluationProcessor:
    """Call an evaluator once and normalize its result to an ``EvaluationRecord``.

    :param application_evaluator: Joint application assessment callback.
    :param executor: Component that invokes the evaluator.
    :param objective_declaration: Ordered objective comparison contracts.
    """

    __slots__ = ('_application_evaluator', '_executor', '_objective_declaration')

    def __init__(
        self,
        *,
        application_evaluator: ApplicationEvaluator,
        executor: Executor,
        objective_declaration: ObjectiveDeclaration,
    ) -> None:
        self._application_evaluator = application_evaluator
        self._executor = executor
        self._objective_declaration = objective_declaration

    def evaluate(self, solution: object, solution_id: SolutionId) -> EvaluationRecord:
        """Dispatch one evaluation and attach all findings to ``solution_id``."""
        try:
            execution = self._executor.execute(
                self._application_evaluator,
                solution,
                self._objective_declaration,
            )
        except Exception as error:
            return self._failed_record(
                self._failure(
                    FailureKind.INFRASTRUCTURE_FAILURE,
                    self._executor,
                    'execution',
                    f'Executor.execute raised: {error}',
                    solution_id,
                )
            )

        if not isinstance(execution, ExecutionResult):
            return self._failed_record(
                self._failure(
                    FailureKind.CONTRACT_VIOLATION,
                    self._executor,
                    'execution',
                    'Executor.execute must return an ExecutionResult',
                    solution_id,
                )
            )
        if execution.failure is not None:
            if not isinstance(execution.failure, ComponentFailure):
                failure = self._failure(
                    FailureKind.CONTRACT_VIOLATION,
                    self._executor,
                    'execution',
                    'ExecutionResult.failure must be a ComponentFailure',
                    solution_id,
                )
            else:
                failure = replace(execution.failure, solution_id=solution_id)
            return self._failed_record(failure)
        if not isinstance(execution.application_evaluation, ApplicationEvaluation):
            return self._failed_record(
                self._failure(
                    FailureKind.CONTRACT_VIOLATION,
                    self._application_evaluator,
                    'application_evaluation',
                    'ApplicationEvaluator must return an ApplicationEvaluation',
                    solution_id,
                )
            )

        return self._normalize_evaluation(
            execution.application_evaluation,
            solution_id,
        )

    def _normalize_evaluation(
        self,
        evaluation: ApplicationEvaluation,
        solution_id: SolutionId,
    ) -> EvaluationRecord:
        representation_valid = evaluation.representation_valid
        feasible = evaluation.feasible
        if not isinstance(representation_valid, bool):
            return self._failed_record(
                self._failure(
                    FailureKind.CONTRACT_VIOLATION,
                    self._application_evaluator,
                    'application_evaluation',
                    'representation_valid must be bool',
                    solution_id,
                )
            )
        if feasible is not None and not isinstance(feasible, bool):
            return self._failed_record(
                self._failure(
                    FailureKind.CONTRACT_VIOLATION,
                    self._application_evaluator,
                    'application_evaluation',
                    'feasible must be bool or None',
                    solution_id,
                )
            )
        if representation_valid and feasible is None:
            return self._failed_record(
                self._failure(
                    FailureKind.CONTRACT_VIOLATION,
                    self._application_evaluator,
                    'application_evaluation',
                    'valid representations require a feasibility finding',
                    solution_id,
                )
            )

        objective_values = evaluation.objective_values
        failures: list[ComponentFailure] = []
        normalized_values: tuple[object, ...] | None = None
        if objective_values is None:
            if representation_valid:
                failures.append(
                    self._failure(
                        FailureKind.CONTRACT_VIOLATION,
                        self._application_evaluator,
                        'application_evaluation',
                        'representation-valid solutions require an objective vector',
                        solution_id,
                    )
                )
        elif not isinstance(objective_values, tuple):
            failures.append(
                self._failure(
                    FailureKind.CONTRACT_VIOLATION,
                    self._application_evaluator,
                    'application_evaluation',
                    'objective_values must be a tuple',
                    solution_id,
                )
            )
        elif len(objective_values) != len(self._objective_declaration.objectives):
            failures.append(
                self._failure(
                    FailureKind.CONTRACT_VIOLATION,
                    self._application_evaluator,
                    'application_evaluation',
                    'objective vector length does not match ObjectiveDeclaration',
                    solution_id,
                )
            )
        else:
            failures.extend(
                self._objective_value_failures(objective_values, solution_id)
            )
            if not failures:
                normalized_values = objective_values

        normalized_evaluation = ApplicationEvaluation(
            representation_valid=representation_valid,
            feasible=feasible,
            objective_values=normalized_values,
            analysis=evaluation.analysis,
        )
        if not representation_valid or normalized_values is None:
            assessment = ApplicationAssessment.INVALID
        elif feasible is False:
            assessment = ApplicationAssessment.INFEASIBLE
        else:
            assessment = ApplicationAssessment.VALID
        return EvaluationRecord(
            processing_status=ProcessingStatus.COMPLETED,
            application_evaluation=normalized_evaluation,
            assessment=assessment,
            failures=tuple(failures),
        )

    def _objective_value_failures(
        self,
        values: tuple[object, ...],
        solution_id: SolutionId,
    ) -> list[ComponentFailure]:
        failures: list[ComponentFailure] = []
        for objective, value in zip(
            self._objective_declaration.objectives,
            values,
            strict=True,
        ):
            try:
                comparison = objective.compare(value, value)
            except Exception as error:
                failures.append(
                    self._failure(
                        FailureKind.CALLBACK_EXCEPTION,
                        objective,
                        'objective_comparison',
                        f'ObjectiveFunction.compare raised: {error}',
                        solution_id,
                    )
                )
                continue
            if not isinstance(comparison, ObjectiveComparison):
                failures.append(
                    self._failure(
                        FailureKind.CONTRACT_VIOLATION,
                        objective,
                        'objective_comparison',
                        'ObjectiveFunction.compare returned an invalid result',
                        solution_id,
                    )
                )
            elif comparison is not ObjectiveComparison.EQUAL:
                failures.append(
                    self._failure(
                        FailureKind.CONTRACT_VIOLATION,
                        objective,
                        'objective_comparison',
                        'ObjectiveFunction.compare(value, value) must be equal',
                        solution_id,
                    )
                )
        return failures

    @staticmethod
    def _failed_record(failure: ComponentFailure) -> EvaluationRecord:
        return EvaluationRecord(
            processing_status=ProcessingStatus.FAILED,
            application_evaluation=None,
            assessment=None,
            failures=(failure,),
        )

    @staticmethod
    def _failure(
        kind: FailureKind,
        component: ComponentIdentity,
        phase: str,
        reason: str,
        solution_id: SolutionId,
    ) -> ComponentFailure:
        return ComponentFailure(
            kind=kind,
            source=component.component_id,
            phase=phase,
            reason=reason or 'component failed without a diagnostic',
            solution_id=solution_id,
        )


__all__ = ['EvaluationProcessor']
