"""VRP objective ordering and declaration construction.

Created: 2026-11-03T18:03:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf, isnan

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)

__all__ = ['VrpObjectiveFunction', 'build_vrp_objective_declaration']


@dataclass(frozen=True, slots=True)
class VrpObjectiveFunction:
    """Compare and display one minimized VRP objective.

    :param objective_id: Stable objective identifier from the loaded instance.
    :param component_id: Package-owned identity used in failure diagnostics.
    :param configuration_id: Stable float-comparison and display configuration.
    """

    objective_id: str
    component_id: str
    configuration_id: str

    def __post_init__(self) -> None:
        for name in ('objective_id', 'component_id', 'configuration_id'):
            value = getattr(self, name)
            if type(value) is not str or not value or value.strip() != value:
                raise ValueError(f'{name} must be a nonempty string without padding')

    def compare(self, left: object, right: object) -> ObjectiveComparison:
        """Compare VRP objective values using exact Python float ordering.

        :param left: Finite Python float or the positive-infinity hard-infeasible
            sentinel.
        :param right: Finite Python float or the positive-infinity sentinel.
        :returns: The exact numeric ordering under minimization.
        :raises TypeError: If either value is not an exact Python float.
        :raises ValueError: If either value is NaN or negative infinity.
        """
        left_value = _objective_float(left, name='left')
        right_value = _objective_float(right, name='right')
        if left_value < right_value:
            return ObjectiveComparison.LESS
        if left_value > right_value:
            return ObjectiveComparison.GREATER
        return ObjectiveComparison.EQUAL

    def format(self, value: object) -> str:
        """Format a finite objective value or hard-infeasibility sentinel.

        :param value: Objective value to display.
        :returns: Round-trip decimal text, or a readable infeasible label.
        :raises TypeError: If ``value`` is not an exact Python float.
        :raises ValueError: If ``value`` is NaN or negative infinity.
        """
        numeric = _objective_float(value, name='value')
        if numeric == inf:
            return 'infeasible (+inf)'
        return repr(numeric)

    def __str__(self) -> str:
        """Return the objective identifier as its display name."""
        return self.objective_id


def build_vrp_objective_declaration(
    instance: LoadedVrpInstance,
) -> ObjectiveDeclaration:
    """Build the generic package's ordered objective interface for an instance.

    :param instance: Validated VRP instance containing at least one objective.
    :returns: Objective functions in the input declaration order.
    """
    objectives = tuple(
        VrpObjectiveFunction(
            objective_id=spec.objective_id,
            component_id=(
                'multiobjective-vns:multiday-vrp:objective-function:'
                f'{spec.objective_id}'
            ),
            configuration_id=(
                'multiobjective-vns:multiday-vrp:float-objective-v1:'
                f'{spec.objective_id}'
            ),
        )
        for spec in instance.objective_data.objectives
    )
    return ObjectiveDeclaration(objectives)


def _objective_float(value: object, *, name: str) -> float:
    if type(value) is not float:
        raise TypeError(f'{name} objective value must be an exact Python float')
    if isnan(value) or value == -inf:
        raise ValueError(f'{name} objective value must be finite or positive infinity')
    return value
