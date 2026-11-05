"""Checkpoint codec for immutable knapsack solution tuples.

Created: 2026-10-29T17:44:53+00:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.callbacks import CodecPayload, Solution
from multiobjective_vns.identifiers import ComponentConfigurationId, ComponentId


class KnapsackSolutionCodec:
    """Preserve integer tuples exactly as opaque checkpoint payloads."""

    component_id: ComponentId = 'multiobjective-vns:knapsack:solution-codec'
    configuration_id: ComponentConfigurationId = 'knapsack-tuple-codec-v1'

    def capture(self, solution: Solution) -> CodecPayload:
        """Return the supplied tuple unchanged, including malformed bit vectors.

        :param solution: Immutable tuple of integer item decisions.
        :returns: The same tuple, used as the opaque codec payload.
        :raises TypeError: If the value is not a tuple of integers.
        """
        return self._require_integer_tuple(solution)

    def restore(self, payload: CodecPayload) -> Solution:
        """Restore the tuple payload without changing its length or entries.

        :param payload: Tuple previously returned by :meth:`capture`.
        :returns: The unchanged solution tuple.
        :raises TypeError: If the payload is not a tuple of integers.
        """
        return self._require_integer_tuple(payload)

    @staticmethod
    def _require_integer_tuple(value: object) -> tuple[int, ...]:
        if not isinstance(value, tuple):
            raise TypeError('knapsack solutions and payloads must be tuples')
        if any(type(item) not in (int, bool) for item in value):
            raise TypeError('knapsack tuple entries must be integers')
        return value
