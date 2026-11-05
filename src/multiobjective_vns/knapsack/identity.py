"""Exact tuple identity for multiobjective knapsack solutions.

Created: 2026-10-29T17:44:53+00:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.callbacks import LookupKey, Solution
from multiobjective_vns.identifiers import ComponentConfigurationId, ComponentId


class KnapsackSolutionIdentity:
    """Identify vectors by length and type-sensitive equality of each entry."""

    component_id: ComponentId = 'multiobjective-vns:knapsack:solution-identity'
    configuration_id: ComponentConfigurationId = 'knapsack-tuple-identity-v1'

    def equivalent(self, left: Solution, right: Solution) -> bool:
        """Compare two integer tuples, distinguishing Boolean and integer entries.

        :param left: First immutable vector.
        :param right: Second immutable vector.
        :returns: Whether the tuples have equal lengths and typed entries.
        :raises TypeError: If either value is not an integer tuple.
        """
        left_values = self._require_integer_tuple(left)
        right_values = self._require_integer_tuple(right)
        return len(left_values) == len(right_values) and all(
            type(left_value) is type(right_value) and left_value == right_value
            for left_value, right_value in zip(left_values, right_values, strict=True)
        )

    def lookup_key(self, solution: Solution) -> LookupKey:
        """Return the immutable tuple as a deterministic lookup hint.

        :param solution: Immutable vector, including a wrong-length or nonbinary one.
        :returns: Hashable tuple used only to narrow identity lookup.
        :raises TypeError: If the value is not an integer tuple.
        """
        return self._require_integer_tuple(solution)

    @staticmethod
    def _require_integer_tuple(value: object) -> tuple[int, ...]:
        if not isinstance(value, tuple):
            raise TypeError('knapsack solutions must be tuples')
        if any(type(item) not in (int, bool) for item in value):
            raise TypeError('knapsack tuple entries must be integers')
        return value
