"""Tests for feature-specific activity relocation Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    VrpInterventionData,
)
from multiobjective_vns.multiday_vrp.operators.activity_relocation import (
    ActivityRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
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


def test_intervention_relocation_moves_activity_and_rebuilds_routes(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    intervention = InterventionRecord('repair', 'b', 10.0, 1)
    empty = VrpInterventionData((), (), (), (), ())
    instance = instance._replace(
        intervention_data=empty._replace(interventions=(intervention,))
    )
    routes = list(base.routes)
    stops = list(routes[0].stops)
    stops[0] = ActivityStop(
        ActivityRef('intervention', 'repair'), frozenset(), 0.0, 0.0, 0.0, False
    )
    routes[0] = replace(routes[0], stops=tuple(stops))
    base = replace(base, routes=tuple(routes))
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='intervention',
        max_neighborhood_size=2,
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
    locations = tuple(
        (route_index, stop_index)
        for route_index, route in enumerate(changed.routes)
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'repair'
    )
    assert locations != ((0, 0),)


def test_availability_guided_relocation_supports_activity_stops(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    intervention = InterventionRecord('repair', 'b', 10.0, 1)
    empty = VrpInterventionData((), (), (), (), ())
    instance = instance._replace(
        intervention_data=empty._replace(interventions=(intervention,))
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'repair'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
        legs=(RouteLeg(('a-b',)), RouteLeg(('b-a',))),
    )
    base = replace(base, routes=tuple(routes))
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='vehicle_availability',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
        )
    )

    assert result.m == 1
    assert result.candidate is not None


def test_availability_guided_relocation_can_move_depot_stops(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(DepotOnlyStop('b', 0.0, 1.0, 0.0, False),),
        legs=(RouteLeg(('a-b',)), RouteLeg(('b-a',))),
    )
    base = replace(base, routes=tuple(routes))
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='vehicle_availability',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
        )
    )

    assert result.m == 1
    assert result.candidate is not None
