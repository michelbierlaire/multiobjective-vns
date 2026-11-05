"""Application evaluation findings and registry-owned solution records.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass
from typing import TypeAlias

from multiobjective_vns.identifiers import SolutionId
from multiobjective_vns.objectives import ObjectiveValue
from multiobjective_vns.records.failures import ComponentFailure
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    ArchiveStatus,
    ProcessingStatus,
)

ApplicationAnalysis: TypeAlias = object


@dataclass(frozen=True, slots=True)
class ApplicationEvaluation:
    """Joint application findings for representation, feasibility, and values.

    :param representation_valid: Whether the solution matches the application
        representation and invariants.
    :param feasible: Feasibility finding, or ``None`` for invalid input whose
        feasibility cannot be determined.
    :param objective_values: Ordered objective values or ``None`` when no
        usable vector is available.
    :param analysis: Optional opaque application analysis for Operators.
    """

    representation_valid: bool
    feasible: bool | None
    objective_values: tuple[ObjectiveValue, ...] | None
    analysis: ApplicationAnalysis | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.representation_valid, bool):
            raise TypeError('representation_valid must be bool')
        if self.feasible is not None and not isinstance(self.feasible, bool):
            raise TypeError('feasible must be bool or None')
        if self.representation_valid and self.feasible is None:
            raise ValueError('valid representations require a feasibility finding')


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    """The one stored application processing result for a solution ID."""

    processing_status: ProcessingStatus
    application_evaluation: ApplicationEvaluation | None
    assessment: ApplicationAssessment | None
    failures: tuple[ComponentFailure, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.processing_status, ProcessingStatus):
            raise TypeError('processing_status must be a ProcessingStatus')
        if not isinstance(self.failures, tuple) or any(
            not isinstance(failure, ComponentFailure) for failure in self.failures
        ):
            raise TypeError('failures must be a tuple of ComponentFailure records')
        if self.assessment is not None and not isinstance(
            self.assessment, ApplicationAssessment
        ):
            raise TypeError('assessment must be an ApplicationAssessment or None')
        if self.processing_status is ProcessingStatus.COMPLETED:
            if self.application_evaluation is None or self.assessment is None:
                raise ValueError(
                    'completed evaluations require findings and assessment'
                )
        elif self.application_evaluation is not None or self.assessment is not None:
            raise ValueError('failed evaluations have no classifiable findings')
        elif not self.failures:
            raise ValueError('failed evaluations require a ComponentFailure')


@dataclass(frozen=True, slots=True)
class SolutionRecord:
    """Registry record for one identified solution and its stored evaluation."""

    solution_id: SolutionId
    solution: object
    evaluation_record: EvaluationRecord
    operator_failures: tuple[ComponentFailure, ...] = ()
    archive_status: ArchiveStatus | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.solution_id, SolutionId):
            raise TypeError('solution_id must be a SolutionId')
        if not isinstance(self.evaluation_record, EvaluationRecord):
            raise TypeError('evaluation_record must be an EvaluationRecord')
        if not isinstance(self.operator_failures, tuple) or any(
            not isinstance(failure, ComponentFailure)
            for failure in self.operator_failures
        ):
            raise TypeError(
                'operator_failures must be a tuple of ComponentFailure records'
            )
        if self.archive_status is not None and not isinstance(
            self.archive_status, ArchiveStatus
        ):
            raise TypeError('archive_status must be an ArchiveStatus or None')
