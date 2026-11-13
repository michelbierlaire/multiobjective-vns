"""Checkpoint codec for immutable knapsack solution tuples.

Created: 2026-10-29T17:44:53+00:00
Author: Michel Bierlaire, EPFL
"""

import json

from multiobjective_vns.callbacks import CodecPayload, Solution
from multiobjective_vns.identifiers import ComponentConfigurationId, ComponentId


class KnapsackSolutionCodec:
    """Encode integer tuples as UTF-8 JSON bytes for durable snapshots."""

    component_id: ComponentId = 'multiobjective-vns:knapsack:solution-codec'
    configuration_id: ComponentConfigurationId = 'knapsack-json-codec-v1'

    def capture(self, solution: Solution) -> CodecPayload:
        """Encode a tuple, including malformed bit vectors, as compact JSON.

        :param solution: Immutable tuple of integer item decisions.
        :returns: UTF-8 bytes containing the tuple entries as a JSON array.
        :raises TypeError: If the value is not a tuple of integers.
        """
        values = self._require_integer_tuple(solution)
        return json.dumps(list(values), separators=(',', ':')).encode('utf-8')

    def restore(self, payload: CodecPayload) -> Solution:
        """Restore the tuple represented by captured UTF-8 JSON bytes.

        :param payload: Bytes previously returned by :meth:`capture`.
        :returns: The reconstructed tuple, preserving its entries and length.
        :raises TypeError: If the payload is not bytes or a JSON array of
            integers and Booleans.
        :raises ValueError: If the payload is not valid UTF-8 JSON.
        """
        if type(payload) is not bytes:
            raise TypeError('knapsack codec payloads must be bytes')
        try:
            decoded = json.loads(payload.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(
                'knapsack codec payload is not valid UTF-8 JSON'
            ) from error
        if not isinstance(decoded, list):
            raise TypeError('knapsack codec payload must contain a JSON array')
        return self._require_integer_tuple(tuple(decoded))

    @staticmethod
    def _require_integer_tuple(value: object) -> tuple[int, ...]:
        if not isinstance(value, tuple):
            raise TypeError('knapsack solutions and payloads must be tuples')
        if any(type(item) not in (int, bool) for item in value):
            raise TypeError('knapsack tuple entries must be integers')
        return value
