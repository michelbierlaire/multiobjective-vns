"""Stable invocation logs, results, and maintenance records.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass
from pathlib import Path

from multiobjective_vns.events import AlgorithmEvent
from multiobjective_vns.identifiers import InvocationId
from multiobjective_vns.records.candidates import CandidateOutcome
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.records.failures import ComponentFailure
from multiobjective_vns.records.policies import InvocationCounters
from multiobjective_vns.records.reporting import (
    AssessmentCount,
    ChoiceStatistics,
    ComponentConfigurationEntry,
    ComponentRole,
    DispositionCount,
    ExcursionFrequency,
    ExecutiveSummary,
    FailureCount,
    ModificationCountFrequency,
    OperatorCatalogEntry,
    ParetoSolutionReport,
    PromotedCompositionReport,
    ReportDiagnostic,
    ReportDiagnosticKind,
    RunProvenance,
)
from multiobjective_vns.records.state import (
    Checkpoint,
    CheckpointReference,
    SearchState,
)
from multiobjective_vns.records.status import TerminationReason


@dataclass(frozen=True, slots=True)
class RunLog:
    """Ordered in-memory event stream for one invocation."""

    invocation_id: InvocationId
    events: tuple[AlgorithmEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.invocation_id, InvocationId):
            raise TypeError('invocation_id must be an InvocationId')
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, AlgorithmEvent) for event in self.events
        ):
            raise TypeError('events must be a tuple of AlgorithmEvent values')
        for expected, event in enumerate(self.events):
            if event.invocation_id != self.invocation_id:
                raise ValueError('all events must belong to the RunLog invocation')
            if event.sequence_number != expected:
                raise ValueError(
                    'event sequence numbers must start at zero and be ordered'
                )


@dataclass(frozen=True, slots=True)
class LogArtifactPaths:
    """Paths created by an invocation's detailed and summary log files."""

    event_log_path: Path | None
    summary_path: Path | None


@dataclass(frozen=True, slots=True)
class LogFinalizationResult:
    """Completed summary and both generated log artifacts."""

    executive_summary: ExecutiveSummary
    event_log_path: Path
    summary_path: Path
    report_diagnostics: tuple[ReportDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Either one application evaluation or one executor diagnostic."""

    application_evaluation: ApplicationEvaluation | None
    failure: ComponentFailure | None

    def __post_init__(self) -> None:
        if (self.application_evaluation is None) == (self.failure is None):
            raise ValueError('exactly one execution result field must be present')


@dataclass(frozen=True, slots=True)
class RunResult:
    """Stable result of one search invocation."""

    search_state: SearchState
    candidate_outcomes: tuple[CandidateOutcome, ...]
    termination_reason: TerminationReason
    diagnostics: tuple[ComponentFailure, ...]
    counters: InvocationCounters
    run_log: RunLog | None = None
    executive_summary: ExecutiveSummary | None = None
    report_diagnostics: tuple[ReportDiagnostic, ...] = ()
    checkpoint_reference: CheckpointReference | None = None
    event_log_path: Path | None = None
    summary_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_outcomes, tuple) or any(
            not isinstance(outcome, CandidateOutcome)
            for outcome in self.candidate_outcomes
        ):
            raise TypeError('candidate_outcomes must be a tuple of CandidateOutcome')
        if not isinstance(self.termination_reason, TerminationReason):
            raise TypeError('termination_reason must be a TerminationReason')
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(failure, ComponentFailure) for failure in self.diagnostics
        ):
            raise TypeError('diagnostics must be a tuple of ComponentFailure')
        if not isinstance(self.counters, InvocationCounters):
            raise TypeError('counters must be InvocationCounters')


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    """Stable result of a state-maintenance invocation."""

    checkpoint: Checkpoint
    termination_reason: TerminationReason
    diagnostics: tuple[ComponentFailure, ...]
    counters: InvocationCounters
    run_log: RunLog | None = None
    executive_summary: ExecutiveSummary | None = None
    report_diagnostics: tuple[ReportDiagnostic, ...] = ()
    checkpoint_reference: CheckpointReference | None = None
    event_log_path: Path | None = None
    summary_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint, Checkpoint):
            raise TypeError('checkpoint must be a Checkpoint')
        if self.termination_reason not in (
            TerminationReason.MAINTENANCE_COMPLETED,
            TerminationReason.COMPONENT_FAILURE,
        ):
            raise ValueError('invalid maintenance termination reason')
        if self.counters != InvocationCounters():
            raise ValueError('maintenance counters are zero')


__all__ = [
    'AssessmentCount',
    'ChoiceStatistics',
    'ComponentConfigurationEntry',
    'ComponentRole',
    'DispositionCount',
    'ExecutiveSummary',
    'ExecutionResult',
    'ExcursionFrequency',
    'FailureCount',
    'LogArtifactPaths',
    'LogFinalizationResult',
    'MaintenanceResult',
    'ModificationCountFrequency',
    'OperatorCatalogEntry',
    'ParetoSolutionReport',
    'PromotedCompositionReport',
    'ReportDiagnostic',
    'ReportDiagnosticKind',
    'RunLog',
    'RunProvenance',
    'RunResult',
]
