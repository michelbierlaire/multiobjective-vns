"""Protocols for application extensions that observe committed transitions.

Created: 2026-10-29T13:47:55+00:00
Author: Michel Bierlaire, EPFL
"""

from typing import Protocol

from multiobjective_vns.callbacks import StatePayload
from multiobjective_vns.records.extensions import (
    ExtensionCommitResult,
    SearchExtensionRequest,
)


class SearchExtension(Protocol):
    """Application callback for committed search-transition data."""

    def prepare_commit(self, request: SearchExtensionRequest) -> ExtensionCommitResult:
        """Prepare extension state and events for one pending transition."""


class StatefulSearchExtension(SearchExtension, Protocol):
    """Extension callback with checkpointed continuation state."""

    def capture_state(self) -> StatePayload:
        """Capture the current extension state as an opaque payload."""

    def restore_state(self, payload: StatePayload) -> None:
        """Restore the extension state from its captured payload."""
