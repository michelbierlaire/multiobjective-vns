"""Candidate, Operator result, and submission outcome records.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass

from multiobjective_vns.identifiers import ChoiceId, SolutionId
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.evaluation import EvaluationRecord
from multiobjective_vns.records.failures import ComponentFailure
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    CompositionPosition,
    ProcessingStatus,
    SearchDisposition,
    SubmissionSource,
)
from multiobjective_vns.values import require_integer


@dataclass(frozen=True, slots=True)
class Candidate:
    """Application solution proposed by an Operator with optional metadata."""

    solution: object
    metadata: object | None = None


@dataclass(frozen=True, slots=True)
class OperatorResult:
    """Modification count and candidate from one Operator call."""

    m: int
    candidate: Candidate | None

    def __post_init__(self) -> None:
        require_integer(self.m, name='m', minimum=0)
        if self.candidate is not None and not isinstance(self.candidate, Candidate):
            raise TypeError('candidate must be a Candidate or None')
        if (self.m == 0) != (self.candidate is None):
            raise ValueError('candidate must be None exactly when m is zero')


@dataclass(frozen=True, slots=True)
class CandidateContext:
    """Inputs provided to one Operator call.

    :param base: Read-only application solution used as the starting value.
    :param base_evaluation: Stored evaluation record for a registered base;
        ``None`` when the Operator receives an unregistered composition value.
    :param k: Positive neighborhood size supported by the Operator.
    :param ensure_feasible: Whether the Operator must return a feasible result.
    :param operator_state: Operator-owned mutable continuation state.
    :param random_source: Shared random stream for the complete search.
    """

    base: object
    k: int
    ensure_feasible: bool
    operator_state: object | None
    random_source: RandomSource
    base_evaluation: EvaluationRecord | None = None

    def __post_init__(self) -> None:
        require_integer(self.k, name='k', minimum=1)
        if not isinstance(self.ensure_feasible, bool):
            raise TypeError('ensure_feasible must be bool')


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    """Stable outcome of one initial or final Operator-candidate submission."""

    source: SubmissionSource
    submitted_solution: object
    processing_status: ProcessingStatus
    failures: tuple[ComponentFailure, ...] = ()
    choice_id: ChoiceId | None = None
    component_position: CompositionPosition | None = None
    base_solution_id: SolutionId | None = None
    k: int | None = None
    m: int | None = None
    solution_id: SolutionId | None = None
    assessment: ApplicationAssessment | None = None
    disposition: SearchDisposition | None = None
    metadata: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, SubmissionSource):
            raise TypeError('source must be a SubmissionSource')
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
        if self.disposition is not None and not isinstance(
            self.disposition, SearchDisposition
        ):
            raise TypeError('disposition must be a SearchDisposition or None')

        if self.source is SubmissionSource.INITIAL:
            if any(
                value is not None
                for value in (
                    self.choice_id,
                    self.component_position,
                    self.base_solution_id,
                    self.k,
                    self.m,
                    self.metadata,
                )
            ):
                raise ValueError('initial outcomes cannot carry Operator metadata')
        else:
            if self.choice_id is None or self.base_solution_id is None:
                raise ValueError('Operator outcomes require choice and base IDs')
            if self.k is None or self.m is None:
                raise ValueError('Operator outcomes require k and m')
            require_integer(self.k, name='k', minimum=1)
            require_integer(self.m, name='m', minimum=1)
            if self.m > self.k:
                raise ValueError('m cannot exceed k')
            if self.choice_id.kind.value == 'operator':
                if self.component_position is not None:
                    raise ValueError('standalone outcomes have no component position')
            elif self.component_position is not CompositionPosition.SECOND:
                raise ValueError(
                    'composition candidates have component_position=second'
                )

        if self.processing_status is ProcessingStatus.COMPLETED:
            if self.assessment is None:
                raise ValueError('completed outcomes require an assessment')
        elif self.assessment is not None:
            raise ValueError('failed outcomes have no application assessment')
        elif not self.failures:
            raise ValueError('failed outcomes require a ComponentFailure')
        if self.solution_id is None and (
            self.assessment is not None or self.disposition is not None
        ):
            raise ValueError('assessment and disposition require an established ID')
