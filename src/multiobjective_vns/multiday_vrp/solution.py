"""Immutable solution records for the multi-day vehicle-routing application.

Created: 2026-11-03T16:03:02+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

type ActivityId = str
type ActivityKind = Literal['pickup', 'delivery', 'intervention']
type ArcId = str
type DepotId = str
type NodeId = str
type PackageId = str
type PersonId = str
type TransferRole = Literal['out', 'in']
type VehicleId = str

__all__ = [
    'ActivityId',
    'ActivityKind',
    'ActivityRef',
    'ActivityStop',
    'ArcId',
    'DepotId',
    'DepotOnlyStop',
    'NodeId',
    'PackageId',
    'PersonId',
    'RouteLeg',
    'RouteStop',
    'TransferOperationStop',
    'TransferRole',
    'TransferStep',
    'TransferStepRef',
    'VehicleDayRoute',
    'VehicleId',
    'VrpSolution',
]


def _require_exact_type(
    value: object,
    expected_type: type[object],
    *,
    field_name: str,
) -> None:
    if type(value) is not expected_type:
        raise TypeError(f'{field_name} must be an exact {expected_type.__name__}')


def _require_tuple_items(
    value: object,
    *,
    field_name: str,
    item_types: tuple[type[object], ...],
) -> None:
    _require_exact_type(value, tuple, field_name=field_name)
    items = cast(tuple[object, ...], value)
    if any(type(item) not in item_types for item in items):
        names = ', '.join(item_type.__name__ for item_type in item_types)
        raise TypeError(f'{field_name} must contain only {names}')


def _require_frozenset_of_strings(value: object, *, field_name: str) -> None:
    _require_exact_type(value, frozenset, field_name=field_name)
    items = cast(frozenset[object], value)
    if any(type(item) is not str for item in items):
        raise TypeError(f'{field_name} must contain only exact str values')


@dataclass(frozen=True, slots=True)
class ActivityRef:
    """Identify a pickup, delivery, or intervention activity.

    :param kind: Activity category; the evaluator checks its declared domain.
    :param activity_id: Application identifier for the activity.
    """

    kind: ActivityKind
    activity_id: ActivityId

    def __post_init__(self) -> None:
        _require_exact_type(self.kind, str, field_name='kind')
        _require_exact_type(self.activity_id, str, field_name='activity_id')


@dataclass(frozen=True, slots=True)
class TransferStepRef:
    """Identify one package transfer step.

    :param package_id: Application identifier for the package.
    :param step_number: One-based position in the package's transfer chain.
    """

    package_id: PackageId
    step_number: int

    def __post_init__(self) -> None:
        _require_exact_type(self.package_id, str, field_name='package_id')
        _require_exact_type(self.step_number, int, field_name='step_number')


@dataclass(frozen=True, slots=True)
class RouteLeg:
    """Store the selected directed road path between adjacent route stops.

    :param arc_ids: Ordered arc identifiers for this path.
    """

    arc_ids: tuple[ArcId, ...]

    def __post_init__(self) -> None:
        _require_tuple_items(
            self.arc_ids,
            field_name='arc_ids',
            item_types=(str,),
        )


@dataclass(frozen=True, slots=True)
class ActivityStop:
    """Represent one pickup, delivery, or intervention route stop.

    :param activity_ref: Activity served at this stop.
    :param team_member_ids: Assigned intervention team, or empty for package
        pickup and delivery stops.
    :param pre_service_wait: Planned wait before service, in the instance time
        unit.
    :param wait: Planned stationary time after service, in the instance time
        unit.
    :param break_duration: Scheduled break within the post-service wait.
    :param tour_end: Whether this stop ends an intermediate tour at a depot.
    """

    activity_ref: ActivityRef
    team_member_ids: frozenset[PersonId]
    pre_service_wait: float
    wait: float
    break_duration: float
    tour_end: bool

    def __post_init__(self) -> None:
        _require_exact_type(self.activity_ref, ActivityRef, field_name='activity_ref')
        _require_frozenset_of_strings(
            self.team_member_ids,
            field_name='team_member_ids',
        )
        _require_exact_type(self.pre_service_wait, float, field_name='pre_service_wait')
        _require_exact_type(self.wait, float, field_name='wait')
        _require_exact_type(self.break_duration, float, field_name='break_duration')
        _require_exact_type(self.tour_end, bool, field_name='tour_end')


@dataclass(frozen=True, slots=True)
class TransferOperationStop:
    """Represent the outgoing or incoming operation of a transfer step.

    :param transfer_step_ref: Package and step represented by this operation.
    :param role: ``'out'`` for the sending operation or ``'in'`` for receipt.
    :param pre_service_wait: Planned wait before the transfer operation.
    :param wait: Planned stationary time after the operation.
    :param break_duration: Scheduled break within the post-operation wait.
    :param tour_end: Whether this stop ends an intermediate tour at a depot.
    """

    transfer_step_ref: TransferStepRef
    role: TransferRole
    pre_service_wait: float
    wait: float
    break_duration: float
    tour_end: bool

    def __post_init__(self) -> None:
        _require_exact_type(
            self.transfer_step_ref,
            TransferStepRef,
            field_name='transfer_step_ref',
        )
        _require_exact_type(self.role, str, field_name='role')
        _require_exact_type(self.pre_service_wait, float, field_name='pre_service_wait')
        _require_exact_type(self.wait, float, field_name='wait')
        _require_exact_type(self.break_duration, float, field_name='break_duration')
        _require_exact_type(self.tour_end, bool, field_name='tour_end')


@dataclass(frozen=True, slots=True)
class DepotOnlyStop:
    """Represent depot dwell or a scheduled break at a road node.

    :param node_id: Road node mapped to at least one depot in the instance.
    :param pre_service_wait: Zero-valued field retained for the common stop
        timing shape.
    :param wait: Planned stationary time after arrival.
    :param break_duration: Scheduled break within the wait.
    :param tour_end: Whether this stop ends an intermediate tour.
    """

    node_id: NodeId
    pre_service_wait: float
    wait: float
    break_duration: float
    tour_end: bool

    def __post_init__(self) -> None:
        _require_exact_type(self.node_id, str, field_name='node_id')
        _require_exact_type(self.pre_service_wait, float, field_name='pre_service_wait')
        _require_exact_type(self.wait, float, field_name='wait')
        _require_exact_type(self.break_duration, float, field_name='break_duration')
        _require_exact_type(self.tour_end, bool, field_name='tour_end')


@dataclass(frozen=True, slots=True)
class VehicleDayRoute:
    """Store the decisions for one active vehicle on one calendar day.

    :param vehicle_id: Vehicle assigned to the route.
    :param day_index: One-based planning-day index.
    :param overnight_depot_id: Depot where the route finishes.
    :param start_time: Planned duty start on the continuous instance time axis.
    :param driver_id: Person assigned as the route's driver.
    :param stops: Ordered route stops.
    :param legs: Paths aligned with gaps before, between, and after stops.
    """

    vehicle_id: VehicleId
    day_index: int
    overnight_depot_id: DepotId
    start_time: float
    driver_id: PersonId
    stops: tuple[RouteStop, ...]
    legs: tuple[RouteLeg, ...]

    def __post_init__(self) -> None:
        _require_exact_type(self.vehicle_id, str, field_name='vehicle_id')
        _require_exact_type(self.day_index, int, field_name='day_index')
        _require_exact_type(
            self.overnight_depot_id,
            str,
            field_name='overnight_depot_id',
        )
        _require_exact_type(self.start_time, float, field_name='start_time')
        _require_exact_type(self.driver_id, str, field_name='driver_id')
        _require_tuple_items(
            self.stops,
            field_name='stops',
            item_types=(ActivityStop, TransferOperationStop, DepotOnlyStop),
        )
        _require_tuple_items(
            self.legs,
            field_name='legs',
            item_types=(RouteLeg,),
        )


@dataclass(frozen=True, slots=True)
class TransferStep:
    """Store one package transfer through a transfer facility.

    :param package_id: Identifier for the package whose custody is transferred.
    :param step_number: One-based position in the package's transfer chain.
    :param facility_id: Road node identifier of the transfer facility.
    """

    package_id: PackageId
    step_number: int
    facility_id: NodeId

    def __post_init__(self) -> None:
        _require_exact_type(self.package_id, str, field_name='package_id')
        _require_exact_type(self.step_number, int, field_name='step_number')
        _require_exact_type(self.facility_id, str, field_name='facility_id')


@dataclass(frozen=True, slots=True)
class VrpSolution:
    """Represent an immutable multi-day VRP plan.

    :param routes: Active vehicle-day routes in their declared order.
    :param transfer_steps: Package transfer steps in custody order.
    """

    routes: tuple[VehicleDayRoute, ...]
    transfer_steps: tuple[TransferStep, ...]

    def __post_init__(self) -> None:
        _require_tuple_items(
            self.routes,
            field_name='routes',
            item_types=(VehicleDayRoute,),
        )
        _require_tuple_items(
            self.transfer_steps,
            field_name='transfer_steps',
            item_types=(TransferStep,),
        )


type RouteStop = ActivityStop | TransferOperationStop | DepotOnlyStop
