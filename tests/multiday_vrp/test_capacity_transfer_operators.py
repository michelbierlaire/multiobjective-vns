"""Tests for cargo, transfer, and tool-capacity repair Operators.

Created: 2026-11-04T12:39:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    InterventionToolRecord,
    LoadedVrpInstance,
    PackageRecord,
    PackageTransferFacilityRecord,
    ToolRecord,
    TransferFacilityRecord,
)
from multiobjective_vns.multiday_vrp.operators.capacity_profile import (
    capacity_excess_by_stop,
    capacity_states_by_stop,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.package_capacity import (
    CapacityAwarePackageChainRelocationOperator,
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


def _context(base: VrpSolution, *, k: int = 1) -> CandidateContext:
    return CandidateContext(
        base=base,
        base_evaluation=None,
        k=k,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, _ZeroRandomSource()),
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

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    package_occurrences = [
        (route.vehicle_id, stop.activity_ref.kind)
        for route in changed.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'pkg'
    ]
    assert {vehicle for vehicle, _kind in package_occurrences} == {'vehicle-2'}
    assert {kind for _vehicle, kind in package_occurrences} == {'pickup', 'delivery'}


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

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
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

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
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

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert changed.transfer_steps[0].facility_id == 'd'


def test_reassign_transfer_step_can_change_operation_position(tmp_path: Path) -> None:
    instance, objectives, base = _package_instance(tmp_path)
    instance = instance._replace(
        package_data=instance.package_data._replace(
            package_transfer_facilities=(PackageTransferFacilityRecord('pkg', 'c'),)
        )
    )
    base = _one_transfer_plan(base)
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

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    outgoing_index = next(
        index
        for route in changed.routes
        for index, stop in enumerate(route.stops)
        if isinstance(stop, TransferOperationStop) and stop.role == 'out'
    )
    assert outgoing_index == 0


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

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
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

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
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
