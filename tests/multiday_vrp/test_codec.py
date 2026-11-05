"""Tests for the multi-day VRP solution checkpoint codec.

Created: 2026-11-03T16:06:37+01:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.callbacks import SolutionCodec
from multiobjective_vns.multiday_vrp.codec import VrpSolutionCodec
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)


def make_solution() -> VrpSolution:
    stop = ActivityStop(
        activity_ref=ActivityRef(kind='pickup', activity_id='unknown-package'),
        team_member_ids=frozenset(),
        pre_service_wait=float('nan'),
        wait=-1.0,
        break_duration=0.0,
        tour_end=False,
    )
    route = VehicleDayRoute(
        vehicle_id='unknown-vehicle',
        day_index=0,
        overnight_depot_id='unknown-depot',
        start_time=float('inf'),
        driver_id='unknown-person',
        stops=(stop,),
        legs=(RouteLeg(('unknown-arc',)),),
    )
    return VrpSolution(routes=(route,), transfer_steps=())


def test_codec_exposes_stable_checkpoint_component_identity() -> None:
    codec = VrpSolutionCodec()

    assert codec.component_id == 'multiobjective-vns:multiday-vrp:solution-codec'
    assert codec.configuration_id == 'multiday-vrp-solution-object-v1'


def test_codec_satisfies_the_shared_solution_codec_protocol() -> None:
    codec: SolutionCodec = VrpSolutionCodec()

    assert codec.component_id == 'multiobjective-vns:multiday-vrp:solution-codec'


def test_capture_and_restore_return_the_exact_same_solution_object() -> None:
    codec = VrpSolutionCodec()
    solution = make_solution()

    payload = codec.capture(solution)
    restored = codec.restore(payload)

    assert payload is solution
    assert restored is solution
    assert restored.routes[0].stops[0].wait == -1.0
    assert restored.routes[0].start_time == float('inf')


@pytest.mark.parametrize('value', [None, (), {}, 'solution'])
def test_capture_rejects_values_with_another_root_type(value: object) -> None:
    with pytest.raises(TypeError):
        VrpSolutionCodec().capture(value)


@pytest.mark.parametrize('payload', [None, (), {}, 'payload'])
def test_restore_rejects_payloads_with_another_root_type(payload: object) -> None:
    with pytest.raises(TypeError):
        VrpSolutionCodec().restore(payload)
