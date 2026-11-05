"""Structured component-failure diagnostics and failure categories.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from multiobjective_vns.identifiers import (
    ChoiceId,
    ComponentId,
    SolutionId,
    identifier_text,
)

FailurePhase: TypeAlias = str


class FailureKind(StrEnum):
    """Nature of a component failure."""

    CALLBACK_EXCEPTION = 'callback_exception'
    CONTRACT_VIOLATION = 'contract_violation'
    INFRASTRUCTURE_FAILURE = 'infrastructure_failure'


@dataclass(frozen=True, slots=True)
class ComponentFailure:
    """Diagnostic identifying one failed component operation.

    :param kind: Classification of the failure.
    :param source: Registered component that produced the failure.
    :param phase: Nonempty operation label; the phase namespace is open.
    :param reason: Human-readable, nonempty diagnostic detail.
    :param solution_id: Identified solution associated with the failure.
    :param choice_id: Operator choice associated with the failure.
    """

    kind: FailureKind
    source: ComponentId
    phase: FailurePhase
    reason: str
    solution_id: SolutionId | None = None
    choice_id: ChoiceId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FailureKind):
            raise TypeError('kind must be a FailureKind')
        identifier_text(self.source)
        if not isinstance(self.phase, str) or not self.phase:
            raise ValueError('phase must be a nonempty string')
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError('reason must be a nonempty string')
