"""Tests for feature-specific schedule and depot Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from random import Random
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    LoadedVrpInstance,
    VehicleOvernightDepotRecord,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.path_search import route_node_sequence
from multiobjective_vns.multiday_vrp.operators.schedule_features import (
    DriverSwapCycleOperator,
    MultiDayDepotReassignmentOperator,
    OvernightDepotChangeOperator,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
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


class _QuarterRandomSource:
    def random(self) -> float:
        return 0.25


class _SeededRandomSource:
    def __init__(self, seed: int) -> None:
        self._random = Random(seed)

    def random(self) -> float:
        return self._random.random()


class _SequenceRandomSource:
    def __init__(self, values: tuple[float, ...]) -> None:
        self._draws = iter(values)

    def random(self) -> float:
        return next(self._draws, 0.0)


def _context(base: VrpSolution, *, k: int = 1) -> CandidateContext:
    return CandidateContext(
        base,
        k,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )


def _driver_cycle_plan(
    tmp_path: Path, route_count: int
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, original = _test_plan(tmp_path)
    routes = tuple(
        replace(
            original.routes[index % len(original.routes)],
            day_index=index // len(original.routes) + 1,
            driver_id=f'driver-{index + 1}',
        )
        for index in range(route_count)
    )
    return instance, objectives, replace(original, routes=routes)


def test_driver_swap_cycle_composes_cycles_and_keeps_route_data_fixed(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _driver_cycle_plan(tmp_path, 4)
    assessed: list[VrpSolution] = []

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        assessed.append(candidate)
        return _always_assess(_instance, _objectives, candidate)

    operator = DriverSwapCycleOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        max_neighborhood_size=4,
    )
    context = CandidateContext(
        base,
        4,
        False,
        None,
        cast(
            RandomSource,
            _SequenceRandomSource((0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 0.99)),
        ),
        max_candidates=1,
    )

    result = operator.call(context)

    assert len(result.candidates) == 1
    proposal = result.candidates[0]
    assert proposal.m == 4
    changed = cast(VrpSolution, proposal.candidate.solution)
    assert tuple(route.driver_id for route in changed.routes) == (
        'driver-2',
        'driver-1',
        'driver-4',
        'driver-3',
    )
    assert all(
        replace(original, driver_id=updated.driver_id) == updated
        for original, updated in zip(base.routes, changed.routes, strict=True)
    )
    assert assessed == [changed]


def test_driver_swap_cycle_returns_distinct_trials_in_generation_order(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _driver_cycle_plan(tmp_path, 4)
    assessed = 0

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessed
        assessed += 1
        return _always_assess(_instance, _objectives, _candidate)

    operator = DriverSwapCycleOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        max_neighborhood_size=4,
    )

    result = operator.call(
        CandidateContext(
            base,
            4,
            False,
            None,
            cast(RandomSource, _SeededRandomSource(121)),
            max_candidates=8,
        )
    )

    assert len(result.candidates) == 8
    assert assessed == len(result.candidates)
    assert all(proposal.m == 4 for proposal in result.candidates)
    assignments = tuple(
        tuple(
            route.driver_id
            for route in cast(VrpSolution, proposal.candidate.solution).routes
        )
        for proposal in result.candidates
    )
    assert len(set(assignments)) == len(assignments)
    assert all(
        set(assignment) == set(('driver-1', 'driver-2', 'driver-3', 'driver-4'))
        for assignment in assignments
    )


def test_driver_swap_cycle_returns_empty_when_assignments_cannot_change(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    assessed = 0

    def assessor(
        instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessed
        assessed += 1
        return _always_assess(instance, declaration, solution)

    operator = DriverSwapCycleOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        max_neighborhood_size=2,
    )

    result = operator.call(_context(base, k=2))

    assert result.candidates == ()
    assert assessed == 0


def test_driver_swap_cycle_respects_requested_feasibility(tmp_path: Path) -> None:
    instance, objectives, base = _driver_cycle_plan(tmp_path, 2)
    operator = DriverSwapCycleOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda *_args: ApplicationEvaluation(True, False, (0.0,)),
        ),
        max_neighborhood_size=2,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            True,
            None,
            cast(RandomSource, _SeededRandomSource(6)),
            max_candidates=4,
        )
    )

    assert result.candidates == ()


def test_overnight_depot_change_rebuilds_endpoint_path(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    base = replace(
        base,
        routes=(base.routes[0], replace(base.routes[0], day_index=3), base.routes[1]),
    )
    records = tuple(
        VehicleOvernightDepotRecord(record.vehicle_id, depot.depot_id)
        for record in instance.fleet_data.vehicles[:1]
        for depot in instance.network_data.depots
    )
    fleet = instance.fleet_data._replace(vehicle_overnight_depots=records)
    instance = instance._replace(fleet_data=fleet)
    operator = OvernightDepotChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
        max_candidate_alternatives=8,
    )

    result = operator.call(_context(base))

    assert result.candidates
    assert result.candidates[0].m == 1
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.routes[0].overnight_depot_id != base.routes[0].overnight_depot_id
    assert changed.routes[0].legs[-1] != base.routes[0].legs[-1]
    assert changed.routes[1].legs[0] != base.routes[1].legs[0]


def test_overnight_depot_change_returns_objective_ranked_alternatives(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    records = tuple(
        VehicleOvernightDepotRecord(base.routes[0].vehicle_id, depot.depot_id)
        for depot in instance.network_data.depots
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicle_overnight_depots=records)
    )
    depot_scores = {
        depot.depot_id: float(index)
        for index, depot in enumerate(instance.network_data.depots)
    }

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(
            True, True, (depot_scores[solution.routes[0].overnight_depot_id],)
        )

    operator = OvernightDepotChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, Random(17)),
        max_candidates=5,
    )

    result = operator.call(context)

    assert len(result.candidates) == 5
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(solutions)) == len(solutions)
    assert all(solution != base for solution in solutions)
    assert all(proposal.m == 1 for proposal in result.candidates)
    scores = tuple(
        depot_scores[solution.routes[0].overnight_depot_id] for solution in solutions
    )
    assert scores == tuple(sorted(scores))


def test_overnight_depot_change_returns_multiple_randomized_alternatives(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    records = tuple(
        VehicleOvernightDepotRecord(base.routes[0].vehicle_id, depot.depot_id)
        for depot in instance.network_data.depots
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicle_overnight_depots=records)
    )
    operator = OvernightDepotChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, Random(31)),
        max_candidates=4,
    )

    result = operator.call(context)

    assert len(result.candidates) == 4
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(solutions)) == len(solutions)
    assert all(solution != base for solution in solutions)
    assert all(proposal.m == 1 for proposal in result.candidates)


def test_overnight_depot_change_reassigns_up_to_k_routes(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    records = tuple(
        VehicleOvernightDepotRecord(base.routes[0].vehicle_id, depot.depot_id)
        for depot in instance.network_data.depots
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicle_overnight_depots=records)
    )
    second_route = replace(base.routes[0], day_index=2)
    base = replace(base, routes=(base.routes[0], second_route))
    operator = OvernightDepotChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=4,
        max_paths_per_leg=4,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, Random(43)),
            max_candidates=2,
        )
    )

    assert result.candidates
    assert all(proposal.m == 2 for proposal in result.candidates)


def test_overnight_depot_change_has_no_move_with_one_eligible_depot(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    assessments = 0

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return ApplicationEvaluation(True, True, (0.0,))

    operator = OvernightDepotChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=2,
    )

    result = operator.call(_context(base, k=2))

    assert result.candidates == ()
    assert assessments == 0


def test_overnight_depot_change_enforces_feasibility_and_candidate_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    records = tuple(
        VehicleOvernightDepotRecord(base.routes[0].vehicle_id, depot.depot_id)
        for depot in instance.network_data.depots
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicle_overnight_depots=records)
    )
    assessments = 0

    def infeasible_assessment(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return ApplicationEvaluation(True, False, (0.0,))

    operator = OvernightDepotChangeOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, infeasible_assessment),
        max_neighborhood_size=1,
        max_candidate_alternatives=3,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        True,
        None,
        cast(RandomSource, Random(47)),
        max_candidates=8,
    )

    result = operator.call(context)

    assert result.candidates == ()
    assert assessments <= 3


def test_multi_day_depot_reassignment_changes_a_contiguous_route_block(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    second_route = replace(base.routes[0], day_index=3)
    base = replace(base, routes=(base.routes[0], second_route))
    records = tuple(
        VehicleOvernightDepotRecord(base.routes[0].vehicle_id, depot.depot_id)
        for depot in instance.network_data.depots
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicle_overnight_depots=records)
    )
    operator = MultiDayDepotReassignmentOperator(
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
            cast(RandomSource, Random(29)),
            max_candidates=4,
        )
    )

    assert result.candidates
    assert len(result.candidates) == 4
    assert all(1 <= proposal.m <= 2 for proposal in result.candidates)
    arc_endpoints = {
        arc.arc_id: (arc.tail_node_id, arc.head_node_id)
        for arc in instance.network_data.arcs
    }
    for proposal in result.candidates:
        candidate = cast(VrpSolution, proposal.candidate.solution)
        changed_indices = tuple(
            index
            for index, route in enumerate(candidate.routes)
            if route.overnight_depot_id != base.routes[index].overnight_depot_id
        )
        assert proposal.m == len(changed_indices)
        assert 1 <= len(changed_indices) <= 2
        for route in candidate.routes:
            nodes = route_node_sequence(instance, candidate, route)
            for leg_index, leg in enumerate(route.legs):
                current_node = nodes[leg_index]
                for arc_id in leg.arc_ids:
                    tail_node, head_node = arc_endpoints[arc_id]
                    assert tail_node == current_node
                    current_node = head_node
                assert current_node == nodes[leg_index + 1]


def test_multi_day_depot_reassignment_returns_objective_ranked_batch(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    second_route = replace(base.routes[0], day_index=3)
    base = replace(base, routes=(base.routes[0], second_route))
    records = tuple(
        VehicleOvernightDepotRecord(base.routes[0].vehicle_id, depot.depot_id)
        for depot in instance.network_data.depots
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicle_overnight_depots=records)
    )
    depot_scores = {
        depot.depot_id: float(index)
        for index, depot in enumerate(instance.network_data.depots)
    }

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        score = sum(
            (position + 1) * depot_scores[route.overnight_depot_id]
            for position, route in enumerate(solution.routes)
        )
        return ApplicationEvaluation(True, True, (score,))

    operator = MultiDayDepotReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=2,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        2,
        False,
        None,
        cast(RandomSource, Random(37)),
        max_candidates=5,
    )

    result = operator.call(context)

    assert len(result.candidates) == 5
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    assert len(set(solutions)) == len(solutions)
    assert all(solution != base for solution in solutions)
    scores = tuple(
        sum(
            (position + 1) * depot_scores[route.overnight_depot_id]
            for position, route in enumerate(solution.routes)
        )
        for solution in solutions
    )
    assert scores == tuple(sorted(scores))


def test_multi_day_depot_reassignment_selects_vehicle_uniformly(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    vehicle_one_routes = (
        base.routes[0],
        replace(base.routes[0], day_index=2),
        replace(base.routes[0], day_index=3),
    )
    vehicle_two_routes = (
        base.routes[1],
        replace(base.routes[1], day_index=2),
    )
    base = replace(base, routes=(*vehicle_one_routes, *vehicle_two_routes))
    records = tuple(
        VehicleOvernightDepotRecord(vehicle.vehicle_id, depot.depot_id)
        for vehicle in instance.fleet_data.vehicles
        for depot in instance.network_data.depots
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicle_overnight_depots=records)
    )
    operator = MultiDayDepotReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
        max_candidate_alternatives=1,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _SequenceRandomSource((0.5, 0.3, 0.3, 0.3))),
        )
    )

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    changed_vehicle_ids = {
        candidate.routes[index].vehicle_id
        for index in range(len(base.routes))
        if candidate.routes[index].overnight_depot_id
        != base.routes[index].overnight_depot_id
    }
    assert changed_vehicle_ids == {base.routes[3].vehicle_id}


def test_multi_day_depot_reassignment_returns_empty_for_k_below_two(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    assessments = 0

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return ApplicationEvaluation(True, True, (0.0,))

    operator = MultiDayDepotReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=2,
    )

    result = operator.call(_context(base, k=1))

    assert result.candidates == ()
    assert assessments == 0


def test_multi_day_depot_reassignment_caps_trials_and_rejects_infeasible(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    second_route = replace(base.routes[0], day_index=3)
    base = replace(base, routes=(base.routes[0], second_route))
    records = tuple(
        VehicleOvernightDepotRecord(base.routes[0].vehicle_id, depot.depot_id)
        for depot in instance.network_data.depots
    )
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(vehicle_overnight_depots=records)
    )
    assessments = 0

    def infeasible_assessment(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        return ApplicationEvaluation(True, False, (0.0,))

    operator = MultiDayDepotReassignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, infeasible_assessment),
        max_neighborhood_size=2,
        max_candidate_alternatives=3,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            True,
            None,
            cast(RandomSource, Random(41)),
            max_candidates=8,
        )
    )

    assert result.candidates == ()
    assert assessments <= 3
