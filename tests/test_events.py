"""Tests for the versioned algorithm event envelope.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from uuid import UUID

import pytest

from multiobjective_vns.events import AlgorithmEvent, EventObserver, EventPhase
from multiobjective_vns.identifiers import InvocationId


class RecordingObserver:
    component_id = 'observer'

    def on_event(self, event: AlgorithmEvent) -> None:
        self.event = event


def make_event(**overrides: object) -> AlgorithmEvent:
    values: dict[str, object] = {
        'schema_version': 1,
        'invocation_id': InvocationId(UUID(int=1)),
        'sequence_number': 0,
        'commit_sequence': None,
        'event_type': 'invocation_started',
        'phase': EventPhase.INVOCATION,
        'source_id': None,
        'choice_id': None,
        'solution_id': None,
        'data': {'mode': 'initial'},
    }
    values.update(overrides)
    return AlgorithmEvent(**values)  # type: ignore[arg-type]


def test_event_has_a_frozen_payload_snapshot() -> None:
    data = {'mode': 'initial', 'nested': {'count': 1}}
    event = make_event(data=data)
    observer: EventObserver = RecordingObserver()

    observer.on_event(event)
    assert event.data['mode'] == 'initial'
    with pytest.raises(TypeError):
        event.data['mode'] = 'resume'  # type: ignore[index]
    with pytest.raises(TypeError):
        event.data['nested']['count'] = 2  # type: ignore[index]


def test_event_validates_schema_sequence_and_phase() -> None:
    with pytest.raises(ValueError):
        make_event(sequence_number=-1)
    with pytest.raises(ValueError):
        make_event(event_type='candidate_processed', phase=EventPhase.INVOCATION)
    with pytest.raises(ValueError):
        make_event(schema_version=0)


def test_extension_event_uses_extension_phase_and_namespace() -> None:
    event = make_event(
        event_type='extension.metrics.candidate_seen',
        phase=EventPhase.EXTENSION,
    )
    assert event.event_type == 'extension.metrics.candidate_seen'

    with pytest.raises(ValueError):
        make_event(event_type='extension.metrics.bad-name', phase=EventPhase.EXTENSION)


def test_composition_promotion_uses_composition_phase() -> None:
    event = make_event(
        event_type='composition_promoted',
        phase=EventPhase.COMPOSITION,
    )

    assert event.event_type == 'composition_promoted'


def test_choice_category_selection_uses_choice_selection_phase() -> None:
    event = make_event(
        event_type='choice_category_selected',
        phase=EventPhase.CHOICE_SELECTION,
    )

    assert event.event_type == 'choice_category_selected'


@pytest.mark.parametrize(
    'event_type', ['neighborhood_stage_ended', 'base_search_ended']
)
def test_stage_and_episode_events_are_not_part_of_the_iteration_contract(
    event_type: str,
) -> None:
    with pytest.raises(ValueError, match='unknown'):
        make_event(event_type=event_type, phase=EventPhase.CHOICE_SELECTION)
