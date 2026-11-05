"""Tests for shared identifier contracts.

Created: 2026-10-29T13:18:36+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass
from uuid import UUID

import pytest

from multiobjective_vns.identifiers import (
    ChoiceId,
    ChoiceKind,
    CompositionId,
    IdentifierValue,
    InvocationId,
    SolutionId,
    identifier_text,
)


@dataclass(frozen=True)
class ApplicationId:
    value: int

    def to_log_text(self) -> str:
        return f'item-{self.value}'


def test_identifier_text_accepts_strings_and_application_ids() -> None:
    assert identifier_text('route-a') == 'route-a'
    assert identifier_text(ApplicationId(4)) == 'item-4'


@pytest.mark.parametrize('value', ['', 3, object()])
def test_identifier_text_rejects_invalid_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        identifier_text(value)  # type: ignore[arg-type]


def test_package_ids_have_stable_text_forms() -> None:
    assert SolutionId(12).to_log_text() == 'solution:12'
    invocation_id = InvocationId(UUID('12345678-1234-5678-1234-567812345678'))
    assert invocation_id.to_log_text() == '12345678123456781234567812345678'


def test_package_ids_reject_malformed_values() -> None:
    with pytest.raises(ValueError):
        SolutionId(-1)
    with pytest.raises(TypeError):
        SolutionId(True)
    with pytest.raises(TypeError):
        InvocationId('not-a-uuid')  # type: ignore[arg-type]


def test_choice_id_uses_a_tagged_collision_free_shape() -> None:
    operator_choice = ChoiceId.for_operator('move')
    composition_choice = ChoiceId.for_composition(CompositionId('move', 'repair'))

    assert operator_choice.to_json_value() == {
        'kind': 'operator',
        'operator_id': 'move',
    }
    assert composition_choice.to_json_value() == {
        'kind': 'composition',
        'first_operator_id': 'move',
        'second_operator_id': 'repair',
    }
    assert operator_choice != composition_choice


def test_choice_id_rejects_an_inconsistent_tagged_value() -> None:
    with pytest.raises(TypeError):
        ChoiceId.for_operator(None)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        ChoiceId(
            kind=ChoiceKind.COMPOSITION,
            composition_id=object(),  # type: ignore[arg-type]
        )


def test_custom_identifier_contract_is_structural() -> None:
    value: IdentifierValue = ApplicationId(1)
    assert identifier_text(value) == 'item-1'
