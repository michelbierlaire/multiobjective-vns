"""Tests for objective ordering contracts.

Created: 2026-10-29T13:18:36+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass

import pytest

from multiobjective_vns.exceptions import ComponentRegistrationError
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
    ObjectiveFunction,
)


@dataclass(frozen=True)
class NumberObjective:
    objective_id: str
    component_id: str
    configuration_id: str

    def compare(self, left: object, right: object) -> ObjectiveComparison:
        if left < right:  # type: ignore[operator]
            return ObjectiveComparison.LESS
        if left > right:  # type: ignore[operator]
            return ObjectiveComparison.GREATER
        return ObjectiveComparison.EQUAL

    def format(self, value: object) -> str:
        return str(value)

    def __str__(self) -> str:
        return self.objective_id


def test_objective_declaration_preserves_order() -> None:
    first = NumberObjective('cost', 'cost-component', 'v1')
    second = NumberObjective('risk', 'risk-component', 'v1')
    declaration = ObjectiveDeclaration((first, second))

    assert declaration.objectives == (first, second)


def test_objective_declaration_normalizes_and_validates_upper_bounds() -> None:
    objective = NumberObjective('cost', 'cost-component', 'v1')

    unbounded = ObjectiveDeclaration((objective,))
    assert unbounded.upper_bounds == (None,)

    bounded = ObjectiveDeclaration((objective,), upper_bounds=(10,))
    assert bounded.upper_bounds == (10,)

    with pytest.raises(ComponentRegistrationError, match='length'):
        ObjectiveDeclaration((objective,), upper_bounds=())
    with pytest.raises(ComponentRegistrationError, match='tuple'):
        ObjectiveDeclaration((objective,), upper_bounds=[10])  # type: ignore[arg-type]


def test_objective_comparison_has_minimization_order_values() -> None:
    objective = NumberObjective('cost', 'cost-component', 'v1')

    assert objective.compare(1, 2) is ObjectiveComparison.LESS
    assert objective.compare(2, 2) is ObjectiveComparison.EQUAL
    assert objective.compare(3, 2) is ObjectiveComparison.GREATER


def test_objective_declaration_requires_two_unique_objectives() -> None:
    objective = NumberObjective('cost', 'cost-component', 'v1')

    assert ObjectiveDeclaration((objective,)).objectives == (objective,)
    with pytest.raises(ComponentRegistrationError):
        ObjectiveDeclaration((objective, objective))


def test_objective_declaration_rejects_an_empty_vector() -> None:
    with pytest.raises(ComponentRegistrationError):
        ObjectiveDeclaration(())


def test_objective_function_is_a_structural_contract() -> None:
    objective: ObjectiveFunction = NumberObjective('cost', 'c', 'v1')
    assert objective.format(8) == '8'
