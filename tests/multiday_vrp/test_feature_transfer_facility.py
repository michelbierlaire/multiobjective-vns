"""Tests for transfer-facility assignment Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    PackageRecord,
    PackageTransferFacilityRecord,
    TransferFacilityRecord,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.transfer_facility import (
    TransferFacilityAssignmentOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    RouteLeg,
    TransferOperationStop,
    TransferRole,
    TransferStep,
    TransferStepRef,
    VrpSolution,
)
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


def test_transfer_facility_assignment_updates_step_and_both_routes(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 1, 0.0, 0.0)
    instance = instance._replace(
        network_data=instance.network_data._replace(
            transfer_facilities=(
                TransferFacilityRecord('c', 100.0),
                TransferFacilityRecord('d', 100.0),
            )
        ),
        package_data=instance.package_data._replace(
            packages=(package,),
            package_transfer_facilities=(
                PackageTransferFacilityRecord('pkg', 'c'),
                PackageTransferFacilityRecord('pkg', 'd'),
            ),
        ),
    )
    routes = []
    for route_index, role in enumerate(('out', 'in')):
        route = base.routes[route_index]
        stop = TransferOperationStop(
            TransferStepRef('pkg', 1), cast(TransferRole, role), 0.0, 0.0, 0.0, False
        )
        routes.append(
            replace(
                route,
                stops=(stop,),
                legs=(RouteLeg(('a-c',)), RouteLeg(('c-a',))),
            )
        )
    base = replace(
        base,
        routes=tuple(routes),
        transfer_steps=(TransferStep('pkg', 1, 'c'),),
    )
    operator = TransferFacilityAssignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )

    result = operator.call(context)

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert changed.transfer_steps[0].facility_id == 'd'
    arc_endpoints = {
        arc.arc_id: (arc.tail_node_id, arc.head_node_id)
        for arc in instance.network_data.arcs
    }
    for route in changed.routes:
        assert arc_endpoints[route.legs[0].arc_ids[0]][0] == 'a'
        assert arc_endpoints[route.legs[0].arc_ids[-1]][1] == 'd'
