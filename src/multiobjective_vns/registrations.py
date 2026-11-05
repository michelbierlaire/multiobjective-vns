"""Immutable Operator and Operator-group registration records.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

import re
from dataclasses import dataclass

from multiobjective_vns.callbacks import Operator
from multiobjective_vns.exceptions import ComponentRegistrationError
from multiobjective_vns.extensions import SearchExtension
from multiobjective_vns.identifiers import (
    ComponentConfigurationId,
    ComponentId,
    OperatorGroupId,
    OperatorId,
    identifier_text,
)
from multiobjective_vns.records.extensions import ExtensionEventSchema
from multiobjective_vns.values import require_integer

_EXTENSION_NAME_PATTERN = re.compile(r'[a-z][a-z0-9_]*\Z', re.ASCII)


@dataclass(frozen=True, slots=True)
class OperatorRegistration:
    """Operator callback and its stable compatibility metadata.

    :param operator_id: Stable ID that also identifies the Operator component.
    :param configuration_id: Stable identity for behavior and registration data.
    :param operator: Callback implementing ``call(context)``.
    :param initial_state: Mutable Operator state, or ``None`` for stateless.
    :param max_neighborhood_size: Largest size accepted by the Operator.
    :param feasibility_restoration: Whether the Operator supports restoration.
    :param modification_count_definition: Meaning of this Operator's ``m``.
    """

    operator_id: OperatorId
    configuration_id: ComponentConfigurationId
    operator: Operator
    initial_state: object | None
    max_neighborhood_size: int
    feasibility_restoration: bool
    modification_count_definition: str

    def __post_init__(self) -> None:
        try:
            identifier_text(self.operator_id)
            identifier_text(self.configuration_id)
            if not callable(getattr(self.operator, 'call', None)):
                raise TypeError('operator must provide call(context)')
            if self.initial_state is not None:
                if not callable(getattr(self.operator, 'capture_state', None)):
                    raise TypeError('stateful Operators must provide capture_state()')
                if not callable(getattr(self.operator, 'restore_state', None)):
                    raise TypeError('stateful Operators must provide restore_state()')
            require_integer(
                self.max_neighborhood_size,
                name='max_neighborhood_size',
                minimum=1,
            )
            if not isinstance(self.feasibility_restoration, bool):
                raise TypeError('feasibility_restoration must be bool')
            if (
                not isinstance(self.modification_count_definition, str)
                or not self.modification_count_definition.strip()
            ):
                raise ValueError('modification_count_definition must be nonempty')
        except Exception as error:
            raise ComponentRegistrationError(str(error)) from error


@dataclass(frozen=True, slots=True)
class OperatorGroup:
    """User hint grouping Operators that may be promising to combine."""

    group_id: OperatorGroupId
    operator_ids: frozenset[OperatorId]

    def __post_init__(self) -> None:
        try:
            identifier_text(self.group_id)
            if not isinstance(self.operator_ids, frozenset):
                raise TypeError('operator_ids must be a frozenset')
            if len(self.operator_ids) < 2:
                raise ValueError('an OperatorGroup requires at least two Operators')
            for operator_id in self.operator_ids:
                identifier_text(operator_id)
        except Exception as error:
            raise ComponentRegistrationError(str(error)) from error


@dataclass(frozen=True, slots=True)
class SearchExtensionRegistration:
    """Search extension and its stable identity, namespace, and event schemas."""

    component_id: ComponentId
    configuration_id: ComponentConfigurationId
    extension: SearchExtension
    namespace: str
    stateful: bool
    event_schemas: tuple[ExtensionEventSchema, ...]

    def __post_init__(self) -> None:
        try:
            identifier_text(self.component_id)
            identifier_text(self.configuration_id)
            if not callable(getattr(self.extension, 'prepare_commit', None)):
                raise TypeError('extension must provide prepare_commit(request)')
            if not isinstance(
                self.namespace, str
            ) or not _EXTENSION_NAME_PATTERN.fullmatch(self.namespace):
                raise ValueError('namespace must be a lowercase ASCII name')
            if not isinstance(self.stateful, bool):
                raise TypeError('stateful must be bool')
            if self.stateful:
                if not callable(getattr(self.extension, 'capture_state', None)):
                    raise TypeError('stateful extensions must provide capture_state()')
                if not callable(getattr(self.extension, 'restore_state', None)):
                    raise TypeError('stateful extensions must provide restore_state()')
            if not isinstance(self.event_schemas, tuple) or any(
                not isinstance(schema, ExtensionEventSchema)
                for schema in self.event_schemas
            ):
                raise TypeError('event_schemas must be a tuple of schema records')
            event_names: set[str] = set()
            for schema in self.event_schemas:
                if not _EXTENSION_NAME_PATTERN.fullmatch(schema.event_name):
                    raise ValueError('event names must be lowercase ASCII names')
                if schema.event_name in event_names:
                    raise ValueError('event names must be unique within an extension')
                event_names.add(schema.event_name)
                if schema.payload_schema.get('type') != 'object':
                    raise ValueError('extension payload schemas must describe objects')
        except Exception as error:
            raise ComponentRegistrationError(str(error)) from error
