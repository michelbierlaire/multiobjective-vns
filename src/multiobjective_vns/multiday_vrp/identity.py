"""Identify VRP plans by their authoritative immutable record structure.

Created: 2026-11-03T23:52:10+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from math import isnan
from typing import cast

from multiobjective_vns.callbacks import Solution
from multiobjective_vns.identifiers import ComponentConfigurationId, ComponentId
from multiobjective_vns.multiday_vrp.solution import VrpSolution

__all__ = ['VrpSolutionIdentity']


class VrpSolutionIdentity:
    """Apply the structural equality contract for immutable VRP plans.

    :raises TypeError: If either root value is not exactly a ``VrpSolution``.
    """

    component_id: ComponentId = 'multiobjective-vns:multiday-vrp:solution-identity'
    configuration_id: ComponentConfigurationId = 'multiday-vrp-structural-v1'

    def equivalent(self, left: Solution, right: Solution) -> bool:
        """Return whether two plans have equal authoritative record values.

        Ordered routes, stops, legs, arcs, and transfer steps compare in order.
        Set-valued crew fields compare by membership. Numeric scalar types and
        values compare exactly; NaNs at corresponding fields compare equal.

        :param left: First immutable plan.
        :param right: Second immutable plan.
        :returns: Whether the plans have the same structural identity.
        :raises TypeError: If either value is not exactly a ``VrpSolution``.
        """
        if type(left) is not VrpSolution or type(right) is not VrpSolution:
            raise TypeError('both identity values must be exactly VrpSolution')
        return _structurally_equal(left, right)


def _structurally_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is float:
        left_float = left
        right_float = cast(float, right)
        if isnan(left_float) and isnan(right_float):
            return True
        return left_float == right_float
    if is_dataclass(left) and not isinstance(left, type):
        if not is_dataclass(right) or isinstance(right, type):
            return False
        return all(
            _structurally_equal(getattr(left, field.name), getattr(right, field.name))
            for field in fields(left)
        )
    if type(left) is tuple:
        left_tuple = cast(tuple[object, ...], left)
        right_tuple = cast(tuple[object, ...], right)
        return len(left_tuple) == len(right_tuple) and all(
            _structurally_equal(left_value, right_value)
            for left_value, right_value in zip(left_tuple, right_tuple, strict=True)
        )
    if type(left) is frozenset:
        return left == right
    return left == right
