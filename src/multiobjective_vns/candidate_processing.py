"""Identity-first evaluation and registry handling for candidate submissions.

Created: 2026-10-29T13:54:42+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass

from multiobjective_vns.evaluation import EvaluationProcessor
from multiobjective_vns.identifiers import ChoiceId, SolutionId
from multiobjective_vns.records.candidates import Candidate, CandidateOutcome
from multiobjective_vns.records.failures import ComponentFailure, FailureKind
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    CompositionPosition,
    ProcessingStatus,
    SearchDisposition,
    SubmissionSource,
)
from multiobjective_vns.registry import SolutionRegistry


@dataclass(frozen=True, slots=True)
class CandidateProcessingResult:
    """Staged candidate outcome, registry snapshot, and request accounting."""

    registry: SolutionRegistry
    outcome: CandidateOutcome
    evaluation_requested: bool
    invocation_diagnostics: tuple[ComponentFailure, ...]


class CandidateProcessor:
    """Resolve identity, reuse or evaluate, and stage a registry record.

    The processor leaves archive and active-pool decisions to later search
    layers. It returns an immutable registry snapshot so a caller can publish
    the candidate and its record together in a search commit.
    """

    __slots__ = ('_evaluation_processor',)

    def __init__(self, *, evaluation_processor: EvaluationProcessor) -> None:
        self._evaluation_processor = evaluation_processor

    def process(
        self,
        registry: SolutionRegistry,
        candidate: Candidate,
        *,
        source: SubmissionSource,
        choice_id: ChoiceId | None = None,
        base_solution_id: SolutionId | None = None,
        k: int | None = None,
        m: int | None = None,
        component_position: CompositionPosition | None = None,
    ) -> CandidateProcessingResult:
        """Prepare one initial or Operator-produced candidate submission.

        :param registry: Last committed registry snapshot.
        :param candidate: Application solution and optional immutable metadata.
        :param source: Whether this is an initial or Operator submission.
        :param choice_id: Selected Operator choice for an Operator submission.
        :param base_solution_id: Selected search base for an Operator submission.
        :param k: Requested neighborhood size for an Operator submission.
        :param m: Actual modification count for an Operator submission.
        :param component_position: Position of the final Operator in a composition.
        :returns: Candidate result and staged registry state.
        """
        resolution = registry.resolve(candidate.solution)
        identity_failures = (
            (resolution.failure,) if resolution.failure is not None else ()
        )
        if resolution.solution_id is None and resolution.failure is not None:
            outcome = self._outcome(
                candidate,
                source=source,
                processing_status=ProcessingStatus.FAILED,
                failures=identity_failures,
                choice_id=choice_id,
                base_solution_id=base_solution_id,
                k=k,
                m=m,
                component_position=component_position,
            )
            return CandidateProcessingResult(
                registry, outcome, False, identity_failures
            )

        if resolution.solution_id is not None:
            existing = registry.get(resolution.solution_id)
            stored = existing.evaluation_record
            failures = (*stored.failures, *identity_failures)
            outcome = self._outcome(
                candidate,
                source=source,
                processing_status=stored.processing_status,
                failures=failures,
                choice_id=choice_id,
                base_solution_id=base_solution_id,
                k=k,
                m=m,
                component_position=component_position,
                solution_id=existing.solution_id,
                assessment=stored.assessment,
                disposition=SearchDisposition.DUPLICATE,
            )
            return CandidateProcessingResult(
                registry, outcome, False, identity_failures
            )

        solution_id = SolutionId(registry.next_sequence)
        evaluation_record = self._evaluation_processor.evaluate(
            candidate.solution,
            solution_id,
        )
        try:
            updated_registry, solution_record = registry.register(
                candidate.solution,
                evaluation_record,
                lookup_key=(
                    resolution.lookup_key if resolution.has_lookup_key else None
                ),
            )
        except Exception as error:
            failure = ComponentFailure(
                kind=FailureKind.CONTRACT_VIOLATION,
                source=registry.identity_component_id,
                phase='identity_resolution',
                reason=f'lookup-key index update failed: {error}',
            )
            outcome = self._outcome(
                candidate,
                source=source,
                processing_status=ProcessingStatus.FAILED,
                failures=(failure,),
                choice_id=choice_id,
                base_solution_id=base_solution_id,
                k=k,
                m=m,
                component_position=component_position,
            )
            return CandidateProcessingResult(registry, outcome, True, (failure,))

        assessment = solution_record.evaluation_record.assessment
        if (
            assessment
            in (
                ApplicationAssessment.INVALID,
                ApplicationAssessment.INFEASIBLE,
            )
            or solution_record.evaluation_record.processing_status
            is ProcessingStatus.FAILED
        ):
            disposition: SearchDisposition | None = SearchDisposition.REGISTERED
        else:
            disposition = None
        outcome = self._outcome(
            candidate,
            source=source,
            processing_status=solution_record.evaluation_record.processing_status,
            failures=solution_record.evaluation_record.failures,
            choice_id=choice_id,
            base_solution_id=base_solution_id,
            k=k,
            m=m,
            component_position=component_position,
            solution_id=solution_record.solution_id,
            assessment=assessment,
            disposition=disposition,
        )
        return CandidateProcessingResult(updated_registry, outcome, True, ())

    def _outcome(
        self,
        candidate: Candidate,
        *,
        source: SubmissionSource,
        processing_status: ProcessingStatus,
        failures: tuple[ComponentFailure, ...],
        choice_id: ChoiceId | None,
        base_solution_id: SolutionId | None,
        k: int | None,
        m: int | None,
        component_position: CompositionPosition | None,
        solution_id: SolutionId | None = None,
        assessment: ApplicationAssessment | None = None,
        disposition: SearchDisposition | None = None,
    ) -> CandidateOutcome:
        return CandidateOutcome(
            source=source,
            submitted_solution=candidate.solution,
            processing_status=processing_status,
            failures=failures,
            choice_id=choice_id,
            component_position=component_position,
            base_solution_id=base_solution_id,
            k=k,
            m=m,
            solution_id=solution_id,
            assessment=assessment,
            disposition=disposition,
            metadata=candidate.metadata,
        )


__all__ = ['CandidateProcessingResult', 'CandidateProcessor']
