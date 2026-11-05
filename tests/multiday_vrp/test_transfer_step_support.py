"""Tests for transfer-operation route and path rebuilding.

Created: 2026-11-04T12:32:23+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.operators.transfer_step_support import (
    rebuild_routes,
)
from multiobjective_vns.multiday_vrp.solution import (
    TransferOperationStop,
    TransferStep,
    TransferStepRef,
)
from multiobjective_vns.random_source import RandomSource

from .test_capacity_transfer_operators import _package_instance


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 10000)

    def random(self) -> float:
        return next(self._draws)


def test_rebuild_routes_handles_added_transfer_stop_and_path(
    tmp_path: Path,
) -> None:
    instance, _objectives, original = _package_instance(tmp_path)
    original_route = original.routes[0]
    operation = TransferOperationStop(
        TransferStepRef('pkg', 1), 'out', 0.0, 0.0, 0.0, False
    )
    updated_route = replace(
        original_route,
        stops=(operation, *original_route.stops),
    )
    provisional = replace(
        original,
        routes=(updated_route, *original.routes[1:]),
        transfer_steps=(TransferStep('pkg', 1, 'c'),),
    )

    results = rebuild_routes(
        instance,
        original,
        provisional,
        {0},
        random_source=cast(RandomSource, _ZeroRandomSource()),
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
        max_candidate_alternatives=8,
    )

    assert results
    candidate = results[0]
    arc_endpoints = {
        item.arc_id: (item.tail_node_id, item.head_node_id)
        for item in instance.network_data.arcs
    }
    route = candidate.routes[0]
    assert len(route.legs) == len(route.stops) + 1
    first_leg = route.legs[0].arc_ids
    assert arc_endpoints[first_leg[0]][0] == 'a'
    assert arc_endpoints[first_leg[-1]][1] == 'c'
