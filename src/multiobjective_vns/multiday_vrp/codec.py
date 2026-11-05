"""Checkpoint capture and restoration for multi-day VRP solutions.

Created: 2026-11-03T16:06:52+01:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.identifiers import ComponentConfigurationId, ComponentId
from multiobjective_vns.multiday_vrp.solution import VrpSolution


class VrpSolutionCodec:
    """Preserve immutable VRP solution values as opaque checkpoint payloads.

    The codec uses the solution object itself as its payload. It performs no
    serialization; the configured checkpoint store preserves the payload.
    """

    __slots__ = ()

    component_id: ComponentId = 'multiobjective-vns:multiday-vrp:solution-codec'
    configuration_id: ComponentConfigurationId = 'multiday-vrp-solution-object-v1'

    def capture(self, solution: object) -> VrpSolution:
        """Return the supplied VRP solution as its checkpoint payload.

        :param solution: Application value resolved as a ``VrpSolution``.
        :returns: The exact solution object, unchanged.
        :raises TypeError: If ``solution`` is not exactly a ``VrpSolution``.
        """
        if type(solution) is not VrpSolution:
            raise TypeError('solution must be exactly a VrpSolution')
        return solution

    def restore(self, payload: object) -> VrpSolution:
        """Return a previously captured VRP solution payload unchanged.

        :param payload: Opaque checkpoint payload supplied by the store.
        :returns: The exact ``VrpSolution`` payload object.
        :raises TypeError: If ``payload`` is not exactly a ``VrpSolution``.
        """
        if type(payload) is not VrpSolution:
            raise TypeError('payload must be exactly a VrpSolution')
        return payload
