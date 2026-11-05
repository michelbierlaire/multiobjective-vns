"""Tests for post-commit observer delivery and failure handling.

Created: 2026-10-29T14:20:28+00:00
Author: Michel Bierlaire, EPFL
"""

from uuid import UUID

import pytest

from multiobjective_vns.event_observers import ObserverDispatcher
from multiobjective_vns.events import AlgorithmEvent, EventPhase
from multiobjective_vns.identifiers import InvocationId


class RecordingObserver:
    def __init__(self, component_id: str) -> None:
        self.component_id = component_id
        self.events: list[AlgorithmEvent] = []

    def on_event(self, event: AlgorithmEvent) -> None:
        self.events.append(event)


class FailingObserver:
    component_id = 'failing-observer'

    def __init__(self) -> None:
        self.calls = 0

    def on_event(self, event: AlgorithmEvent) -> None:
        del event
        self.calls += 1
        raise RuntimeError('observer failed')


def make_event(sequence_number: int) -> AlgorithmEvent:
    return AlgorithmEvent(
        schema_version=1,
        invocation_id=InvocationId(UUID(int=20)),
        sequence_number=sequence_number,
        commit_sequence=None,
        event_type='invocation_started',
        phase=EventPhase.INVOCATION,
        source_id=None,
        choice_id=None,
        solution_id=None,
        data={},
    )


def test_observer_failure_is_returned_and_disables_that_observer() -> None:
    failing = FailingObserver()
    recording = RecordingObserver('recording-observer')
    dispatcher = ObserverDispatcher(
        (failing, recording),
    )

    failures = dispatcher.deliver((make_event(0), make_event(1)))

    assert len(failures) == 1
    assert failures[0].source == failing.component_id
    assert failures[0].phase == 'event_delivery'
    assert failing.calls == 1
    assert [event.sequence_number for event in recording.events] == [0, 1]
    assert dispatcher.deliver((make_event(2),)) == ()
    assert [event.sequence_number for event in recording.events] == [0, 1, 2]


def test_observer_identifiers_must_be_unique() -> None:
    first = RecordingObserver('same')
    second = RecordingObserver('same')

    with pytest.raises(ValueError, match='unique'):
        ObserverDispatcher((first, second))


def test_observer_batches_must_arrive_in_sequence_order() -> None:
    observer = RecordingObserver('observer')
    dispatcher = ObserverDispatcher((observer,))

    with pytest.raises(ValueError, match='contiguous'):
        dispatcher.deliver((make_event(0), make_event(2)))
