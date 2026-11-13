"""Structural protocols for application-provided search callbacks.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from collections.abc import Hashable
from typing import Protocol, TypeAlias

from multiobjective_vns.components import CheckpointComponent
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation

Solution: TypeAlias = object
CodecPayload: TypeAlias = bytes
StatePayload: TypeAlias = object
LookupKey: TypeAlias = Hashable


class Operator(Protocol):
    """Application callback that generates one result from a search base."""

    def call(self, context: CandidateContext) -> OperatorResult:
        """Generate a result using the supplied base, size, and context."""


class ApplicationEvaluator(CheckpointComponent, Protocol):
    """Joint application assessment callback for one solution."""

    def evaluate(
        self, solution: object, objectives: ObjectiveDeclaration
    ) -> ApplicationEvaluation:
        """Return representation, feasibility, and objective findings."""


class SolutionIdentity(CheckpointComponent, Protocol):
    """Application rule for semantic equivalence between solutions."""

    def equivalent(self, left: object, right: object) -> bool:
        """Report whether two application values represent the same solution."""


class KeyedSolutionIdentity(SolutionIdentity, Protocol):
    """Solution-identity protocol with an optional lookup-key accelerator."""

    def lookup_key(self, solution: Solution) -> LookupKey:
        """Return a deterministic, immutable, hashable lookup key."""


class SolutionCodec(CheckpointComponent, Protocol):
    """Application-owned byte encoding and restoration of solution values."""

    def capture(self, solution: Solution) -> CodecPayload:
        """Capture application-defined bytes representing a solution."""

    def restore(self, payload: CodecPayload) -> Solution:
        """Restore the application solution represented by encoded bytes."""


class StatefulOperator(Operator, Protocol):
    """Operator protocol for implementations with continuation state."""

    def capture_state(self, state: object) -> StatePayload:
        """Capture the state after construction or an Operator call."""

    def restore_state(self, payload: StatePayload) -> object:
        """Restore state and return the value for the next Operator call."""


class StopToken(Protocol):
    """Thread-safe request handle for graceful search termination."""

    def request_stop(self) -> None:
        """Request a stop at the next safe point."""

    def is_stop_requested(self) -> bool:
        """Return whether a graceful stop has been requested."""
