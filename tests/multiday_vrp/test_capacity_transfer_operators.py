"""Tests for cargo, transfer, and tool-capacity repair Operators.

Created: 2026-11-04T12:39:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from random import Random
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    InterventionToolRecord,
    LoadedVrpInstance,
    PackageRecord,
    PackageTransferFacilityRecord,
    ToolRecord,
    TransferFacilityRecord,
    VehiclePackageCompatibilityRecord,
)
from multiobjective_vns.multiday_vrp.operators.capacity_profile import (
    capacity_excess_by_stop,
    capacity_states_by_stop,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.package_capacity import (
    CapacityAwarePackageChainRelocationOperator,
    _chain_placement,
)
from multiobjective_vns.multiday_vrp.operators.tool_capacity import (
    ToolReservationGuidedInterventionRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators.transfer_step import (
    TransferStepOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
    RouteStop,
    TransferOperationStop,
    TransferStep,
    TransferStepRef,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 10000)

    def random(self) -> float:
        return next(self._draws)


def _context(
    base: VrpSolution, *, k: int = 1, max_candidates: int = 1
) -> CandidateContext:
    return CandidateContext(
        base=base,
        base_evaluation=None,
        k=k,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, _ZeroRandomSource()),
        max_candidates=max_candidates,
    )


def _package_instance(
    tmp_path: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 2.0, 0.0, 0.0, 2, 0.0, 0.0)
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
    return instance, objectives, base


def test_capacity_aware_package_chain_relocation_returns_complete_candidate(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    vehicles = instance.fleet_data.vehicles
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=(vehicles[0]._replace(cargo_capacity=1.0), vehicles[1])
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
    operator = CapacityAwarePackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    package_occurrences = [
        (route.vehicle_id, stop.activity_ref.kind)
        for route in changed.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'pkg'
    ]
    assert {vehicle for vehicle, _kind in package_occurrences} == {'vehicle-2'}
    assert {kind for _vehicle, kind in package_occurrences} == {'pickup', 'delivery'}


def test_capacity_relocation_returns_capacity_ranked_objective_tiebreak_batch(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    vehicles = instance.fleet_data.vehicles
    vehicle_1 = vehicles[0]._replace(cargo_capacity=1.0)
    vehicle_3 = vehicles[0]._replace(vehicle_id='vehicle-3', cargo_capacity=100.0)
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=(vehicle_1, vehicles[1], vehicle_3)
        )
    )
    instance = instance._replace(
        package_data=instance.package_data._replace(
            vehicle_package_compatibility=(
                *instance.package_data.vehicle_package_compatibility,
                VehiclePackageCompatibilityRecord('vehicle-3', 'pkg', True),
            )
        )
    )
    pickup = ActivityStop(
        ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    delivery = ActivityStop(
        ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(pickup, DepotOnlyStop('g', 0.0, 1.0, 0.0, False), delivery),
        legs=(
            RouteLeg(('a-b',)),
            RouteLeg(('b-g',)),
            RouteLeg(('g-c',)),
            RouteLeg(('c-a',)),
        ),
    )
    routes.append(
        replace(
            base.routes[1],
            vehicle_id='vehicle-3',
            stops=(DepotOnlyStop('f', 0.0, 1.0, 0.0, False),),
            legs=(RouteLeg(('a-f',)), RouteLeg(('f-a',))),
        )
    )
    base = replace(base, routes=tuple(routes))

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        pickup_vehicle = next(
            route.vehicle_id
            for route in solution.routes
            for stop in route.stops
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('pickup', 'pkg')
        )
        value = {'vehicle-1': 0.0, 'vehicle-2': 2.0, 'vehicle-3': 1.0}[pickup_vehicle]
        return ApplicationEvaluation(True, True, (value,))

    operator = CapacityAwarePackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base, max_candidates=3))

    assert len(result.candidates) == 3
    assert all(proposal.m == 1 for proposal in result.candidates)
    changed = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    carriers = tuple(
        next(
            route.vehicle_id
            for route in solution.routes
            for stop in route.stops
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('pickup', 'pkg')
        )
        for solution in changed
    )
    assert carriers == ('vehicle-3', 'vehicle-2', 'vehicle-1')
    assert tuple(
        max(capacity_excess_by_stop(instance, solution).values(), default=0.0)
        for solution in changed
    ) == (0.0, 0.0, 1.0)

    capped = operator.call(_context(base, max_candidates=2))
    capped_carriers = tuple(
        next(
            route.vehicle_id
            for route in cast(VrpSolution, proposal.candidate.solution).routes
            for stop in route.stops
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('pickup', 'pkg')
        )
        for proposal in capped.candidates
    )
    assert capped_carriers == ('vehicle-3', 'vehicle-2')

    unranked = CapacityAwarePackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    ).call(_context(base, max_candidates=3))
    unranked_carriers = tuple(
        next(
            route.vehicle_id
            for route in cast(VrpSolution, proposal.candidate.solution).routes
            for stop in route.stops
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('pickup', 'pkg')
        )
        for proposal in unranked.candidates
    )
    assert unranked_carriers == ('vehicle-2', 'vehicle-3', 'vehicle-1')


def test_capacity_relocation_targets_heaviest_package_in_and_out_of_excess(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    heavy = PackageRecord('pkg-heavy', 'c', 'e', 3.0, 0.0, 0.0, 2, 0.0, 0.0)
    instance = instance._replace(
        package_data=instance.package_data._replace(
            packages=(*instance.package_data.packages, heavy),
            vehicle_package_compatibility=(
                *instance.package_data.vehicle_package_compatibility,
                VehiclePackageCompatibilityRecord('vehicle-1', 'pkg-heavy', True),
                VehiclePackageCompatibilityRecord('vehicle-2', 'pkg-heavy', True),
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
                ActivityRef('pickup', 'pkg-heavy'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
            ActivityStop(
                ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'pkg-heavy'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
        legs=(
            RouteLeg(('a-b',)),
            RouteLeg(('b-c',)),
            RouteLeg(('c-d',)),
            RouteLeg(('d-e',)),
            RouteLeg(('e-a',)),
        ),
    )
    base = replace(base, routes=tuple(routes))

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        heavy_vehicle = next(
            route.vehicle_id
            for route in solution.routes
            for stop in route.stops
            if type(stop) is ActivityStop
            and stop.activity_ref == ActivityRef('pickup', 'pkg-heavy')
        )
        return ApplicationEvaluation(
            True, True, (0.0 if heavy_vehicle == 'vehicle-2' else 1.0,)
        )

    for vehicle_capacity in (4.0, 10.0):
        vehicles = instance.fleet_data.vehicles
        capacity_instance = instance._replace(
            fleet_data=instance.fleet_data._replace(
                vehicles=(
                    vehicles[0]._replace(cargo_capacity=vehicle_capacity),
                    *vehicles[1:],
                )
            )
        )
        operator = CapacityAwarePackageChainRelocationOperator(
            capacity_instance,
            objectives,
            cast(VrpPlanAssessor, assess),
            objective_id=objectives.objectives[0].objective_id,
            max_neighborhood_size=1,
            max_candidate_alternatives=8,
            max_paths_per_leg=1,
            max_arc_expansion_steps=500,
        )

        result = operator.call(_context(base))

        assert result.candidates
        changed = cast(VrpSolution, result.candidates[0].candidate.solution)
        carriers = {
            stop.activity_ref.activity_id: route.vehicle_id
            for route in changed.routes
            for stop in route.stops
            if type(stop) is ActivityStop and stop.activity_ref.kind == 'pickup'
        }
        assert carriers == {'pkg': 'vehicle-1', 'pkg-heavy': 'vehicle-2'}


def test_capacity_profile_reports_loaded_packages_at_excess_state(
    tmp_path: Path,
) -> None:
    instance, _objectives, base = _package_instance(tmp_path)
    vehicles = instance.fleet_data.vehicles
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=(vehicles[0]._replace(cargo_capacity=1.0), vehicles[1])
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
    solution = replace(base, routes=tuple(routes))

    excess = capacity_excess_by_stop(instance, solution)
    states = capacity_states_by_stop(instance, solution)

    assert excess[(0, 0)] == 1.0
    assert states[(0, 0)] == (1.0, frozenset({'pkg'}))


def test_package_relocation_has_no_target_for_tool_only_capacity_excess(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    vehicles = instance.fleet_data.vehicles
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=(vehicles[0]._replace(cargo_capacity=1.0), vehicles[1])
        ),
        intervention_data=instance.intervention_data._replace(
            interventions=(InterventionRecord('repair', 'b', 1.0, 0),),
            tools=(ToolRecord('tool', 3.0),),
            intervention_tools=(InterventionToolRecord('repair', 'tool'),),
        ),
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
    )
    routes[1] = replace(
        routes[1],
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
    operator = CapacityAwarePackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    targets = operator._target_packages(base, _context(base))

    assert targets == ()


def test_add_transfer_step_moves_delivery_to_a_distinct_compatible_vehicle(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _package_instance(tmp_path)
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
    operator = TransferStepOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='add',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.transfer_steps == (TransferStep('pkg', 1, 'c'),)
    transfer_vehicles = {
        stop.role: route.vehicle_id
        for route in changed.routes
        for stop in route.stops
        if isinstance(stop, TransferOperationStop)
    }
    assert transfer_vehicles['out'] != transfer_vehicles['in']
    assert any(
        isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('delivery', 'pkg')
        and route.vehicle_id == transfer_vehicles['in']
        for route in changed.routes
        for stop in route.stops
    )


def test_add_transfer_step_supports_omitted_delivery(tmp_path: Path) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        legs=(RouteLeg(('a-b',)), RouteLeg(('b-a',))),
        stops=(
            ActivityStop(
                ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
            ),
        ),
    )
    base = replace(base, routes=tuple(routes))
    operator = TransferStepOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='add',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=2,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.transfer_steps == (TransferStep('pkg', 1, 'c'),)
    assert not any(
        type(stop) is ActivityStop and stop.activity_ref.kind == 'delivery'
        for route in changed.routes
        for stop in route.stops
    )


def test_add_transfer_step_rejects_incompatible_sender(tmp_path: Path) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    instance = instance._replace(
        package_data=instance.package_data._replace(
            vehicle_package_compatibility=(
                VehiclePackageCompatibilityRecord('vehicle-1', 'pkg', False),
            )
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
    operator = TransferStepOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='add',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=2,
        max_arc_expansion_steps=500,
    )

    assert operator.call(_context(base)).candidates == ()


def test_remove_transfer_step_can_remove_multiple_steps_of_one_package(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    vehicles = instance.fleet_data.vehicles
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=(*vehicles, vehicles[0]._replace(vehicle_id='vehicle-3'))
        )
    )

    def route(
        vehicle_id: str,
        stops: tuple[RouteStop, ...],
        stop_nodes: tuple[str, ...],
    ) -> VehicleDayRoute:
        nodes = ('a', *stop_nodes, 'a')
        legs = tuple(
            RouteLeg(()) if tail == head else RouteLeg((f'{tail}-{head}',))
            for tail, head in zip(nodes[:-1], nodes[1:], strict=True)
        )
        return replace(
            base.routes[0],
            vehicle_id=vehicle_id,
            stops=stops,
            legs=legs,
        )

    base = replace(
        base,
        routes=(
            route(
                'vehicle-1',
                (
                    ActivityStop(
                        ActivityRef('pickup', 'pkg'),
                        frozenset(),
                        0.0,
                        0.0,
                        0.0,
                        False,
                    ),
                    TransferOperationStop(
                        TransferStepRef('pkg', 1), 'out', 0.0, 0.0, 0.0, False
                    ),
                ),
                ('b', 'c'),
            ),
            route(
                'vehicle-2',
                (
                    TransferOperationStop(
                        TransferStepRef('pkg', 1), 'in', 0.0, 0.0, 0.0, False
                    ),
                    TransferOperationStop(
                        TransferStepRef('pkg', 2), 'out', 0.0, 0.0, 0.0, False
                    ),
                ),
                ('c', 'd'),
            ),
            route(
                'vehicle-3',
                (
                    TransferOperationStop(
                        TransferStepRef('pkg', 2), 'in', 0.0, 0.0, 0.0, False
                    ),
                    ActivityStop(
                        ActivityRef('delivery', 'pkg'),
                        frozenset(),
                        0.0,
                        0.0,
                        0.0,
                        False,
                    ),
                ),
                ('d', 'c'),
            ),
        ),
        transfer_steps=(
            TransferStep('pkg', 1, 'c'),
            TransferStep('pkg', 2, 'd'),
        ),
    )
    operator = TransferStepOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='remove',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=2,
        max_arc_expansion_steps=500,
    )

    one_step = operator.call(_context(base, k=1))

    assert one_step.candidates
    intermediate = cast(VrpSolution, one_step.candidates[0].candidate.solution)
    assert intermediate.transfer_steps == (TransferStep('pkg', 1, 'd'),)
    assert any(
        route.vehicle_id == 'vehicle-3'
        and any(
            type(stop) is TransferOperationStop
            and stop.transfer_step_ref == TransferStepRef('pkg', 1)
            and stop.role == 'in'
            for stop in route.stops
        )
        for route in intermediate.routes
    )

    result = operator.call(_context(base, k=2))

    assert result.candidates
    assert result.candidates[0].m == 2
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.transfer_steps == ()


def test_remove_transfer_step_rejects_same_vehicle_for_next_handoff(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _package_instance(tmp_path)
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
            TransferOperationStop(
                TransferStepRef('pkg', 2), 'in', 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
            ),
        ),
        legs=(
            RouteLeg(('a-b',)),
            RouteLeg(('b-c',)),
            RouteLeg(('c-d',)),
            RouteLeg(('d-c',)),
            RouteLeg(('c-a',)),
        ),
    )
    routes[1] = replace(
        routes[1],
        stops=(
            TransferOperationStop(
                TransferStepRef('pkg', 1), 'in', 0.0, 0.0, 0.0, False
            ),
            TransferOperationStop(
                TransferStepRef('pkg', 2), 'out', 0.0, 0.0, 0.0, False
            ),
        ),
        legs=(
            RouteLeg(('a-c',)),
            RouteLeg(('c-d',)),
            RouteLeg(('d-a',)),
        ),
    )
    base = replace(
        base,
        routes=tuple(routes),
        transfer_steps=(
            TransferStep('pkg', 1, 'c'),
            TransferStep('pkg', 2, 'd'),
        ),
    )
    operator = TransferStepOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='remove',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=2,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates == ()


def test_reassign_returns_ordered_candidate_batch_up_to_context_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    base = _one_transfer_plan(base)
    instance = instance._replace(
        package_data=instance.package_data._replace(
            package_transfer_facilities=(PackageTransferFacilityRecord('pkg', 'c'),)
        )
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            routes[0].stops[0],
            DepotOnlyStop('a', 0.0, 1.0, 0.0, False),
            routes[0].stops[1],
        ),
        legs=(
            RouteLeg(('a-b',)),
            RouteLeg(('b-a',)),
            RouteLeg(('a-c',)),
            RouteLeg(('c-a',)),
        ),
    )
    routes[1] = replace(
        routes[1],
        stops=(
            routes[1].stops[0],
            DepotOnlyStop('a', 0.0, 1.0, 0.0, False),
            routes[1].stops[1],
        ),
        legs=(
            RouteLeg(('a-c',)),
            RouteLeg(('c-a',)),
            RouteLeg(('a-c',)),
            RouteLeg(('c-a',)),
        ),
    )
    base = replace(base, routes=tuple(routes))

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        outgoing_index = next(
            index
            for route in solution.routes
            for index, stop in enumerate(route.stops)
            if type(stop) is TransferOperationStop and stop.role == 'out'
        )
        return ApplicationEvaluation(True, True, (float(outgoing_index),))

    operator = TransferStepOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='reassign',
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=8,
        max_paths_per_leg=2,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base=base,
        base_evaluation=None,
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, Random(17)),
        max_candidates=2,
    )

    result = operator.call(context)

    assert len(result.candidates) == 2
    scores = tuple(
        float(
            next(
                index
                for route in cast(VrpSolution, item.candidate.solution).routes
                for index, stop in enumerate(route.stops)
                if type(stop) is TransferOperationStop and stop.role == 'out'
            )
        )
        for item in result.candidates
    )
    assert scores == tuple(sorted(scores))


def test_reassign_rejects_candidates_with_incompatible_package_carriers(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    base = _one_transfer_plan(base)
    vehicles = instance.fleet_data.vehicles
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=(*vehicles, vehicles[0]._replace(vehicle_id='vehicle-3'))
        ),
        package_data=instance.package_data._replace(
            package_transfer_facilities=(PackageTransferFacilityRecord('pkg', 'c'),),
            vehicle_package_compatibility=(
                VehiclePackageCompatibilityRecord('vehicle-1', 'pkg', True),
                VehiclePackageCompatibilityRecord('vehicle-2', 'pkg', False),
                VehiclePackageCompatibilityRecord('vehicle-3', 'pkg', True),
            ),
        ),
    )
    base = replace(
        base,
        routes=(
            *base.routes,
            replace(
                base.routes[1],
                vehicle_id='vehicle-3',
                stops=(DepotOnlyStop('f', 0.0, 1.0, 0.0, False),),
                legs=(RouteLeg(('a-f',)), RouteLeg(('f-a',))),
            ),
        ),
    )
    operator = TransferStepOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='reassign',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=2,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base, max_candidates=8))

    assert result.candidates
    receivers = tuple(
        next(
            route.vehicle_id
            for route in cast(VrpSolution, proposal.candidate.solution).routes
            for stop in route.stops
            if type(stop) is TransferOperationStop
            and stop.role == 'in'
            and stop.transfer_step_ref == TransferStepRef('pkg', 1)
        )
        for proposal in result.candidates
    )
    assert set(receivers) == {'vehicle-3'}


def _one_transfer_plan(
    base: VrpSolution,
) -> VrpSolution:
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
    )
    routes[1] = replace(
        routes[1],
        stops=(
            TransferOperationStop(
                TransferStepRef('pkg', 1), 'in', 0.0, 0.0, 0.0, False
            ),
            ActivityStop(
                ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
            ),
        ),
    )
    return replace(
        base,
        routes=tuple(routes),
        transfer_steps=(TransferStep('pkg', 1, 'c'),),
    )


def test_capacity_relocation_preserves_transfer_count_and_facility(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    base = _one_transfer_plan(base)
    vehicles = instance.fleet_data.vehicles
    vehicle_3 = vehicles[0]._replace(vehicle_id='vehicle-3', cargo_capacity=100.0)
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicles=(*vehicles, vehicle_3)),
        package_data=instance.package_data._replace(
            vehicle_package_compatibility=(
                *instance.package_data.vehicle_package_compatibility,
                VehiclePackageCompatibilityRecord('vehicle-3', 'pkg', True),
            )
        ),
    )
    routes = (
        *base.routes,
        replace(
            base.routes[1],
            vehicle_id='vehicle-3',
            stops=(DepotOnlyStop('f', 0.0, 1.0, 0.0, False),),
            legs=(RouteLeg(('a-f',)), RouteLeg(('f-a',))),
        ),
    )
    base = replace(base, routes=routes)
    vehicles = instance.fleet_data.vehicles
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=(vehicles[0]._replace(cargo_capacity=1.0), *vehicles[1:])
        )
    )
    operator = CapacityAwarePackageChainRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert (
        changed.transfer_steps == base.transfer_steps == (TransferStep('pkg', 1, 'c'),)
    )
    transfer_vehicle_by_role = {
        stop.role: route.vehicle_id
        for route in changed.routes
        for stop in route.stops
        if type(stop) is TransferOperationStop
    }
    assert transfer_vehicle_by_role == {'out': 'vehicle-3', 'in': 'vehicle-2'}
    assert (
        sum(
            type(stop) is TransferOperationStop
            for route in changed.routes
            for stop in route.stops
        )
        == 2
    )


def test_package_chain_placement_ignores_route_path_changes(tmp_path: Path) -> None:
    _instance, _objectives, base = _package_instance(tmp_path)
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
    changed_path = replace(
        base,
        routes=(
            replace(base.routes[0], legs=(*base.routes[0].legs, RouteLeg(('a-b',)))),
            *base.routes[1:],
        ),
    )

    assert _chain_placement(changed_path, 'pkg') == _chain_placement(base, 'pkg')


def test_remove_transfer_step_restores_single_carrier_chain(tmp_path: Path) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    base = _one_transfer_plan(base)
    operator = TransferStepOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='remove',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.transfer_steps == ()
    assert not any(
        isinstance(stop, TransferOperationStop)
        for route in changed.routes
        for stop in route.stops
    )
    pickup_vehicle = next(
        route.vehicle_id
        for route in changed.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('pickup', 'pkg')
    )
    delivery_vehicle = next(
        route.vehicle_id
        for route in changed.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('delivery', 'pkg')
    )
    assert pickup_vehicle == delivery_vehicle


def test_reassign_transfer_step_changes_eligible_facility(tmp_path: Path) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    base = _one_transfer_plan(base)
    operator = TransferStepOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='reassign',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.transfer_steps[0].facility_id == 'd'


def test_reassign_transfer_step_can_change_operation_position(tmp_path: Path) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    instance = instance._replace(
        package_data=instance.package_data._replace(
            package_transfer_facilities=(PackageTransferFacilityRecord('pkg', 'c'),)
        )
    )
    base = _one_transfer_plan(base)
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            routes[0].stops[0],
            DepotOnlyStop('a', 0.0, 1.0, 0.0, False),
            routes[0].stops[1],
        ),
        legs=(
            RouteLeg(('a-b',)),
            RouteLeg(('b-a',)),
            RouteLeg(('a-c',)),
            RouteLeg(('c-a',)),
        ),
    )
    base = replace(base, routes=tuple(routes))
    operator = TransferStepOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='reassign',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    outgoing_index = next(
        index
        for route in changed.routes
        for index, stop in enumerate(route.stops)
        if isinstance(stop, TransferOperationStop) and stop.role == 'out'
    )
    assert outgoing_index == 1
    assert isinstance(changed.routes[0].stops[0], ActivityStop)
    assert changed.routes[0].stops[0].activity_ref == ActivityRef('pickup', 'pkg')


def test_reassign_intermediate_transfer_moves_downstream_sender(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    vehicles = instance.fleet_data.vehicles
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=(*vehicles, vehicles[0]._replace(vehicle_id='vehicle-3'))
        )
    )

    def route(
        vehicle_id: str,
        stops: tuple[RouteStop, ...],
        stop_nodes: tuple[str, ...],
    ) -> VehicleDayRoute:
        nodes = ('a', *stop_nodes, 'a')
        legs = tuple(
            RouteLeg(()) if tail == head else RouteLeg((f'{tail}-{head}',))
            for tail, head in zip(nodes[:-1], nodes[1:], strict=True)
        )
        return replace(
            base.routes[0],
            vehicle_id=vehicle_id,
            stops=stops,
            legs=legs,
        )

    pickup = ActivityStop(
        ActivityRef('pickup', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    first_out = TransferOperationStop(
        TransferStepRef('pkg', 1), 'out', 0.0, 0.0, 0.0, False
    )
    first_in = TransferOperationStop(
        TransferStepRef('pkg', 1), 'in', 0.0, 0.0, 0.0, False
    )
    second_out = TransferOperationStop(
        TransferStepRef('pkg', 2), 'out', 0.0, 0.0, 0.0, False
    )
    second_in = TransferOperationStop(
        TransferStepRef('pkg', 2), 'in', 0.0, 0.0, 0.0, False
    )
    delivery = ActivityStop(
        ActivityRef('delivery', 'pkg'), frozenset(), 0.0, 0.0, 0.0, False
    )
    base = replace(
        base,
        routes=(
            route(
                'vehicle-1',
                (pickup, first_out, second_in, delivery),
                ('b', 'c', 'd', 'e'),
            ),
            route('vehicle-2', (first_in, second_out), ('c', 'd')),
            route('vehicle-3', (), ()),
        ),
        transfer_steps=(
            TransferStep('pkg', 1, 'c'),
            TransferStep('pkg', 2, 'd'),
        ),
    )

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        receiver = any(
            route.vehicle_id == 'vehicle-3'
            and any(
                isinstance(stop, TransferOperationStop)
                and stop.transfer_step_ref
                in {TransferStepRef('pkg', 1), TransferStepRef('pkg', 2)}
                and (stop.transfer_step_ref.step_number, stop.role)
                in {(1, 'in'), (2, 'out')}
                for stop in route.stops
            )
            for route in solution.routes
        )
        return ApplicationEvaluation(True, True, (0.0 if receiver else 10.0,))

    operator = TransferStepOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='reassign',
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=32,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    receiver_stops = tuple(
        stop
        for route in changed.routes
        if route.vehicle_id == 'vehicle-3'
        for stop in route.stops
    )
    assert first_in in receiver_stops
    assert second_out in receiver_stops


def test_tool_reservation_relocation_moves_an_intervention(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            interventions=(InterventionRecord('repair', 'b', 1.0, 0),),
            tools=(ToolRecord('tool', 10.0),),
            intervention_tools=(InterventionToolRecord('repair', 'tool'),),
        )
    )
    routes = list(base.routes)
    routes[0] = replace(
        routes[0],
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'repair'), frozenset(), 0.0, 0.0, 0.0, False
            ),
        ),
        legs=(RouteLeg(('a-b',)), RouteLeg(('b-a',))),
    )
    base = replace(base, routes=tuple(routes))
    operator = ToolReservationGuidedInterventionRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed != base
    assert (
        sum(
            isinstance(stop, ActivityStop)
            and stop.activity_ref == ActivityRef('intervention', 'repair')
            for route in changed.routes
            for stop in route.stops
        )
        == 1
    )
