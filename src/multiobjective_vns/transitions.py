"""Atomic publication of search snapshots, outcomes, and event batches.

Created: 2026-10-29T14:07:42+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass

from multiobjective_vns.event_observers import ObserverDispatcher
from multiobjective_vns.event_stream import EventDraft, EventJournal
from multiobjective_vns.events import AlgorithmEvent, EventObserver, EventPhase
from multiobjective_vns.records.candidates import CandidateOutcome
from multiobjective_vns.records.failures import ComponentFailure, FailureKind
from multiobjective_vns.records.results import RunLog
from multiobjective_vns.records.state import SearchState
from multiobjective_vns.values import require_integer


@dataclass(frozen=True, slots=True)
class _PublishedSearchSnapshot:
    """One pointer-published unit of continuation and invocation results."""

    search_state: SearchState
    commit_sequence: int
    candidate_outcomes: tuple[CandidateOutcome, ...]


@dataclass(frozen=True, slots=True)
class SearchCommitReceipt:
    """Publicly inspectable result of one successfully published transition."""

    commit_sequence: int
    search_state: SearchState
    events: tuple[AlgorithmEvent, ...]
    candidate_outcomes: tuple[CandidateOutcome, ...]
    diagnostics: tuple[ComponentFailure, ...]


class SearchCommitCoordinator:
    """Coordinate one invocation's atomic state and event commits.

    The coordinator prepares event sequence numbers and asks the event
    journal to append a complete batch before publishing replacement state or
    outcomes. Observer delivery follows publication and cannot roll back a
    successful commit.

    :param search_state: Initial or restored immutable continuation snapshot.
    :param event_journal: Invocation event stream and batch appender.
    :param commit_sequence: Sequence of the supplied continuation snapshot.
    :param observers: Event observers in delivery order.
    :raises TypeError: If the initial state or journal is malformed.
    :raises ValueError: If the initial sequence is invalid.
    """

    __slots__ = (
        '_diagnostics',
        '_event_journal',
        '_observer_dispatcher',
        '_snapshot',
    )

    def __init__(
        self,
        search_state: SearchState,
        event_journal: EventJournal,
        *,
        commit_sequence: int = 0,
        observers: tuple[EventObserver, ...] = (),
    ) -> None:
        if any(
            not callable(getattr(search_state, method, None))
            for method in (
                'pareto_solutions',
                'active_solutions',
                'visited_solutions',
            )
        ):
            raise TypeError('search_state must satisfy the SearchState contract')
        if not isinstance(event_journal, EventJournal):
            raise TypeError('event_journal must be an EventJournal')
        require_integer(commit_sequence, name='commit_sequence', minimum=0)
        self._diagnostics: tuple[ComponentFailure, ...] = ()
        self._event_journal = event_journal
        self._observer_dispatcher = ObserverDispatcher(observers)
        self._snapshot = _PublishedSearchSnapshot(search_state, commit_sequence, ())

    @property
    def search_state(self) -> SearchState:
        """Return the latest successfully published continuation snapshot."""
        return self._snapshot.search_state

    @property
    def commit_sequence(self) -> int:
        """Return the latest successfully published search commit number."""
        return self._snapshot.commit_sequence

    @property
    def candidate_outcomes(self) -> tuple[CandidateOutcome, ...]:
        """Return candidate outcomes published by successful commits."""
        return self._snapshot.candidate_outcomes

    @property
    def diagnostics(self) -> tuple[ComponentFailure, ...]:
        """Return observer and event-reporting diagnostics for this invocation."""
        return self._diagnostics

    @property
    def run_log(self) -> RunLog:
        """Return the current immutable invocation event log."""
        return self._event_journal.run_log

    def append_invocation_events(
        self, drafts: tuple[EventDraft, ...]
    ) -> tuple[AlgorithmEvent, ...]:
        """Append invocation events, then notify observers.

        :param drafts: Ordered event records for an invocation-level action.
        :returns: Events in the explicitly requested batch.
        :raises Exception: If the logger rejects the batch.
        """
        events = self._event_journal.append_invocation_batch(drafts)
        diagnostics = self._deliver_events(events)
        self._diagnostics = (*self._diagnostics, *diagnostics)
        return events

    def commit(
        self,
        replacement_state: SearchState,
        event_drafts: tuple[EventDraft, ...],
        candidate_outcomes: tuple[CandidateOutcome, ...] = (),
    ) -> SearchCommitReceipt:
        """Append one event batch, then publish its state and outcomes.

        :param replacement_state: Fully prepared immutable continuation state.
        :param event_drafts: Standard and extension events for the transition.
        :param candidate_outcomes: Outcomes made public by this transition.
        :returns: Receipt for the published commit.
        :raises TypeError: If replacement values have malformed types.
        :raises ValueError: If an eventless commit or malformed outcome batch
            is supplied.
        :raises Exception: If the logger rejects the complete event batch; in
            that case the prior state, sequence, and outcomes remain current.
        """
        if any(
            not callable(getattr(replacement_state, method, None))
            for method in (
                'pareto_solutions',
                'active_solutions',
                'visited_solutions',
            )
        ):
            raise TypeError('replacement_state must satisfy the SearchState contract')
        if not isinstance(candidate_outcomes, tuple) or any(
            not isinstance(outcome, CandidateOutcome) for outcome in candidate_outcomes
        ):
            raise TypeError('candidate_outcomes must be a tuple of CandidateOutcome')
        if not event_drafts:
            raise ValueError('a SearchCommit requires at least one event')

        next_sequence = self._snapshot.commit_sequence + 1
        events = self._event_journal.prepare_batch(event_drafts, next_sequence)
        self._event_journal.append_prepared_batch(events)

        self._snapshot = _PublishedSearchSnapshot(
            replacement_state,
            next_sequence,
            (*self._snapshot.candidate_outcomes, *candidate_outcomes),
        )
        diagnostics = self._deliver_events(events)
        self._diagnostics = (*self._diagnostics, *diagnostics)
        return SearchCommitReceipt(
            next_sequence,
            replacement_state,
            events,
            candidate_outcomes,
            diagnostics,
        )

    def _deliver_events(
        self, events: tuple[AlgorithmEvent, ...]
    ) -> tuple[ComponentFailure, ...]:
        """Deliver committed events and append diagnostics from failed observers."""
        diagnostics: list[ComponentFailure] = []
        pending_failures = self._observer_dispatcher.deliver(events)
        while pending_failures:
            diagnostics.extend(pending_failures)
            drafts = tuple(
                self._observer_failure_draft(failure) for failure in pending_failures
            )
            try:
                failure_events = self._event_journal.prepare_batch(drafts, None)
                self._event_journal.append_prepared_batch(failure_events)
            except Exception as error:
                diagnostics.append(
                    ComponentFailure(
                        FailureKind.INFRASTRUCTURE_FAILURE,
                        self._event_journal.logger_id,
                        'run_logging',
                        f'RunLogger.append failed while recording observer failure: {error}',
                    )
                )
                break
            pending_failures = self._observer_dispatcher.deliver(failure_events)
        return tuple(diagnostics)

    def _observer_failure_draft(self, failure: ComponentFailure) -> EventDraft:
        """Create the standard invocation event for an observer failure."""
        return EventDraft(
            event_type='component_failure',
            phase=EventPhase.FAILURE,
            source_id=failure.source,
            choice_id=failure.choice_id,
            solution_id=failure.solution_id,
            data={
                'failure': failure,
                'diagnostic_origin': 'current_operation',
                'base_solution_id': None,
                'k': None,
                'component_position': None,
            },
        )


__all__ = ['SearchCommitCoordinator', 'SearchCommitReceipt']
