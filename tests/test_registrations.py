"""Tests for operator registration contracts.

Created: 2026-10-29T13:18:36+00:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.exceptions import ComponentRegistrationError
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.registrations import OperatorGroup, OperatorRegistration


class TestRandomSource:
    component_id = 'random'
    configuration_id = 'random-v1'

    def random(self) -> float:
        return 0.5

    def capture_state(self) -> object:
        return 0.5

    def restore_state(self, payload: object) -> None:
        assert payload == 0.5


class TestOperator:
    def call(self, context: CandidateContext) -> OperatorResult:
        return OperatorResult(m=0, candidate=None)


class StatefulTestOperator(TestOperator):
    def capture_state(self, state: object) -> object:
        return state

    def restore_state(self, payload: object) -> object:
        return payload


class FailingIdentifier:
    def __hash__(self) -> int:
        return 1

    def __eq__(self, other: object) -> bool:
        return self is other

    def to_log_text(self) -> str:
        raise RuntimeError('cannot serialize this ID')


def test_operator_registration_exposes_declared_contract() -> None:
    registration = OperatorRegistration(
        operator_id='swap',
        configuration_id='swap-v1',
        operator=TestOperator(),
        initial_state=None,
        max_neighborhood_size=5,
        feasibility_restoration=False,
        modification_count_definition='number of changed decisions',
    )
    group = OperatorGroup('local', frozenset({'swap', 'repair'}))

    source: RandomSource = TestRandomSource()
    assert source.random() == 0.5
    assert registration.max_neighborhood_size == 5
    assert group.operator_ids == frozenset({'swap', 'repair'})


def test_invalid_operator_registration_uses_named_error() -> None:
    with pytest.raises(ComponentRegistrationError):
        OperatorRegistration(
            operator_id='swap',
            configuration_id='swap-v1',
            operator=TestOperator(),
            initial_state=None,
            max_neighborhood_size=0,
            feasibility_restoration=False,
            modification_count_definition='changed decisions',
        )


def test_stateful_operator_requires_state_lifecycle_methods() -> None:
    with pytest.raises(ComponentRegistrationError):
        OperatorRegistration(
            operator_id='stateful',
            configuration_id='stateful-v1',
            operator=TestOperator(),
            initial_state={'count': 0},
            max_neighborhood_size=3,
            feasibility_restoration=False,
            modification_count_definition='number of changed decisions',
        )

    registration = OperatorRegistration(
        operator_id='stateful',
        configuration_id='stateful-v1',
        operator=StatefulTestOperator(),
        initial_state={'count': 0},
        max_neighborhood_size=3,
        feasibility_restoration=False,
        modification_count_definition='number of changed decisions',
    )
    assert registration.initial_state == {'count': 0}


def test_identifier_serialization_errors_are_registration_errors() -> None:
    with pytest.raises(ComponentRegistrationError):
        OperatorRegistration(
            operator_id=FailingIdentifier(),
            configuration_id='failing-v1',
            operator=TestOperator(),
            initial_state=None,
            max_neighborhood_size=2,
            feasibility_restoration=False,
            modification_count_definition='changed decisions',
        )
