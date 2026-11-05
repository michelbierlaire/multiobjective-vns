"""Protocol for the one random stream shared by search components.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from multiobjective_vns.identifiers import ComponentConfigurationId, ComponentId

if TYPE_CHECKING:
    from multiobjective_vns.callbacks import StatePayload


class RandomSource(Protocol):
    """Shared source of random values used by Operators and policies."""

    @property
    def component_id(self) -> ComponentId:
        """Return the registered identity used for failure attribution."""

    @property
    def configuration_id(self) -> ComponentConfigurationId:
        """Identify generator setup and continuation behavior."""

    def random(self) -> float:
        """Return a float in the half-open interval ``[0, 1)``."""

    def capture_state(self) -> StatePayload:
        """Capture the current stream state for a committed snapshot."""

    def restore_state(self, payload: StatePayload) -> None:
        """Restore a previously captured stream state."""
