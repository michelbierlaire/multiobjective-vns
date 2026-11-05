"""Replaceable policy callback protocols for search decisions and feedback.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from collections.abc import Mapping
from typing import Protocol

from multiobjective_vns.components import CheckpointComponent
from multiobjective_vns.identifiers import ChoiceId
from multiobjective_vns.records.policies import (
    ActivePoolDecision,
    ActivePoolRequest,
    ArchiveDecision,
    ArchiveRequest,
    BaseSelectionDecision,
    BaseSelectionRequest,
    BudgetDecision,
    BudgetRequest,
    ChoiceSelectionRequest,
    FeedbackObservation,
)
from multiobjective_vns.values import FiniteReal


class ArchivePolicy(CheckpointComponent, Protocol):
    """Choose archive membership using the exact Pareto relation."""

    def decide(self, request: ArchiveRequest) -> ArchiveDecision:
        """Return the candidate admission and exact removal set."""


class ExplorationPolicy(CheckpointComponent, Protocol):
    """Choose active-pool membership within the engine's eligible IDs."""

    def decide(self, request: ActivePoolRequest) -> ActivePoolDecision:
        """Return the complete active-pool membership for the request."""


class BaseSelectionPolicy(CheckpointComponent, Protocol):
    """Select a feasible active-pool solution as the next iteration base."""

    def select(self, request: BaseSelectionRequest) -> BaseSelectionDecision:
        """Return the selected base's SolutionId."""


class BudgetPolicy(CheckpointComponent, Protocol):
    """Authorize or stop the next search iteration."""

    def decide(self, request: BudgetRequest) -> BudgetDecision:
        """Return whether the next iteration may proceed."""


class FeedbackPolicy(CheckpointComponent, Protocol):
    """Track choice performance and supply a distribution for selection."""

    def observe(self, observation: FeedbackObservation) -> None:
        """Stage feedback from one committed candidate attempt."""

    def distribution(
        self, request: ChoiceSelectionRequest
    ) -> Mapping[ChoiceId, FiniteReal]:
        """Return probabilities covering every requested choice exactly once."""

    def reset(self) -> None:
        """Stage removal of accumulated counts while keeping choice identities."""

    def capture_state(self) -> object:
        """Capture policy state for a search commit or checkpoint."""

    def restore_state(self, payload: object) -> None:
        """Restore policy state from an opaque saved payload."""


__all__ = [
    'ArchivePolicy',
    'BaseSelectionPolicy',
    'BudgetPolicy',
    'ExplorationPolicy',
    'FeedbackPolicy',
]
