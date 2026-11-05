"""Tests for registered search-extension contracts.

Created: 2026-10-29T13:47:31+00:00
Author: Michel Bierlaire, EPFL
"""

from uuid import UUID

import pytest

from multiobjective_vns.events import AlgorithmEvent, EventPhase
from multiobjective_vns.exceptions import ComponentRegistrationError
from multiobjective_vns.extensions import SearchExtension, StatefulSearchExtension
from multiobjective_vns.identifiers import InvocationId
from multiobjective_vns.records.extensions import (
    ExtensionCommitResult,
    ExtensionEvent,
    ExtensionEventSchema,
    JSONValue,
    SearchExtensionRequest,
)
from multiobjective_vns.registrations import SearchExtensionRegistration


class SimpleExtension:
    def prepare_commit(self, request: SearchExtensionRequest) -> ExtensionCommitResult:
        return ExtensionCommitResult(())


class StatefulExtension(SimpleExtension):
    def capture_state(self) -> object:
        return {'calls': 0}

    def restore_state(self, payload: object) -> None:
        assert payload == {'calls': 0}


def make_event() -> AlgorithmEvent:
    return AlgorithmEvent(
        schema_version=1,
        invocation_id=InvocationId(UUID(int=1)),
        sequence_number=0,
        commit_sequence=0,
        event_type='candidate_processed',
        phase=EventPhase.CANDIDATE_PROCESSING,
        source_id=None,
        choice_id=None,
        solution_id=None,
        data={},
    )


def test_extension_records_capture_committed_transition_snapshots() -> None:
    event = make_event()
    request = SearchExtensionRequest(
        commit_sequence=0,
        commit_kind='candidate_attempt',
        standard_events=(event,),
        candidate_outcomes=(),
    )
    extension: SearchExtension = SimpleExtension()
    result = extension.prepare_commit(request)

    assert result.events == ()
    assert request.standard_events == (event,)
    with pytest.raises(ValueError, match='commit_kind'):
        SearchExtensionRequest(0, 'unsupported', (), ())  # type: ignore[arg-type]


def test_extension_event_payload_is_json_safe_and_immutable() -> None:
    payload: dict[str, JSONValue] = {'count': 2, 'nested': ['ready', None]}
    event = ExtensionEvent('candidate_seen', payload)

    assert event.payload['count'] == 2
    with pytest.raises(TypeError):
        event.payload['nested'][0] = 'changed'  # type: ignore[index]
    with pytest.raises(ValueError, match='finite'):
        ExtensionEvent('candidate_seen', {'score': float('nan')})
    with pytest.raises(ValueError, match='event_name'):
        ExtensionEvent('CandidateSeen', {})


def test_extension_schema_requires_a_versioned_object_schema() -> None:
    with pytest.raises(ValueError, match='event_name'):
        ExtensionEventSchema('CandidateSeen', 1, {'type': 'object'})
    with pytest.raises(ValueError, match='object schema'):
        ExtensionEventSchema('candidate_seen', 1, {'type': 'string'})


def test_extension_registration_checks_names_schemas_and_state_lifecycle() -> None:
    schema = ExtensionEventSchema(
        'candidate_seen',
        1,
        {'type': 'object', 'properties': {'count': {'type': 'integer'}}},
    )
    stateless: SearchExtension = SimpleExtension()
    registration = SearchExtensionRegistration(
        component_id='telemetry',
        configuration_id='telemetry-v1',
        extension=stateless,
        namespace='metrics',
        stateful=False,
        event_schemas=(schema,),
    )
    stateful: StatefulSearchExtension = StatefulExtension()

    assert registration.namespace == 'metrics'
    assert stateful.capture_state() == {'calls': 0}
    with pytest.raises(ComponentRegistrationError):
        SearchExtensionRegistration(
            component_id='telemetry',
            configuration_id='telemetry-v1',
            extension=stateless,
            namespace='Metrics',
            stateful=False,
            event_schemas=(schema,),
        )
    with pytest.raises(ComponentRegistrationError):
        SearchExtensionRegistration(
            component_id='telemetry',
            configuration_id='telemetry-v1',
            extension=stateless,
            namespace='metrics',
            stateful=True,
            event_schemas=(),
        )
