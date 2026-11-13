"""Identify VRP plans modulo declared pickup-order symmetries.

Created: 2026-11-03T23:52:10+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from hashlib import sha256
from math import isnan
from types import MappingProxyType
from typing import cast

from multiobjective_vns.callbacks import LookupKey, Solution
from multiobjective_vns.identifiers import ComponentConfigurationId, ComponentId
from multiobjective_vns.multiday_vrp.codec import VrpSolutionCodec
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    VehicleDayRoute,
    VrpSolution,
)

__all__ = ['VrpSolutionIdentity']


class VrpSolutionIdentity:
    """Compare immutable VRP plans under an instance's pickup symmetry groups.

    Contiguous pickup blocks declared interchangeable by the instance compare
    by sorted package ID. Complete stop records move together, preserving all
    stop fields with their package. Route boundaries and travel legs retain
    their positions. Every other authoritative field uses exact comparison.

    :param instance: Validated instance that declares the pickup symmetry
        groups used by this identity callback.
    :raises TypeError: If ``instance`` is not exactly a ``LoadedVrpInstance``.
    """

    __slots__ = ('_codec', '_group_by_package')

    component_id: ComponentId = 'multiobjective-vns:multiday-vrp:solution-identity'
    configuration_id: ComponentConfigurationId = (
        'multiday-vrp-canonical-pickup-identity-v4'
    )
    compatibility_configuration_id_aliases: Mapping[str, str] = MappingProxyType(
        {
            'multiday-vrp-canonical-pickup-identity-v2': (
                'multiday-vrp-canonical-pickup-identity-v4'
            ),
            'multiday-vrp-canonical-pickup-identity-v3': (
                'multiday-vrp-canonical-pickup-identity-v4'
            ),
        }
    )

    def __init__(self, instance: LoadedVrpInstance) -> None:
        if type(instance) is not LoadedVrpInstance:
            raise TypeError('instance must be exactly a LoadedVrpInstance')
        group_by_package = {
            package_id: group.group_id
            for group in instance.pickup_symmetry_groups
            for package_id in group.package_ids
        }
        self._group_by_package = MappingProxyType(group_by_package)
        self._codec = VrpSolutionCodec()

    def equivalent(self, left: Solution, right: Solution) -> bool:
        """Return whether two plans have the same canonical identity.

        Declared contiguous pickup blocks compare after sorting complete stop
        records by package ID. Route boundaries and travel legs remain fixed;
        all other ordered records retain their order. Set-valued crew fields
        compare by membership. Numeric values compare exactly; NaNs at
        corresponding fields compare equal.

        :param left: First immutable plan.
        :param right: Second immutable plan.
        :returns: Whether both plans have the same canonical identity.
        :raises TypeError: If either value is not exactly a ``VrpSolution``.
        """
        if type(left) is not VrpSolution or type(right) is not VrpSolution:
            raise TypeError('both identity values must be exactly VrpSolution')
        left_canonical = _canonicalize_solution(left, self._group_by_package)
        right_canonical = _canonicalize_solution(right, self._group_by_package)
        return _structurally_equal(left_canonical, right_canonical)

    def lookup_key(self, solution: Solution) -> LookupKey:
        """Return a hashable canonical key for registry and proposal lookup.

        The key applies the same pickup-order canonicalization and floating-
        point equality rules as :meth:`equivalent`. It is a lookup accelerator;
        callers still confirm equivalence for values in the same key bucket.

        :param solution: Immutable VRP plan.
        :returns: 32-byte SHA-256 digest of the canonical solution payload.
        :raises TypeError: If the value is not exactly a ``VrpSolution``.
        """
        if type(solution) is not VrpSolution:
            raise TypeError('identity value must be exactly VrpSolution')
        canonical = _canonicalize_solution(solution, self._group_by_package)
        payload = self._codec.capture(canonical)
        for field_name in (
            'start_time',
            'pre_service_wait',
            'wait',
            'break_duration',
        ):
            payload = payload.replace(
                f'"{field_name}":"-0x0.0p+0"'.encode('ascii'),
                f'"{field_name}":"0x0.0p+0"'.encode('ascii'),
            )
        return cast(LookupKey, sha256(payload).digest())


def _canonicalize_solution(
    solution: VrpSolution,
    group_by_package: Mapping[str, str],
) -> VrpSolution:
    routes = tuple(
        _canonicalize_route(route, group_by_package) for route in solution.routes
    )
    if all(route is original for route, original in zip(routes, solution.routes)):
        return solution
    return replace(solution, routes=routes)


def _canonicalize_route(
    route: VehicleDayRoute,
    group_by_package: Mapping[str, str],
) -> VehicleDayRoute:
    stops = list(route.stops)
    changed = False
    start = 0
    while start < len(stops):
        group_id = _pickup_group_id(stops[start], group_by_package)
        if group_id is None or not _qualifying_pickup_stop(stops[start]):
            start += 1
            continue
        end = start + 1
        while end < len(stops):
            if _pickup_group_id(stops[end], group_by_package) != group_id:
                break
            if not _qualifying_pickup_stop(stops[end]):
                break
            # Route leg ``end`` connects the previous stop in the block to
            # this stop. A nonempty path makes this a boundary between blocks.
            if end >= len(route.legs) or route.legs[end].arc_ids:
                break
            end += 1
        block = stops[start:end]
        if len(block) >= 2:
            ordered = sorted(block, key=_package_id)
            if block != ordered:
                stops[start:end] = ordered
                changed = True
        start = end
    if not changed:
        return route
    return replace(route, stops=tuple(stops))


def _pickup_group_id(
    stop: object,
    group_by_package: Mapping[str, str],
) -> str | None:
    if type(stop) is not ActivityStop:
        return None
    activity_ref = stop.activity_ref
    if activity_ref.kind != 'pickup':
        return None
    return group_by_package.get(activity_ref.activity_id)


def _qualifying_pickup_stop(stop: object) -> bool:
    if type(stop) is not ActivityStop:
        return False
    return not stop.tour_end


def _package_id(stop: object) -> str:
    activity_ref = cast(ActivityStop, stop).activity_ref
    return activity_ref.activity_id


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
