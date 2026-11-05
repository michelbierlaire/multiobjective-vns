"""Sequence and atomically append event batches for an invocation.

Created: 2026-10-29T14:07:42+00:00
Author: Michel Bierlaire, EPFL
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from multiobjective_vns.events import (
    AlgorithmEvent,
    EventPayload,
    EventPhase,
    EventSchemaVersion,
    EventType,
)
from multiobjective_vns.identifiers import (
    ChoiceId,
    ComponentId,
    InvocationId,
    SolutionId,
    identifier_text,
)
from multiobjective_vns.records.results import RunLog
from multiobjective_vns.services import RunLogger
from multiobjective_vns.values import require_integer

_INVOCATION_EVENT_TYPES = frozenset(
    {
        'invocation_started',
        'maintenance_requested',
        'initialization_completed',
        'initialization_failed',
        'base_selected',
        'choice_set_available',
        'choice_category_selected',
        'choice_selected',
        'budget_decision',
        'progress_report',
        'checkpoint_saved',
        'invocation_stopped',
    }
)
_COMMIT_EVENT_TYPES = frozenset(
    {
        'operator_result',
        'composition_result',
        'composition_promoted',
        'candidate_processed',
        'excursion_transition',
        'archive_changed',
        'active_pool_changed',
        'feedback_observed',
        'feedback_reset',
    }
)


def _freeze_value(value: object) -> object:
    """Copy nested event payload containers into immutable snapshots."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError('event payload keys must be strings')
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class EventDraft:
    """Event content before sequence numbers are assigned."""

    event_type: EventType
    phase: EventPhase
    data: EventPayload
    schema_version: EventSchemaVersion = 1
    source_id: ComponentId | None = None
    choice_id: ChoiceId | None = None
    solution_id: SolutionId | None = None

    def __post_init__(self) -> None:
        require_integer(self.schema_version, name='schema_version', minimum=1)
        if not isinstance(self.event_type, str) or not self.event_type:
            raise ValueError('event_type must be a nonempty string')
        if not isinstance(self.phase, EventPhase):
            raise TypeError('phase must be an EventPhase')
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
        frozen_data = _freeze_value(self.data)
        if not isinstance(frozen_data, Mapping):
            raise TypeError('data must be a mapping')
        object.__setattr__(self, 'data', frozen_data)


class EventJournal:
    """Append ordered batches to the RunLogger and immutable RunLog.

    The external logger receives a complete batch before the in-memory log is
    replaced. Callers publish search state after this method succeeds.

    :param invocation_id: Identifier shared by this invocation's events.
    :param logger: Atomic event-batch logger.
    """

    __slots__ = ('_invocation_id', '_logger', '_logger_id', '_run_log')

    def __init__(self, invocation_id: InvocationId, logger: RunLogger) -> None:
        if not isinstance(invocation_id, InvocationId):
            raise TypeError('invocation_id must be an InvocationId')
        logger_id = logger.component_id
        identifier_text(logger_id)
        self._invocation_id = invocation_id
        self._logger = logger
        self._logger_id = logger_id
        self._run_log = RunLog(invocation_id, ())

    @property
    def logger_id(self) -> ComponentId:
        """Return the logger identifier used for append diagnostics."""
        return self._logger_id

    @property
    def run_log(self) -> RunLog:
        """Return the current immutable event stream snapshot."""
        return self._run_log

    def prepare_batch(
        self,
        drafts: tuple[EventDraft, ...],
        commit_sequence: int | None,
    ) -> tuple[AlgorithmEvent, ...]:
        """Assign contiguous sequence numbers and validate commit association.

        :param drafts: Ordered event data for one invocation or search commit.
        :param commit_sequence: Commit number, or ``None`` for invocation
            events.
        :returns: Materialized immutable event records.
        :raises TypeError: If the draft batch is malformed.
        :raises ValueError: If the batch is empty or uses an invalid commit
            association.
        """
        if not isinstance(drafts, tuple) or any(
            not isinstance(draft, EventDraft) for draft in drafts
        ):
            raise TypeError('drafts must be a tuple of EventDraft values')
        if not drafts:
            raise ValueError('event batches must contain at least one draft')
        if commit_sequence is not None:
            require_integer(commit_sequence, name='commit_sequence', minimum=1)
        for draft in drafts:
            if draft.event_type in _INVOCATION_EVENT_TYPES:
                if commit_sequence is not None:
                    raise ValueError(
                        'invocation-level events must have no commit sequence'
                    )
            elif draft.event_type in _COMMIT_EVENT_TYPES or draft.event_type.startswith(
                'extension.'
            ):
                if commit_sequence is None:
                    raise ValueError('this event type requires a SearchCommit')
            elif draft.event_type != 'component_failure':
                raise ValueError(f'unsupported event type: {draft.event_type}')

        first_sequence = len(self._run_log.events)
        return tuple(
            AlgorithmEvent(
                schema_version=draft.schema_version,
                invocation_id=self._invocation_id,
                sequence_number=first_sequence + offset,
                commit_sequence=commit_sequence,
                event_type=draft.event_type,
                phase=draft.phase,
                source_id=draft.source_id,
                choice_id=draft.choice_id,
                solution_id=draft.solution_id,
                data=draft.data,
            )
            for offset, draft in enumerate(drafts)
        )

    def append_prepared_batch(self, events: tuple[AlgorithmEvent, ...]) -> None:
        """Append one prepared batch to both log views.

        :param events: Contiguous events returned by :meth:`prepare_batch`.
        :raises TypeError: If the batch contains non-event values.
        :raises ValueError: If events do not continue this invocation stream.
        :raises Exception: If ``RunLogger.append`` rejects the batch. The
            in-memory ``RunLog`` remains unchanged in that case.
        """
        if not isinstance(events, tuple) or not events:
            raise ValueError('events must be a nonempty tuple')
        first_sequence = len(self._run_log.events)
        for offset, event in enumerate(events):
            if not isinstance(event, AlgorithmEvent):
                raise TypeError('events must contain AlgorithmEvent values')
            if event.invocation_id != self._invocation_id:
                raise ValueError('event belongs to a different invocation')
            if event.sequence_number != first_sequence + offset:
                raise ValueError('event sequence does not continue this stream')
        start_events = tuple(
            event for event in events if event.event_type == 'invocation_started'
        )
        if not self._run_log.events:
            if events[0].event_type != 'invocation_started':
                raise ValueError('invocation_started must be the first event')
            if len(start_events) != 1:
                raise ValueError('invocation_started must appear only once')
        elif start_events:
            raise ValueError('invocation_started must appear only once')
        if len({event.commit_sequence for event in events}) != 1:
            raise ValueError('one batch must use one commit sequence')

        next_log = RunLog(self._invocation_id, (*self._run_log.events, *events))
        self._logger.append(events)
        self._run_log = next_log

    def append_invocation_batch(
        self, drafts: tuple[EventDraft, ...]
    ) -> tuple[AlgorithmEvent, ...]:
        """Prepare and append invocation events without changing search state.

        :param drafts: Ordered invocation-level event data.
        :returns: The materialized events after a successful append.
        :raises Exception: If the logger rejects the batch.
        """
        events = self.prepare_batch(drafts, None)
        self.append_prepared_batch(events)
        return events


__all__ = ['EventDraft', 'EventJournal']
