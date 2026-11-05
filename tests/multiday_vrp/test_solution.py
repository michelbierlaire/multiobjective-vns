"""Tests for immutable multi-day VRP solution records.

Created: 2026-11-03T16:01:44+01:00
Author: Michel Bierlaire, EPFL
"""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.solution import (
    ActivityKind,
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
    TransferOperationStop,
    TransferStep,
    TransferStepRef,
    VehicleDayRoute,
    VrpSolution,
)


def make_activity_stop() -> ActivityStop:
    return ActivityStop(
        activity_ref=ActivityRef(kind='pickup', activity_id='package-1'),
        team_member_ids=frozenset(),
        pre_service_wait=0.0,
        wait=0.0,
        break_duration=0.0,
        tour_end=False,
    )


def make_route(
    *,
    stops: tuple[object, ...] | None = None,
    legs: tuple[object, ...] | None = None,
) -> VehicleDayRoute:
    route_stops = (make_activity_stop(),) if stops is None else stops
    route_legs = (RouteLeg(('arc-1',)), RouteLeg(('arc-2',))) if legs is None else legs
    return VehicleDayRoute(
        vehicle_id='vehicle-1',
        day_index=1,
        overnight_depot_id='depot-1',
        start_time=0.0,
        driver_id='person-1',
        stops=route_stops,  # type: ignore[arg-type]
        legs=route_legs,  # type: ignore[arg-type]
    )


def make_solution() -> VrpSolution:
    return VrpSolution(routes=(make_route(),), transfer_steps=())


def test_solution_records_preserve_authoritative_field_values() -> None:
    activity_stop = make_activity_stop()
    transfer_stop = TransferOperationStop(
        transfer_step_ref=TransferStepRef(package_id='package-1', step_number=1),
        role='out',
        pre_service_wait=1.5,
        wait=2.0,
        break_duration=0.5,
        tour_end=True,
    )
    depot_stop = DepotOnlyStop(
        node_id='node-1',
        pre_service_wait=0.0,
        wait=3.0,
        break_duration=1.0,
        tour_end=False,
    )
    transfer_step = TransferStep(
        package_id='package-1', step_number=1, facility_id='node-2'
    )
    route = make_route(
        stops=(activity_stop, transfer_stop, depot_stop),
        legs=(RouteLeg(('arc-1',)), RouteLeg(()), RouteLeg(()), RouteLeg(('arc-2',))),
    )

    solution = VrpSolution(routes=(route,), transfer_steps=(transfer_step,))

    assert solution.routes == (route,)
    assert solution.routes[0].stops[1] == transfer_stop
    assert solution.routes[0].stops[2] == depot_stop
    assert solution.transfer_steps == (transfer_step,)


def test_intervention_stop_carries_an_immutable_team_set() -> None:
    stop = ActivityStop(
        activity_ref=ActivityRef(kind='intervention', activity_id='job-1'),
        team_member_ids=frozenset({'person-2', 'person-3'}),
        pre_service_wait=0.0,
        wait=0.0,
        break_duration=0.0,
        tour_end=False,
    )

    assert stop.team_member_ids == frozenset({'person-2', 'person-3'})


def test_records_are_frozen() -> None:
    solution = make_solution()

    with pytest.raises(FrozenInstanceError):
        solution.routes = ()  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        solution.routes[0].driver_id = 'person-2'  # type: ignore[misc]


@pytest.mark.parametrize(
    ('factory', 'field_value'),
    [
        (lambda value: VrpSolution(routes=value, transfer_steps=()), []),
        (
            lambda value: VehicleDayRoute(
                vehicle_id='vehicle-1',
                day_index=1,
                overnight_depot_id='depot-1',
                start_time=0.0,
                driver_id='person-1',
                stops=value,
                legs=(),
            ),
            [],
        ),
        (lambda value: RouteLeg(arc_ids=value), ['arc-1']),
        (
            lambda value: ActivityStop(
                activity_ref=ActivityRef(kind='intervention', activity_id='job-1'),
                team_member_ids=value,
                pre_service_wait=0.0,
                wait=0.0,
                break_duration=0.0,
                tour_end=False,
            ),
            {'person-1'},
        ),
    ],
)
def test_collection_fields_require_the_declared_immutable_type(
    factory: Callable[[object], object], field_value: object
) -> None:
    with pytest.raises(TypeError):
        factory(field_value)


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('vehicle_id', 3),
        ('day_index', True),
        ('start_time', 0),
        ('driver_id', None),
    ],
)
def test_route_rejects_values_with_the_wrong_exact_scalar_type(
    field: str, value: object
) -> None:
    values: dict[str, object] = {
        'vehicle_id': 'vehicle-1',
        'day_index': 1,
        'overnight_depot_id': 'depot-1',
        'start_time': 0.0,
        'driver_id': 'person-1',
        'stops': (),
        'legs': (),
    }
    values[field] = value

    with pytest.raises(TypeError):
        VehicleDayRoute(**values)  # type: ignore[arg-type]


def test_nested_collection_members_must_be_solution_records() -> None:
    with pytest.raises(TypeError):
        VrpSolution(routes=(object(),), transfer_steps=())  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        make_route(stops=(object(),))

    with pytest.raises(TypeError):
        VrpSolution(
            routes=(),
            transfer_steps=(object(),),  # type: ignore[arg-type]
        )


def test_constructor_accepts_typed_but_semantically_invalid_plan_for_assessment() -> (
    None
):
    invalid_stop = ActivityStop(
        activity_ref=ActivityRef(
            kind=cast(ActivityKind, 'not-an-activity'), activity_id='missing'
        ),
        team_member_ids=frozenset(),
        pre_service_wait=float('nan'),
        wait=-1.0,
        break_duration=4.0,
        tour_end=False,
    )
    invalid_route = VehicleDayRoute(
        vehicle_id='unknown-vehicle',
        day_index=-1,
        overnight_depot_id='unknown-depot',
        start_time=float('inf'),
        driver_id='unknown-person',
        stops=(invalid_stop,),
        legs=(),
    )

    solution = VrpSolution(routes=(invalid_route,), transfer_steps=())

    assert solution.routes[0].day_index == -1
    assert (
        solution.routes[0].stops[0].pre_service_wait
        != solution.routes[0].stops[0].pre_service_wait
    )


def test_literals_still_require_string_runtime_values() -> None:
    with pytest.raises(TypeError):
        ActivityRef(kind=1, activity_id='package-1')  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        TransferOperationStop(
            transfer_step_ref=TransferStepRef(package_id='package-1', step_number=1),
            role=1,  # type: ignore[arg-type]
            pre_service_wait=0.0,
            wait=0.0,
            break_duration=0.0,
            tour_end=False,
        )
