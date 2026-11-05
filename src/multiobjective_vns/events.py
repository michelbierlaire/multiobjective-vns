"""Versioned event records and the observer callback protocol.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, TypeAlias

from multiobjective_vns.components import ComponentIdentity
from multiobjective_vns.identifiers import (
    ChoiceId,
    ComponentId,
    InvocationId,
    SolutionId,
    identifier_text,
)
from multiobjective_vns.values import require_integer

EventType: TypeAlias = str
EventSchemaVersion: TypeAlias = int
EventPayload: TypeAlias = Mapping[str, object]


class EventPhase(StrEnum):
    """Broad search phase used to group related event types."""

    INVOCATION = 'invocation'
    INITIALIZATION = 'initialization'
    BASE_SELECTION = 'base_selection'
    CHOICE_SELECTION = 'choice_selection'
    OPERATOR_CALL = 'operator_call'
    COMPOSITION = 'composition'
    CANDIDATE_PROCESSING = 'candidate_processing'
    EXCURSION = 'excursion'
    ARCHIVE = 'archive'
    ACTIVE_POOL = 'active_pool'
    FEEDBACK = 'feedback'
    BUDGET = 'budget'
    CHECKPOINT = 'checkpoint'
    FAILURE = 'failure'
    EXTENSION = 'extension'


_STANDARD_EVENT_PHASES: dict[str, EventPhase] = {
    'invocation_started': EventPhase.INVOCATION,
    'maintenance_requested': EventPhase.INVOCATION,
    'initialization_completed': EventPhase.INITIALIZATION,
    'initialization_failed': EventPhase.INITIALIZATION,
    'base_selected': EventPhase.BASE_SELECTION,
    'choice_set_available': EventPhase.CHOICE_SELECTION,
    'choice_category_selected': EventPhase.CHOICE_SELECTION,
    'choice_selected': EventPhase.CHOICE_SELECTION,
    'operator_result': EventPhase.OPERATOR_CALL,
    'composition_result': EventPhase.COMPOSITION,
    'composition_promoted': EventPhase.COMPOSITION,
    'candidate_processed': EventPhase.CANDIDATE_PROCESSING,
    'excursion_transition': EventPhase.EXCURSION,
    'archive_changed': EventPhase.ARCHIVE,
    'active_pool_changed': EventPhase.ACTIVE_POOL,
    'feedback_observed': EventPhase.FEEDBACK,
    'feedback_reset': EventPhase.FEEDBACK,
    'budget_decision': EventPhase.BUDGET,
    'progress_report': EventPhase.BUDGET,
    'checkpoint_saved': EventPhase.CHECKPOINT,
    'component_failure': EventPhase.FAILURE,
    'invocation_stopped': EventPhase.INVOCATION,
}
_EXTENSION_EVENT_PATTERN = re.compile(
    r'extension\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\Z', re.ASCII
)


def _freeze_payload_value(value: object) -> object:
    """Copy nested containers into immutable event-payload values."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError('event payload keys must be strings')
        return MappingProxyType(
            {key: _freeze_payload_value(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_payload_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_payload_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class AlgorithmEvent:
    """One ordered, immutable envelope in an invocation's event stream.

    Event-specific payload fields are defined by the event schemas in the
    architecture design. This record validates the common envelope.
    """

    schema_version: EventSchemaVersion
    invocation_id: InvocationId
    sequence_number: int
    commit_sequence: int | None
    event_type: EventType
    phase: EventPhase
    source_id: ComponentId | None
    choice_id: ChoiceId | None
    solution_id: SolutionId | None
    data: EventPayload

    def __post_init__(self) -> None:
        require_integer(self.schema_version, name='schema_version', minimum=1)
        if not isinstance(self.invocation_id, InvocationId):
            raise TypeError('invocation_id must be an InvocationId')
        require_integer(self.sequence_number, name='sequence_number', minimum=0)
        if self.commit_sequence is not None:
            require_integer(self.commit_sequence, name='commit_sequence', minimum=0)
        if not isinstance(self.event_type, str) or not self.event_type:
            raise ValueError('event_type must be a nonempty string')
        if not isinstance(self.phase, EventPhase):
            raise TypeError('phase must be an EventPhase')
        standard_phase = _STANDARD_EVENT_PHASES.get(self.event_type)
        if standard_phase is not None:
            if self.phase is not standard_phase:
                raise ValueError('standard event phase does not match its event type')
        elif not _EXTENSION_EVENT_PATTERN.fullmatch(self.event_type):
            raise ValueError('event_type is unknown or has an invalid extension name')
        elif self.phase is not EventPhase.EXTENSION:
            raise ValueError('extension events use the extension phase')
        if self.source_id is not None:
            identifier_text(self.source_id)
        if self.choice_id is not None and not isinstance(self.choice_id, ChoiceId):
            raise TypeError('choice_id must be a ChoiceId or None')
        if self.solution_id is not None and not isinstance(
            self.solution_id, SolutionId
        ):
            raise TypeError('solution_id must be a SolutionId or None')
        if not isinstance(self.data, Mapping):
            raise TypeError('data must be a mapping')
        frozen_data = _freeze_payload_value(self.data)
        if not isinstance(frozen_data, Mapping):
            raise TypeError('data must be a mapping')
        object.__setattr__(self, 'data', frozen_data)


class EventObserver(ComponentIdentity, Protocol):
    """Component that receives committed event snapshots in sequence order."""

    def on_event(self, event: AlgorithmEvent) -> None:
        """Receive one immutable event after its event batch is appended."""


__all__ = [
    'AlgorithmEvent',
    'EventObserver',
    'EventPayload',
    'EventPhase',
    'EventSchemaVersion',
    'EventType',
]
