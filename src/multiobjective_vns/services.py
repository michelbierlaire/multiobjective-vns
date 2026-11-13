"""Protocols for evaluation, checkpoint, and reporting services.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from pathlib import Path
from typing import Protocol

from multiobjective_vns.callbacks import ApplicationEvaluator
from multiobjective_vns.components import ComponentIdentity
from multiobjective_vns.config import FeedbackSelectionMode
from multiobjective_vns.events import AlgorithmEvent
from multiobjective_vns.identifiers import InvocationId, ProblemInstanceId, SolutionId
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.policies import InvocationCounters
from multiobjective_vns.records.results import (
    ExecutionResult,
    LogFinalizationResult,
    PromotedCompositionReport,
    RunLog,
    ViewerExportScope,
)
from multiobjective_vns.records.state import (
    Checkpoint,
    CheckpointReference,
    SearchState,
)
from multiobjective_vns.records.status import TerminationReason


class Executor(ComponentIdentity, Protocol):
    """Service that invokes the application evaluator for one solution."""

    def execute(
        self,
        evaluator: ApplicationEvaluator,
        solution: object,
        objectives: ObjectiveDeclaration,
    ) -> ExecutionResult:
        """Invoke the evaluator once and report its result or execution failure."""


class CheckpointStore(ComponentIdentity, Protocol):
    """Service that atomically saves and restores complete checkpoints."""

    def save(self, checkpoint: Checkpoint) -> CheckpointReference:
        """Persist a complete snapshot and return its opaque reference."""

    def load(self, reference: CheckpointReference) -> Checkpoint:
        """Restore the complete checkpoint identified by ``reference``."""


class SolutionFormatter(ComponentIdentity, Protocol):
    """Optional callback supplying a readable solution description."""

    def describe(self, solution: object) -> str:
        """Return a human-readable description for one solution."""


class RunLogger(ComponentIdentity, Protocol):
    """Service that records events and renders invocation reports."""

    def begin(self, invocation_id: InvocationId, log_directory: Path) -> None:
        """Create unique event and summary artifacts for an invocation."""

    def append(self, events: tuple[AlgorithmEvent, ...]) -> None:
        """Atomically append one complete event batch."""

    def publish_view(
        self,
        invocation_id: InvocationId,
        problem_instance_id: ProblemInstanceId,
        committed_state: SearchState,
        commit_sequence: int,
        objective_declaration: ObjectiveDeclaration,
        scope: ViewerExportScope,
        is_final: bool,
        solution_formatter: SolutionFormatter | None,
    ) -> Path:
        """Atomically publish one committed registry or Pareto viewer export."""

    def finalize(
        self,
        run_log: RunLog,
        initial_archive_ids: tuple[SolutionId, ...],
        final_state: SearchState,
        objective_declaration: ObjectiveDeclaration,
        termination_reason: TerminationReason,
        counters: InvocationCounters,
        feedback_selection_mode: FeedbackSelectionMode | None,
        promoted_compositions: tuple[PromotedCompositionReport, ...],
        solution_formatter: SolutionFormatter | None,
    ) -> LogFinalizationResult:
        """Build the structured summary and write the invocation artifacts."""


__all__ = [
    'CheckpointStore',
    'Executor',
    'RunLogger',
    'SolutionFormatter',
]
