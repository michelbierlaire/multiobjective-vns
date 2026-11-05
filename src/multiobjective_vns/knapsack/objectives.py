"""Minimization objective callbacks for negated knapsack contributions.

Created: 2026-10-29T17:44:53+00:00
Author: Michel Bierlaire, EPFL
"""

from math import isfinite

from multiobjective_vns.identifiers import ComponentConfigurationId, ComponentId
from multiobjective_vns.knapsack.instance import KnapsackInstance, KnapsackObjective
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
    ObjectiveFunction,
    ObjectiveValue,
)


class KnapsackObjectiveFunction:
    """Compare and display one negated maximization objective.

    :param specification: Objective ID and display name from the input instance.
    """

    __slots__ = ('_specification', '_component_id', '_configuration_id')

    def __init__(self, specification: KnapsackObjective) -> None:
        self._specification = specification
        self._component_id = (
            f'multiobjective-vns:knapsack:objective:{specification.objective_id}'
        )
        self._configuration_id = (
            'knapsack-numeric-order-v1:'
            f'{specification.objective_id}:{specification.name}'
        )

    @property
    def objective_id(self) -> str:
        """Return the input objective's stable identifier."""
        return self._specification.objective_id

    @property
    def component_id(self) -> ComponentId:
        """Return the failure-attribution ID for this objective callback."""
        return self._component_id

    @property
    def configuration_id(self) -> ComponentConfigurationId:
        """Return the comparison and display configuration identity."""
        return self._configuration_id

    def compare(
        self, left: ObjectiveValue, right: ObjectiveValue
    ) -> ObjectiveComparison:
        """Compare finite numeric values under package minimization.

        :param left: First negated profit value.
        :param right: Second negated profit value.
        :returns: The ascending numeric comparison result.
        :raises TypeError: If either value is Boolean or nonnumeric.
        :raises ValueError: If either numeric value is nonfinite.
        """
        left_value = self._require_finite_number(left)
        right_value = self._require_finite_number(right)
        if left_value < right_value:
            return ObjectiveComparison.LESS
        if left_value > right_value:
            return ObjectiveComparison.GREATER
        return ObjectiveComparison.EQUAL

    def format(self, value: ObjectiveValue) -> str:
        """Render a finite numeric objective value with round-trip precision.

        :param value: Negated profit value.
        :returns: Human-readable decimal representation.
        :raises TypeError: If the value is Boolean or nonnumeric.
        :raises ValueError: If the value is nonfinite.
        """
        return format(self._require_finite_number(value), '.17g')

    def __str__(self) -> str:
        """Return the objective name from the problem file."""
        return self._specification.name

    @staticmethod
    def _require_finite_number(value: object) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError('objective values must be int or float, excluding bool')
        if isinstance(value, float) and not isfinite(value):
            raise ValueError('objective values must be finite')
        return value


def build_objective_declaration(instance: KnapsackInstance) -> ObjectiveDeclaration:
    """Build the ordered core declaration for a knapsack instance.

    :param instance: Validated instance whose objective order is authoritative.
    :returns: Core declaration with one callback per input objective.
    """
    objectives: tuple[ObjectiveFunction, ...] = tuple(
        KnapsackObjectiveFunction(specification)
        for specification in instance.objectives
    )
    return ObjectiveDeclaration(objectives)
