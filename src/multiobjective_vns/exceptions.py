"""Public exceptions for registration, setup, restoration, and run failures.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from multiobjective_vns.identifiers import InvocationId

if TYPE_CHECKING:
    from multiobjective_vns.records.candidates import CandidateOutcome
    from multiobjective_vns.records.failures import ComponentFailure
    from multiobjective_vns.records.results import (
        ExecutiveSummary,
        ReportDiagnostic,
        RunLog,
    )
    from multiobjective_vns.records.state import CheckpointReference


class ComponentRegistrationError(ValueError):
    """Raised when a component registration violates its public contract."""


class ResumeCompatibilityError(ValueError):
    """Raised when a saved search and current application setup differ."""


class ComponentSetupError(RuntimeError):
    """Raised when required component state cannot be prepared or restored."""

    def __init__(self, failure: ComponentFailure) -> None:
        super().__init__(failure.reason)
        self.failure = failure


class CheckpointRestoreError(RuntimeError):
    """Raised when a store or solution codec cannot restore a checkpoint."""

    def __init__(
        self,
        checkpoint_reference: CheckpointReference,
        failure: ComponentFailure,
    ) -> None:
        super().__init__(failure.reason)
        self.checkpoint_reference = checkpoint_reference
        self.failure = failure


class LogFinalizationError(RuntimeError):
    """Raised when the logger cannot complete invocation finalization."""

    def __init__(
        self,
        *,
        failure: ComponentFailure,
        event_log_path: Path | None,
        summary_path: Path | None,
        executive_summary: ExecutiveSummary | None,
        report_diagnostics: tuple[ReportDiagnostic, ...],
        registry_view_path: Path | None = None,
        pareto_view_path: Path | None = None,
    ) -> None:
        super().__init__(failure.reason)
        self.failure = failure
        self.event_log_path = event_log_path
        self.summary_path = summary_path
        self.registry_view_path = registry_view_path
        self.pareto_view_path = pareto_view_path
        self.executive_summary = executive_summary
        self.report_diagnostics = report_diagnostics


class InitialSolutionsError(ValueError):
    """Raised after processing an initial set with no valid feasible value."""

    def __init__(
        self,
        *,
        invocation_id: InvocationId,
        candidate_outcomes: tuple[CandidateOutcome, ...],
        run_log: RunLog | None = None,
        executive_summary: ExecutiveSummary | None = None,
        diagnostics: tuple[ComponentFailure, ...] = (),
        event_log_path: Path | None = None,
        summary_path: Path | None = None,
        registry_view_path: Path | None = None,
        pareto_view_path: Path | None = None,
        report_diagnostics: tuple[ReportDiagnostic, ...] = (),
    ) -> None:
        super().__init__('initial solutions contain no valid feasible solution')
        self.invocation_id = invocation_id
        self.candidate_outcomes = candidate_outcomes
        self.run_log = run_log
        self.executive_summary = executive_summary
        self.diagnostics = diagnostics
        self.event_log_path = event_log_path
        self.summary_path = summary_path
        self.registry_view_path = registry_view_path
        self.pareto_view_path = pareto_view_path
        self.report_diagnostics = report_diagnostics
