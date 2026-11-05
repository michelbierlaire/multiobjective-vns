"""Immutable records for extension transition events and schemas.

Created: 2026-10-29T13:47:55+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Literal, TypeAlias

from multiobjective_vns.events import AlgorithmEvent
from multiobjective_vns.records.candidates import CandidateOutcome
from multiobjective_vns.values import require_integer

JSONValue: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | list['JSONValue']
    | tuple['JSONValue', ...]
    | Mapping[str, 'JSONValue']
)
ExtensionNamespace: TypeAlias = str
ExtensionEventName: TypeAlias = str
ExtensionCommitKind: TypeAlias = Literal[
    'initial_submission', 'candidate_attempt', 'maintenance_action'
]
_EXTENSION_NAME_PATTERN = re.compile(r'[a-z][a-z0-9_]*\Z', re.ASCII)


def _freeze_json(value: object) -> JSONValue:
    """Validate and freeze one JSON-compatible value."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError('JSON numbers must be finite')
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError('JSON object keys must be strings')
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(f'{type(value).__name__} is not a JSON value')


def _freeze_json_object(value: Mapping[str, JSONValue]) -> Mapping[str, JSONValue]:
    """Validate and freeze a JSON object."""
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise TypeError('value must be a JSON object')
    return frozen


@dataclass(frozen=True, slots=True)
class ExtensionEventSchema:
    """Versioned JSON object schema for one extension event name."""

    event_name: ExtensionEventName
    schema_version: int
    payload_schema: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        if not isinstance(
            self.event_name, str
        ) or not _EXTENSION_NAME_PATTERN.fullmatch(self.event_name):
            raise ValueError('event_name must be a lowercase ASCII name')
        require_integer(self.schema_version, name='schema_version', minimum=1)
        if not isinstance(self.payload_schema, Mapping):
            raise TypeError('payload_schema must be a mapping')
        if self.payload_schema.get('type') != 'object':
            raise ValueError('payload_schema must describe a JSON object schema')
        object.__setattr__(
            self, 'payload_schema', _freeze_json_object(self.payload_schema)
        )


@dataclass(frozen=True, slots=True)
class ExtensionEvent:
    """One namespaced event emitted for a committed transition."""

    event_name: ExtensionEventName
    payload: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        if not isinstance(
            self.event_name, str
        ) or not _EXTENSION_NAME_PATTERN.fullmatch(self.event_name):
            raise ValueError('event_name must be a lowercase ASCII name')
        if not isinstance(self.payload, Mapping):
            raise TypeError('payload must be a mapping')
        object.__setattr__(self, 'payload', _freeze_json_object(self.payload))


@dataclass(frozen=True, slots=True)
class SearchExtensionRequest:
    """Read-only event and outcome snapshot for one pending state commit."""

    commit_sequence: int
    commit_kind: ExtensionCommitKind
    standard_events: tuple[AlgorithmEvent, ...]
    candidate_outcomes: tuple[CandidateOutcome, ...]

    def __post_init__(self) -> None:
        require_integer(self.commit_sequence, name='commit_sequence', minimum=0)
        if self.commit_kind not in (
            'initial_submission',
            'candidate_attempt',
            'maintenance_action',
        ):
            raise ValueError('commit_kind must name a supported transition')
        if not isinstance(self.standard_events, tuple) or any(
            not isinstance(event, AlgorithmEvent) for event in self.standard_events
        ):
            raise TypeError('standard_events must be a tuple of AlgorithmEvent')
        if not isinstance(self.candidate_outcomes, tuple) or any(
            not isinstance(outcome, CandidateOutcome)
            for outcome in self.candidate_outcomes
        ):
            raise TypeError('candidate_outcomes must be a tuple of CandidateOutcome')


@dataclass(frozen=True, slots=True)
class ExtensionCommitResult:
    """Events returned by an extension for one pending transition."""

    events: tuple[ExtensionEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, ExtensionEvent) for event in self.events
        ):
            raise TypeError('events must be a tuple of ExtensionEvent records')
