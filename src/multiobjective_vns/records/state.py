"""Opaque continuation-state and checkpoint records.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass
from typing import Protocol, TypeAlias

from multiobjective_vns.records.evaluation import SolutionRecord

CheckpointReference: TypeAlias = object


class SearchState(Protocol):
    """Read-only continuation snapshot exposed by search results."""

    def pareto_solutions(self) -> tuple[SolutionRecord, ...]:
        """Return current Pareto members in registry insertion order."""

    def active_solutions(self) -> tuple[SolutionRecord, ...]:
        """Return active-pool members in registry insertion order."""

    def visited_solutions(self) -> tuple[SolutionRecord, ...]:
        """Return every identity-resolved registry record in insertion order."""


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Opaque immutable continuation snapshot accepted by ``run()``."""

    search_state: SearchState

    def __post_init__(self) -> None:
        for method_name in (
            'pareto_solutions',
            'active_solutions',
            'visited_solutions',
        ):
            if not callable(getattr(self.search_state, method_name, None)):
                raise TypeError(f'search_state must provide {method_name}()')
