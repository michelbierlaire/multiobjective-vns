"""Tests for feature-specific package-chain repair Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from random import Random
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    PackageRecord,
    PackageTransferFacilityRecord,
    TransferFacilityRecord,
    VehiclePackageCompatibilityRecord,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.package_chain import (
    PackageChainMode,
    PackageChainRelocationOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
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


class _PatternRandomSource:
    def __init__(self, pattern: tuple[float, ...]) -> None:
        self._pattern = pattern
        self._position = 0

    def random(self) -> float:
        value = self._pattern[self._position % len(self._pattern)]
        self._position += 1
        return value


def test_compatibility_repair_moves_package_chain_to_compatible_route(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0)
    package_data = instance.package_data._replace(
        packages=(package,),
        vehicle_package_compatibility=(
            VehiclePackageCompatibilityRecord('vehicle-1', 'pkg', False),
            VehiclePackageCompatibilityRecord('vehicle-2', 'pkg', True),
        ),
    )
    instance = instance._replace(package_data=package_data)
    routes = list(base.routes)
    first_stops = list(routes[0].stops)
    first_stops[0] = ActivityStop(
        ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    first_stops[1] = ActivityStop(
        ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    routes[0] = replace(routes[0], stops=tuple(first_stops))
    base = replace(base, routes=tuple(routes))
    operator = PackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='compatibility',
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

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert not any(
        isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'pkg'
        for stop in changed.routes[0].stops
    )
    package_stops = tuple(
        stop
        for stop in changed.routes[1].stops
        if isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'pkg'
    )
    assert tuple(stop.activity_ref.kind for stop in package_stops) == (
        'pickup',
        'delivery',
    )


@pytest.mark.parametrize('mode', ('compatibility', 'requests'))
def test_package_repair_preserves_transfer_chain_and_reassigns_all_carriers(
    tmp_path: Path, mode: str
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 1, 0.0, 0.0)
    instance = instance._replace(
        network_data=instance.network_data._replace(
            transfer_facilities=(TransferFacilityRecord('c', 100.0),)
        ),
        fleet_data=instance.fleet_data._replace(
            vehicles=(
                *instance.fleet_data.vehicles,
                instance.fleet_data.vehicles[0]._replace(vehicle_id='vehicle-3'),
            )
        ),
        package_data=instance.package_data._replace(
            packages=(package,),
            package_transfer_facilities=(PackageTransferFacilityRecord('pkg', 'c'),),
            vehicle_package_compatibility=(
                VehiclePackageCompatibilityRecord('vehicle-1', 'pkg', False),
                VehiclePackageCompatibilityRecord('vehicle-2', 'pkg', True),
                VehiclePackageCompatibilityRecord('vehicle-3', 'pkg', True),
            ),
        ),
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            TransferOperationStop(
                TransferStepRef('pkg', 1), 'out', 0.0, 0.0, 0.0, False
            ),
        ),
        legs=(RouteLeg(('a-b',)), RouteLeg(('b-c',)), RouteLeg(('c-a',))),
    )
    routes.append(
        replace(
            base.routes[1],
            day_index=2,
            stops=(
                TransferOperationStop(
                    TransferStepRef('pkg', 1), 'in', 0.0, 0.0, 0.0, False
                ),
                ActivityStop(
                    ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
                ),
            ),
            legs=(RouteLeg(('a-c',)), RouteLeg(()), RouteLeg(('c-a',))),
        )
    )
    routes.append(replace(base.routes[1], vehicle_id='vehicle-3'))
    base = replace(
        base,
        routes=tuple(routes),
        transfer_steps=(TransferStep('pkg', 1, 'c'),),
    )
    operator = PackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode=cast(PackageChainMode, mode),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
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

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.transfer_steps == base.transfer_steps
    transfer_vehicles = {
        stop.role: route.vehicle_id
        for route in changed.routes
        for stop in route.stops
        if type(stop) is TransferOperationStop
        and stop.transfer_step_ref == TransferStepRef('pkg', 1)
    }
    assert set(transfer_vehicles.values()) == {'vehicle-2', 'vehicle-3'}
    assert transfer_vehicles['out'] != transfer_vehicles['in']
    assert {
        route.vehicle_id
        for route in changed.routes
        for stop in route.stops
        if type(stop) is ActivityStop and stop.activity_ref.activity_id == 'pkg'
    } == {'vehicle-3', 'vehicle-2'}


def test_compatibility_repair_returns_up_to_context_candidate_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0)
    instance = instance._replace(
        package_data=instance.package_data._replace(
            packages=(package,),
            vehicle_package_compatibility=(
                VehiclePackageCompatibilityRecord('vehicle-1', 'pkg', False),
                VehiclePackageCompatibilityRecord('vehicle-2', 'pkg', True),
            ),
        )
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
            ),
        ),
    )
    base = replace(base, routes=tuple(routes))

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        position = next(
            stop_index
            for route in solution.routes
            if route.vehicle_id == 'vehicle-2'
            for stop_index, stop in enumerate(route.stops)
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('pickup', 'pkg')
        )
        return ApplicationEvaluation(True, True, (float(position),))

    operator = PackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='compatibility',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(
            RandomSource,
            _PatternRandomSource((0.0, 0.2, 0.4, 0.6, 0.8)),
        ),
        max_candidates=2,
    )

    result = operator.call(context)

    assert len(result.candidates) == 2
    assert all(proposal.m == 1 for proposal in result.candidates)
    assert len({proposal.candidate.solution for proposal in result.candidates}) == 2
    positions = tuple(
        next(
            stop_index
            for route in cast(VrpSolution, proposal.candidate.solution).routes
            if route.vehicle_id == 'vehicle-2'
            for stop_index, stop in enumerate(route.stops)
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('pickup', 'pkg')
        )
        for proposal in result.candidates
    )
    assert positions == tuple(sorted(positions))

    unranked = PackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='compatibility',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    ).call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(
                RandomSource,
                _PatternRandomSource((0.0, 0.2, 0.4, 0.6, 0.8)),
            ),
            max_candidates=2,
        )
    )
    assert len(unranked.candidates) == 2
    assert len({proposal.candidate.solution for proposal in unranked.candidates}) == 2


def test_compatibility_fallback_counts_distinct_compatible_fleet_vehicles(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    packages = (
        PackageRecord('pkg-1', 'b', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
        PackageRecord('pkg-2', 'c', 'd', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
    )
    instance = instance._replace(
        package_data=instance.package_data._replace(
            packages=packages,
            vehicle_package_compatibility=(
                VehiclePackageCompatibilityRecord('vehicle-1', 'pkg-1', True),
                VehiclePackageCompatibilityRecord('vehicle-2', 'pkg-1', False),
                VehiclePackageCompatibilityRecord('vehicle-1', 'pkg-2', False),
                VehiclePackageCompatibilityRecord('vehicle-2', 'pkg-2', True),
            ),
        )
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'pkg-1'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'pkg-1'), frozenset(), 0.0, 0.0, 0.0, False
            ),
        ),
    )
    routes[1] = replace(
        routes[1],
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'pkg-2'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'pkg-2'), frozenset(), 0.0, 0.0, 0.0, False
            ),
        ),
    )
    routes.append(
        replace(
            base.routes[0],
            day_index=2,
            stops=(),
            legs=(RouteLeg(()),),
        )
    )
    base = replace(base, routes=tuple(routes))
    operator = PackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='compatibility',
        max_neighborhood_size=1,
    )

    assert operator._eligible_packages(base) == ('pkg-1', 'pkg-2')


def test_compatibility_repair_preserves_order_with_independent_stop_positions(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0)
    instance = instance._replace(
        package_data=instance.package_data._replace(
            packages=(package,),
            vehicle_package_compatibility=(
                VehiclePackageCompatibilityRecord('vehicle-1', 'pkg', False),
                VehiclePackageCompatibilityRecord('vehicle-2', 'pkg', True),
            ),
        )
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
            ),
        ),
    )
    base = replace(base, routes=tuple(routes))
    operator = PackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='compatibility',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _PatternRandomSource((0.2, 0.4, 0.6, 0.8, 0.1))),
        max_candidates=8,
    )

    result = operator.call(context)

    assert result.candidates
    has_intervening_stop = False
    for proposal in result.candidates:
        solution = cast(VrpSolution, proposal.candidate.solution)
        route = next(
            route
            for route in solution.routes
            if any(
                type(stop) is ActivityStop
                and stop.activity_ref == ActivityRef('pickup', 'pkg')
                for stop in route.stops
            )
        )
        pickup_index = next(
            index
            for index, stop in enumerate(route.stops)
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('pickup', 'pkg')
        )
        delivery_index = next(
            index
            for index, stop in enumerate(route.stops)
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('delivery', 'pkg')
        )
        assert pickup_index < delivery_index
        has_intervening_stop |= any(
            type(stop) is DepotOnlyStop
            for stop in route.stops[pickup_index + 1 : delivery_index]
        )
    assert has_intervening_stop


def test_request_repair_rebuilds_selected_chains_jointly_from_each_trial_base(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    packages = (
        PackageRecord('pkg-a', 'b', 'c', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
        PackageRecord('pkg-b', 'd', 'e', 1.0, 0.0, 0.0, 0, 0.0, 0.0),
    )
    instance = instance._replace(
        package_data=instance.package_data._replace(packages=packages)
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'pkg-a'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'pkg-a'), frozenset(), 0.0, 0.0, 0.0, False
            ),
        ),
        legs=(RouteLeg(('a-b',)), RouteLeg(('b-c',)), RouteLeg(('c-a',))),
    )
    routes[1] = replace(
        routes[1],
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'pkg-b'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'pkg-b'), frozenset(), 0.0, 0.0, 0.0, False
            ),
        ),
        legs=(RouteLeg(('a-d',)), RouteLeg(('d-e',)), RouteLeg(('e-a',))),
    )
    base = replace(base, routes=tuple(routes))

    def placement(
        solution: VrpSolution, package_id: str
    ) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (route.vehicle_id, route.day_index, stop_index, stop.activity_ref.kind)
            for route in solution.routes
            for stop_index, stop in enumerate(route.stops)
            if type(stop) is ActivityStop
            and stop.activity_ref.activity_id == package_id
        )

    assessed_solutions: list[VrpSolution] = []

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        assessed_solutions.append(solution)
        return ApplicationEvaluation(True, True, (0.0,))

    operator = PackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='requests',
        max_neighborhood_size=2,
        max_candidate_alternatives=32,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, Random(7321)),
        max_candidates=32,
    )

    result = operator.call(context)

    assert len(result.candidates) >= 2
    assert len(assessed_solutions) == len(result.candidates)
    assert {proposal.m for proposal in result.candidates} == {1, 2}
    assert (
        len(
            {
                placement(cast(VrpSolution, proposal.candidate.solution), 'pkg-a')
                for proposal in result.candidates
            }
        )
        > 1
    )
    for proposal in result.candidates:
        candidate = cast(VrpSolution, proposal.candidate.solution)
        assert all(
            sum(
                type(stop) is ActivityStop
                and stop.activity_ref == ActivityRef(kind, package_id)
                for route in candidate.routes
                for stop in route.stops
            )
            == 1
            for package_id in ('pkg-a', 'pkg-b')
            for kind in ('pickup', 'delivery')
        )
        expected_modifications = sum(
            placement(candidate, package_id) != placement(base, package_id)
            for package_id in ('pkg-a', 'pkg-b')
        )
        assert expected_modifications > 0
        assert proposal.m == expected_modifications
