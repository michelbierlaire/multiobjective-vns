"""Candidate, Operator result, and submission outcome records.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass

from multiobjective_vns.identifiers import ChoiceId, SolutionId
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.evaluation import (
    EvaluationRecord,
    ObjectiveBoundFinding,
)
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
class CandidateProposal:
    """One candidate and its actual modification count."""

    candidate: Candidate
    m: int

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, Candidate):
            raise TypeError('candidate must be a Candidate')
        require_integer(self.m, name='m', minimum=1)


@dataclass(frozen=True, slots=True)
class OperatorResult:
    """Ordered candidate proposals returned by one Operator call."""

    candidates: tuple[CandidateProposal, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(proposal, CandidateProposal) for proposal in self.candidates
        ):
            raise TypeError('candidates must be a tuple of CandidateProposal')


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
    max_candidates: int = 1

    def __post_init__(self) -> None:
        require_integer(self.k, name='k', minimum=1)
        require_integer(self.max_candidates, name='max_candidates', minimum=1)
        if not isinstance(self.ensure_feasible, bool):
            raise TypeError('ensure_feasible must be bool')


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    """Stable outcome of one initial or final Operator-candidate submission.

    ``objective_bound_findings`` repeats the stored findings for duplicates so
    each submission occurrence has a complete caller-facing result.
    """

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
    candidate_index: int | None = None
    objective_bound_findings: tuple[ObjectiveBoundFinding, ...] = ()

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
        if not isinstance(self.objective_bound_findings, tuple) or any(
            not isinstance(finding, ObjectiveBoundFinding)
            for finding in self.objective_bound_findings
        ):
            raise TypeError(
                'objective_bound_findings must be a tuple of ObjectiveBoundFinding'
            )
        if (
            self.disposition is SearchDisposition.OBJECTIVE_BOUND_EXCEEDED
            and not self.objective_bound_findings
        ):
            raise ValueError(
                'objective_bound_exceeded outcomes require at least one finding'
            )

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
                    self.candidate_index,
                )
            ):
                raise ValueError('initial outcomes cannot carry Operator metadata')
        else:
            if self.choice_id is None or self.base_solution_id is None:
                raise ValueError('Operator outcomes require choice and base IDs')
            if self.k is None or self.m is None:
                raise ValueError('Operator outcomes require k and m')
            if self.candidate_index is None:
                raise ValueError('Operator outcomes require candidate_index')
            require_integer(self.k, name='k', minimum=1)
            require_integer(self.m, name='m', minimum=1)
            if self.m > self.k:
                raise ValueError('m cannot exceed k')
            require_integer(self.candidate_index, name='candidate_index', minimum=1)
            if self.choice_id.kind.value == 'composition':
                if self.component_position is not CompositionPosition.SECOND:
                    raise ValueError(
                        'dynamic composition outcomes use component_position=second'
                    )
            elif self.component_position not in (
                None,
                CompositionPosition.SECOND,
            ):
                raise ValueError(
                    'standalone outcomes use no position or second for a composite'
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
