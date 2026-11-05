"""Tests for atomic SearchState publication with event batches.

Created: 2026-10-29T14:07:42+00:00
Author: Michel Bierlaire, EPFL
"""

from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from multiobjective_vns.event_stream import EventDraft, EventJournal
from multiobjective_vns.events import AlgorithmEvent, EventObserver, EventPhase
from multiobjective_vns.identifiers import InvocationId, SolutionId
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateOutcome
from multiobjective_vns.records.policies import InvocationCounters
from multiobjective_vns.records.results import LogFinalizationResult, RunLog
from multiobjective_vns.records.state import SearchState
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    ProcessingStatus,
    SubmissionSource,
    TerminationReason,
)
from multiobjective_vns.services import RunLogger, SolutionFormatter
from multiobjective_vns.transitions import SearchCommitCoordinator


class State:
    def __init__(self, label: str) -> None:
        self.label = label

    def pareto_solutions(self) -> tuple[object, ...]:
        return ()

    def active_solutions(self) -> tuple[object, ...]:
        return ()

    def visited_solutions(self) -> tuple[object, ...]:
        return ()


class RecordingLogger:
    component_id = 'test-logger'

    def __init__(self) -> None:
        self.fail_next_append = False

    def begin(self, invocation_id: InvocationId, log_directory: Path) -> None:
        del invocation_id, log_directory

    def append(self, events: tuple[AlgorithmEvent, ...]) -> None:
        del events
        if self.fail_next_append:
            self.fail_next_append = False
            raise OSError('append failed')

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


class StateCheckingObserver:
    component_id = 'state-checking-observer'

    def __init__(self) -> None:
        self.coordinator: SearchCommitCoordinator | None = None
        self.observed_state: SearchState | None = None

    def on_event(self, event: AlgorithmEvent) -> None:
        if event.commit_sequence is not None:
            assert self.coordinator is not None
            self.observed_state = self.coordinator.search_state


class FailingObserver:
    component_id = 'failing-observer'

    def __init__(self) -> None:
        self.calls = 0

    def on_event(self, event: AlgorithmEvent) -> None:
        self.calls += 1
        if event.commit_sequence is not None:
            raise RuntimeError('observer failed')


def make_outcome() -> CandidateOutcome:
    return CandidateOutcome(
        source=SubmissionSource.INITIAL,
        submitted_solution=('candidate',),
        processing_status=ProcessingStatus.COMPLETED,
        solution_id=SolutionId(0),
        assessment=ApplicationAssessment.VALID,
    )


def make_coordinator(
    logger: RecordingLogger,
) -> SearchCommitCoordinator:
    journal = EventJournal(InvocationId(UUID(int=8)), cast(RunLogger, logger))
    coordinator = SearchCommitCoordinator(cast(SearchState, State('old')), journal)
    coordinator.append_invocation_events(
        (
            EventDraft(
                event_type='invocation_started',
                phase=EventPhase.INVOCATION,
                data={},
            ),
        )
    )
    return coordinator


def test_commit_publishes_state_outcomes_and_sequence_after_event_append() -> None:
    coordinator = make_coordinator(RecordingLogger())
    replacement = State('new')
    event = EventDraft(
        event_type='candidate_processed',
        phase=EventPhase.CANDIDATE_PROCESSING,
        data={},
    )

    receipt = coordinator.commit(
        cast(SearchState, replacement), (event,), (make_outcome(),)
    )

    assert coordinator.search_state is cast(object, replacement)
    assert coordinator.commit_sequence == 1
    assert coordinator.candidate_outcomes == (make_outcome(),)
    assert receipt.commit_sequence == 1
    assert receipt.events[0].commit_sequence == 1
    assert coordinator.run_log.events[1:] == receipt.events


def test_failed_append_rolls_back_state_sequence_and_outcomes() -> None:
    logger = RecordingLogger()
    coordinator = make_coordinator(logger)
    old_state = coordinator.search_state
    logger.fail_next_append = True
    event = EventDraft(
        event_type='candidate_processed',
        phase=EventPhase.CANDIDATE_PROCESSING,
        data={},
    )

    with pytest.raises(OSError, match='append failed'):
        coordinator.commit(
            cast(SearchState, State('unpublished')),
            (event,),
            (make_outcome(),),
        )

    assert coordinator.search_state is old_state
    assert coordinator.commit_sequence == 0
    assert coordinator.candidate_outcomes == ()
    assert len(coordinator.run_log.events) == 1
    assert coordinator.run_log.events[0].event_type == 'invocation_started'


def test_invocation_events_do_not_advance_search_commit_sequence() -> None:
    coordinator = make_coordinator(RecordingLogger())
    draft = EventDraft(
        event_type='progress_report',
        phase=EventPhase.BUDGET,
        data={},
    )

    coordinator.append_invocation_events((draft,))

    assert coordinator.commit_sequence == 0
    assert coordinator.run_log.events[1].commit_sequence is None


def test_observers_receive_commit_events_after_state_publication() -> None:
    observer = StateCheckingObserver()
    journal = EventJournal(
        InvocationId(UUID(int=9)),
        cast(RunLogger, RecordingLogger()),
    )
    coordinator = SearchCommitCoordinator(
        cast(SearchState, State('old')),
        journal,
        observers=(cast(EventObserver, observer),),
    )
    coordinator.append_invocation_events(
        (
            EventDraft(
                event_type='invocation_started',
                phase=EventPhase.INVOCATION,
                data={},
            ),
        )
    )
    observer.coordinator = coordinator
    replacement = State('new')

    coordinator.commit(
        cast(SearchState, replacement),
        (
            EventDraft(
                event_type='candidate_processed',
                phase=EventPhase.CANDIDATE_PROCESSING,
                data={},
            ),
        ),
    )

    assert observer.observed_state is cast(object, replacement)


def test_observer_failure_is_logged_after_commit_without_rollback() -> None:
    observer = FailingObserver()
    journal = EventJournal(
        InvocationId(UUID(int=11)), cast(RunLogger, RecordingLogger())
    )
    coordinator = SearchCommitCoordinator(
        cast(SearchState, State('old')),
        journal,
        observers=(cast(EventObserver, observer),),
    )
    coordinator.append_invocation_events(
        (
            EventDraft(
                event_type='invocation_started',
                phase=EventPhase.INVOCATION,
                data={},
            ),
        )
    )
    replacement = State('new')

    coordinator.commit(
        cast(SearchState, replacement),
        (
            EventDraft(
                event_type='candidate_processed',
                phase=EventPhase.CANDIDATE_PROCESSING,
                data={},
            ),
        ),
    )

    assert coordinator.search_state is cast(object, replacement)
    assert coordinator.commit_sequence == 1
    assert observer.calls == 2
    assert [event.event_type for event in coordinator.run_log.events] == [
        'invocation_started',
        'candidate_processed',
        'component_failure',
    ]
    assert len(coordinator.diagnostics) == 1


def test_commit_requires_events_and_matching_candidate_outcomes() -> None:
    coordinator = make_coordinator(RecordingLogger())

    with pytest.raises(ValueError, match='at least one'):
        coordinator.commit(cast(SearchState, State('new')), ())

    with pytest.raises(TypeError, match='CandidateOutcome'):
        coordinator.commit(
            cast(SearchState, State('new')),
            (
                EventDraft(
                    event_type='candidate_processed',
                    phase=EventPhase.CANDIDATE_PROCESSING,
                    data={},
                ),
            ),
            cast(tuple[CandidateOutcome, ...], (object(),)),
        )
