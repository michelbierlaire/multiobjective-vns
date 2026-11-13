"""Tests for optional service coverage Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    PackageRecord,
)
from multiobjective_vns.multiday_vrp.operators import service_coverage
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.service_coverage import (
    ServiceCoverageChangeOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteLeg,
    RouteStop,
    TransferOperationStop,
    TransferStep,
    TransferStepRef,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


class _SequenceRandomSource:
    def __init__(self, draws: tuple[float, ...]) -> None:
        self._draws = iter(draws)

    def random(self) -> float:
        return next(self._draws, 0.0)


def _optional_package_plan(
    tmp_path: Path,
    *,
    present: tuple[str, ...],
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0)
    instance = instance._replace(
        settings=instance.settings._replace(service_coverage_mode='penalized_omission'),
        package_data=instance.package_data._replace(packages=(package,)),
    )
    route = base.routes[0]
    stops = list(route.stops)
    activity_refs = (ActivityRef('pickup', 'pkg'), ActivityRef('delivery', 'pkg'))
    for index, ref in enumerate(activity_refs):
        if ref.kind in present:
            stops[index] = ActivityStop(
                ref, frozenset(), 0.0, 0.0, 0.0, False
            )
    routes = list(base.routes)
    routes[0] = replace(route, stops=tuple(stops))
    return instance, objectives, replace(base, routes=tuple(routes))


def test_removing_package_pickup_removes_dependent_service_chain(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0)
    instance = instance._replace(
        settings=instance.settings._replace(service_coverage_mode='penalized_omission'),
        package_data=instance.package_data._replace(packages=(package,)),
    )
    route = base.routes[0]
    stops = list(route.stops)
    stops[0] = ActivityStop(
        ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    stops[1] = ActivityStop(
        ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    routes = list(base.routes)
    routes[0] = replace(route, stops=tuple(stops))
    base = replace(base, routes=tuple(routes))
    operator = ServiceCoverageChangeOperator(
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

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert not any(
        isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'pkg'
        for route in changed.routes
        for stop in route.stops
    )


def test_removing_package_pickup_also_removes_transfer_operations(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 1, 0.0, 0.0)
    instance = instance._replace(
        settings=instance.settings._replace(service_coverage_mode='penalized_omission'),
        package_data=instance.package_data._replace(packages=(package,)),
    )
    first_route, second_route = base.routes
    out_stop = TransferOperationStop(
        TransferStepRef('pkg', 1), 'out', 0.0, 0.0, 0.0, False
    )
    in_stop = TransferOperationStop(
        TransferStepRef('pkg', 1), 'in', 0.0, 0.0, 0.0, False
    )
    routes = (
        replace(
            first_route,
            stops=(
                ActivityStop(
                    ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
                ),
                out_stop,
            ),
            legs=(RouteLeg(('a-b',)), RouteLeg(('b-c',)), RouteLeg(('c-a',))),
        ),
        replace(
            second_route,
            stops=(
                in_stop,
                ActivityStop(
                    ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
                ),
            ),
            legs=(RouteLeg(('a-c',)), RouteLeg(()), RouteLeg(('c-a',))),
        ),
    )
    base = VrpSolution(routes, (TransferStep('pkg', 1, 'c'),))
    operator = ServiceCoverageChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
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

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.transfer_steps == ()
    assert not any(
        isinstance(stop, TransferOperationStop)
        for route in changed.routes
        for stop in route.stops
    )


def test_each_service_coverage_trial_starts_from_the_same_base(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _optional_package_plan(
        tmp_path, present=('pickup', 'delivery')
    )
    operator = ServiceCoverageChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            max_candidates=2,
        )
    )

    assert len(result.candidates) == 2
    assert all(proposal.m == 1 for proposal in result.candidates)
    states = set()
    plans = set()
    for proposal in result.candidates:
        solution = cast(VrpSolution, proposal.candidate.solution)
        plans.add(solution)
        states.add(
            tuple(
                any(
                    type(stop) is ActivityStop
                    and stop.activity_ref == ActivityRef(kind, 'pkg')
                    for route in solution.routes
                    for stop in route.stops
                )
                for kind in ('pickup', 'delivery')
            )
        )
    assert states == {(False, False), (True, False)}
    assert len(plans) == 2
    assert any(
        type(stop) is ActivityStop and stop.activity_ref == ActivityRef('pickup', 'pkg')
        for route in base.routes
        for stop in route.stops
    )


def test_inserting_absent_service_produces_single_toggle_candidates(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _optional_package_plan(tmp_path, present=())
    operator = ServiceCoverageChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            max_candidates=2,
        )
    )

    assert result.candidates
    assert len(result.candidates) <= 2
    assert all(proposal.m == 1 for proposal in result.candidates)
    for proposal in result.candidates:
        solution = cast(VrpSolution, proposal.candidate.solution)
        assert sum(
            type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('pickup', 'pkg')
            for route in solution.routes
            for stop in route.stops
        ) == 1
        assert not any(
            type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('delivery', 'pkg')
            for route in solution.routes
            for stop in route.stops
        )


def test_candidate_limit_is_applied_per_activity_choice(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _optional_package_plan(
        tmp_path, present=('pickup', 'delivery')
    )

    def assess(
        _instance: object,
        _objectives: object,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        has_pickup = any(
            type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('pickup', 'pkg')
            for route in solution.routes
            for stop in route.stops
        )
        if not has_pickup:
            return ApplicationEvaluation(False, None, None)
        return ApplicationEvaluation(True, True, (0.0,))

    operator = ServiceCoverageChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=1,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            max_candidates=1,
        )
    )

    assert len(result.candidates) == 1
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert result.candidates[0].m == 1
    assert any(
        type(stop) is ActivityStop
        and stop.activity_ref == ActivityRef('pickup', 'pkg')
        for route in candidate.routes
        for stop in route.stops
    )
    assert not any(
        type(stop) is ActivityStop
        and stop.activity_ref == ActivityRef('delivery', 'pkg')
        for route in candidate.routes
        for stop in route.stops
    )


def test_delivery_removal_keeps_pickup_and_transfer_custody(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 1, 0.0, 0.0)
    instance = instance._replace(
        settings=instance.settings._replace(service_coverage_mode='penalized_omission'),
        package_data=instance.package_data._replace(packages=(package,)),
    )
    first_route, second_route = base.routes
    out_stop = TransferOperationStop(
        TransferStepRef('pkg', 1), 'out', 0.0, 0.0, 0.0, False
    )
    in_stop = TransferOperationStop(
        TransferStepRef('pkg', 1), 'in', 0.0, 0.0, 0.0, False
    )
    base = VrpSolution(
        (
            replace(
                first_route,
                stops=(
                    ActivityStop(
                        ActivityRef('pickup', 'pkg'),
                        frozenset(),
                        0.0,
                        0.0,
                        0.0,
                        False,
                    ),
                    out_stop,
                ),
                legs=(RouteLeg(('a-b',)), RouteLeg(('b-c',)), RouteLeg(('c-a',))),
            ),
            replace(
                second_route,
                stops=(
                    in_stop,
                    ActivityStop(
                        ActivityRef('delivery', 'pkg'),
                        frozenset(),
                        0.0,
                        0.0,
                        0.0,
                        False,
                    ),
                ),
                legs=(RouteLeg(('a-c',)), RouteLeg(()), RouteLeg(('c-a',))),
            ),
        ),
        (TransferStep('pkg', 1, 'c'),),
    )
    operator = ServiceCoverageChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
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
            cast(RandomSource, _SequenceRandomSource((0.9,))),
            max_candidates=1,
        )
    )

    assert result.candidates[0].m == 1
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.transfer_steps == base.transfer_steps
    assert any(
        type(stop) is ActivityStop
        and stop.activity_ref == ActivityRef('pickup', 'pkg')
        for route in changed.routes
        for stop in route.stops
    )
    assert not any(
        type(stop) is ActivityStop
        and stop.activity_ref == ActivityRef('delivery', 'pkg')
        for route in changed.routes
        for stop in route.stops
    )
    assert sum(
        type(stop) is TransferOperationStop
        for route in changed.routes
        for stop in route.stops
    ) == 2


def test_insertion_inspects_at_most_the_configured_candidate_alternatives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _optional_package_plan(tmp_path, present=())
    assessments = 0
    rebuilt_count = 0

    def assess(*_args: object) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return ApplicationEvaluation(True, True, (0.0,))

    def rebuild_many(
        _instance: object,
        working: VrpSolution,
        stops_by_route: dict[int, tuple[RouteStop, ...]],
        **_kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        nonlocal rebuilt_count
        route_index, stops = next(iter(stops_by_route.items()))
        candidates = []
        for _ in range(4):
            rebuilt_count += 1
            routes = list(working.routes)
            routes[route_index] = replace(
                routes[route_index],
                stops=stops,
                legs=tuple(RouteLeg(()) for _ in range(len(stops) + 1)),
                start_time=float(rebuilt_count),
            )
            candidates.append(replace(working, routes=tuple(routes)))
        return tuple(candidates)

    monkeypatch.setattr(service_coverage, 'rebuild_changed_routes', rebuild_many)
    operator = ServiceCoverageChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=4,
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
            max_candidates=2,
        )
    )

    assert len(result.candidates) == 2
    assert all(proposal.m == 1 for proposal in result.candidates)
    assert rebuilt_count == 4
    assert assessments == 4
