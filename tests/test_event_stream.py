"""Tests for ordered event batching and commit association.

Created: 2026-10-29T14:07:42+00:00
Author: Michel Bierlaire, EPFL
"""

from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from multiobjective_vns.event_stream import EventDraft, EventJournal
from multiobjective_vns.events import AlgorithmEvent, EventPhase
from multiobjective_vns.identifiers import InvocationId
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.policies import InvocationCounters
from multiobjective_vns.records.results import LogFinalizationResult, RunLog
from multiobjective_vns.records.state import SearchState
from multiobjective_vns.records.status import TerminationReason
from multiobjective_vns.services import RunLogger, SolutionFormatter


class RecordingLogger:
    component_id = 'test-logger'

    def __init__(self) -> None:
        self.batches: list[tuple[AlgorithmEvent, ...]] = []
        self.fail_next_append = False

    def begin(self, invocation_id: InvocationId, log_directory: Path) -> None:
        del invocation_id, log_directory

    def append(self, events: tuple[AlgorithmEvent, ...]) -> None:
        if self.fail_next_append:
            self.fail_next_append = False
            raise OSError('append failed')
        self.batches.append(events)

    def finalize(
        self,
        run_log: RunLog,
        initial_archive_ids: tuple[object, ...],
        final_state: SearchState,
        objective_declaration: ObjectiveDeclaration,
        termination_reason: TerminationReason,
        counters: InvocationCounters,
        feedback_selection_mode: object,
        promoted_compositions: tuple[object, ...],
        solution_formatter: SolutionFormatter | None,
    ) -> LogFinalizationResult:
        del (
            run_log,
            initial_archive_ids,
            final_state,
            objective_declaration,
            termination_reason,
            counters,
            feedback_selection_mode,
            promoted_compositions,
            solution_formatter,
        )
        raise NotImplementedError


def draft(
    event_type: str = 'invocation_started',
    phase: EventPhase = EventPhase.INVOCATION,
    data: Mapping[str, object] | None = None,
) -> EventDraft:
    return EventDraft(event_type=event_type, phase=phase, data=data or {})


def test_event_journal_assigns_contiguous_sequence_and_commit_numbers() -> None:
    invocation_id = InvocationId(UUID(int=3))
    logger = RecordingLogger()
    journal = EventJournal(invocation_id, cast(RunLogger, logger))

    invocation_events = journal.append_invocation_batch((draft(),))
    commit_events = journal.prepare_batch(
        (draft('candidate_processed', EventPhase.CANDIDATE_PROCESSING),), 4
    )
    journal.append_prepared_batch(commit_events)

    assert [event.sequence_number for event in journal.run_log.events] == [0, 1]
    assert invocation_events[0].commit_sequence is None
    assert commit_events[0].commit_sequence == 4
    assert logger.batches == [invocation_events, commit_events]


def test_event_draft_uses_the_architecture_schema_version() -> None:
    assert draft().schema_version == 4


def test_choice_category_selection_is_an_invocation_event() -> None:
    logger = RecordingLogger()
    journal = EventJournal(InvocationId(UUID(int=5)), cast(RunLogger, logger))
    journal.append_invocation_batch((draft(),))

    events = journal.append_invocation_batch(
        (
            draft(
                'choice_category_selected',
                EventPhase.CHOICE_SELECTION,
                {'category': 'composition'},
            ),
        )
    )

    assert len(events) == 1
    assert events[0].commit_sequence is None


def test_failed_logger_append_leaves_event_stream_unchanged() -> None:
    logger = RecordingLogger()
    journal = EventJournal(InvocationId(UUID(int=4)), cast(RunLogger, logger))
    logger.fail_next_append = True

    prepared = journal.prepare_batch((draft(),), None)
    with pytest.raises(OSError, match='append failed'):
        journal.append_prepared_batch(prepared)

    assert journal.run_log.events == ()
    event = journal.append_invocation_batch((draft(),))[0]
    assert event.sequence_number == 0


def test_event_commit_scope_must_match_the_event_contract() -> None:
    journal = EventJournal(
        InvocationId(UUID(int=10)), cast(RunLogger, RecordingLogger())
    )

    with pytest.raises(ValueError, match='requires a SearchCommit'):
        journal.prepare_batch(
            (draft('candidate_processed', EventPhase.CANDIDATE_PROCESSING),), None
        )
    with pytest.raises(ValueError, match='invocation-level'):
        journal.prepare_batch((draft(),), 1)


def test_removed_episode_and_size_stage_events_are_rejected() -> None:
    journal = EventJournal(
        InvocationId(UUID(int=21)), cast(RunLogger, RecordingLogger())
    )

    for event_type in ('base_search_ended', 'neighborhood_stage_ended'):
        with pytest.raises(ValueError, match='unsupported event type'):
            journal.prepare_batch(
                (draft(event_type, EventPhase.CHOICE_SELECTION),), None
            )


def test_composition_promotion_is_part_of_a_search_commit() -> None:
    journal = EventJournal(
        InvocationId(UUID(int=19)), cast(RunLogger, RecordingLogger())
    )

    event = journal.prepare_batch(
        (draft('composition_promoted', EventPhase.COMPOSITION),), 1
    )[0]

    assert event.commit_sequence == 1


def test_invocation_started_is_the_first_and_only_start_event() -> None:
    journal = EventJournal(
        InvocationId(UUID(int=12)), cast(RunLogger, RecordingLogger())
    )

    with pytest.raises(ValueError, match='first event'):
        journal.append_invocation_batch((draft('progress_report', EventPhase.BUDGET),))
    journal.append_invocation_batch((draft(),))
    with pytest.raises(ValueError, match='only once'):
        journal.append_invocation_batch((draft(),))


def test_event_draft_payload_is_a_snapshot() -> None:
    payload = {'details': {'count': 1}}
    event_draft = draft(data=payload)
    payload['details']['count'] = 2
    event = EventJournal(
        InvocationId(UUID(int=7)), cast(RunLogger, RecordingLogger())
    ).append_invocation_batch((event_draft,))[0]

    assert event.data['details'] == {'count': 1}
