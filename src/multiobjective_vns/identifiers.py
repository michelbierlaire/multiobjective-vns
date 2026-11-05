"""Application-provided and package-generated identifier contracts.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import json
from collections.abc import Hashable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeAlias
from uuid import UUID, uuid4

from multiobjective_vns.values import require_integer


class LoggableIdentifier(Hashable, Protocol):
    """Structural interface for an application-defined identifier."""

    def to_log_text(self) -> str:
        """Return this identifier's stable, collision-free textual form."""


IdentifierValue: TypeAlias = str | LoggableIdentifier
OperatorId: TypeAlias = IdentifierValue
OperatorGroupId: TypeAlias = IdentifierValue
ObjectiveId: TypeAlias = IdentifierValue
ComponentId: TypeAlias = IdentifierValue
ProblemInstanceId: TypeAlias = IdentifierValue
ComponentConfigurationId: TypeAlias = IdentifierValue


def identifier_text(value: IdentifierValue) -> str:
    """Validate an identifier and return its stable text form.

    :param value: Built-in string or application-defined loggable identifier.
    :returns: The nonempty text form supplied by the identifier.
    :raises TypeError: If the identifier is unhashable or returns non-text.
    :raises ValueError: If its text form is empty or it is not equality-stable.
    """
    try:
        hash(value)
    except TypeError as error:
        raise TypeError('identifier values must be hashable') from error

    equality = value == value
    if equality is not True:
        raise ValueError('identifier equality must be stable and reflexive')

    if isinstance(value, str):
        text = value
    else:
        to_log_text = getattr(value, 'to_log_text', None)
        if not callable(to_log_text):
            raise TypeError('identifier must be a string or implement to_log_text()')
        text = to_log_text()
    if not isinstance(text, str):
        raise TypeError('identifier text must be a string')
    if not text:
        raise ValueError('identifier text must not be empty')
    return text


@dataclass(frozen=True, slots=True)
class SolutionId:
    """Package-generated reference to one registered solution."""

    sequence: int

    def __post_init__(self) -> None:
        require_integer(self.sequence, name='sequence', minimum=0)

    def to_log_text(self) -> str:
        """Return the stable representation used in logs and checkpoints."""
        return f'solution:{self.sequence}'


@dataclass(frozen=True, slots=True)
class InvocationId:
    """Package-generated, filesystem-safe identifier for one invocation."""

    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError('value must be a UUID')

    @classmethod
    def new(cls) -> InvocationId:
        """Create a new globally unique invocation identifier."""
        return cls(uuid4())

    def to_log_text(self) -> str:
        """Return a filesystem-safe lowercase hexadecimal identifier."""
        return self.value.hex


@dataclass(frozen=True, slots=True)
class CompositionId:
    """Package identifier for an ordered pair of registered Operators."""

    first_operator_id: OperatorId
    second_operator_id: OperatorId

    def __post_init__(self) -> None:
        identifier_text(self.first_operator_id)
        identifier_text(self.second_operator_id)

    def to_log_text(self) -> str:
        """Serialize both component IDs without losing order or boundaries."""
        return json.dumps(
            [
                identifier_text(self.first_operator_id),
                identifier_text(self.second_operator_id),
            ],
            ensure_ascii=True,
            separators=(',', ':'),
        )


class ChoiceKind(StrEnum):
    """Tag distinguishing a standalone Operator from a composition."""

    OPERATOR = 'operator'
    COMPOSITION = 'composition'


@dataclass(frozen=True, slots=True)
class ChoiceId:
    """Tagged package identifier for a standalone or composed choice."""

    kind: ChoiceKind
    operator_id: OperatorId | None = None
    composition_id: CompositionId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ChoiceKind):
            raise TypeError('kind must be a ChoiceKind')
        if self.kind is ChoiceKind.OPERATOR:
            if self.operator_id is None:
                raise TypeError('operator choices require an operator_id')
            if self.composition_id is not None:
                raise ValueError('operator choices require exactly one operator_id')
            identifier_text(self.operator_id)
        elif self.kind is ChoiceKind.COMPOSITION:
            if self.composition_id is None:
                raise TypeError('composition choices require a composition_id')
            if self.operator_id is not None:
                raise ValueError('composition choices require a composition_id')
            if not isinstance(self.composition_id, CompositionId):
                raise TypeError('composition_id must be a CompositionId')
        else:
            raise ValueError('kind must be an available ChoiceKind')

    @classmethod
    def for_operator(cls, operator_id: OperatorId) -> ChoiceId:
        """Create the tagged identifier for one standalone Operator."""
        return cls(kind=ChoiceKind.OPERATOR, operator_id=operator_id)

    @classmethod
    def for_composition(cls, composition_id: CompositionId) -> ChoiceId:
        """Create the tagged identifier for an ordered composition."""
        return cls(kind=ChoiceKind.COMPOSITION, composition_id=composition_id)

    def to_json_value(self) -> dict[str, str]:
        """Return the stable tagged JSON object defined by the design."""
        if self.kind is ChoiceKind.OPERATOR:
            assert self.operator_id is not None
            return {
                'kind': self.kind.value,
                'operator_id': identifier_text(self.operator_id),
            }
        assert self.composition_id is not None
        return {
            'kind': self.kind.value,
            'first_operator_id': identifier_text(self.composition_id.first_operator_id),
            'second_operator_id': identifier_text(
                self.composition_id.second_operator_id
            ),
        }
