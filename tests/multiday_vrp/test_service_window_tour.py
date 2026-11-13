"""Tests for objective-guided time-window-feasible tour search.

Created: 2026-11-12T18:24:52+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from random import Random
from typing import cast

from multiobjective_vns.callbacks import Operator
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    StopScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.catalog import (
    VrpOperatorCatalogSettings,
    VrpOperatorId,
    build_default_vrp_operator_catalog,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.service_window_tour import (
    ObjectiveGuidedBacktrackingTourConstructionOperator,
    ObjectiveGuidedFeasibleDeliveryEdgeExchangeOperator,
    ObjectiveGuidedTourRegretInsertionOperator,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_spatial_time_window_route_cluster import _spatial_package_plan

_PREFERRED_ORDER = ('package-1', 'package-3', 'package-2')
_TOUR_OPERATOR_SPECS = (
    'vrp.service_windows.short_tour_regret_insertion',
    'vrp.service_windows.backtracking_tour_insertion',
    'vrp.service_windows.window_feasible_delivery_edge_exchange',
)


def _three_delivery_plan(
    root: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _spatial_package_plan(root)
    packages = {item.package_id: item for item in instance.package_data.packages}
    assignments = (('package-1', 'package-2', 'package-3'), ('package-4',))
    routes: list[VehicleDayRoute] = []
    for route, package_ids in zip(base.routes, assignments, strict=True):
        pickups = tuple(
            ActivityStop(
                ActivityRef('pickup', package_id),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            )
            for package_id in package_ids
        )
        deliveries = tuple(
            ActivityStop(
                ActivityRef('delivery', package_id),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            )
            for package_id in package_ids
        )
        stops = (*pickups, *deliveries)
        nodes = (
            'a',
            *(packages[package_id].pickup_node_id for package_id in package_ids),
            *(packages[package_id].delivery_node_id for package_id in package_ids),
            'a',
        )
        legs = tuple(
            RouteLeg(()) if left == right else RouteLeg((f'{left}-{right}',))
            for left, right in zip(nodes[:-1], nodes[1:], strict=True)
        )
        routes.append(replace(route, stops=stops, legs=legs))
    return instance, objectives, VrpSolution(tuple(routes), ())


def _delivery_order(
    solution: VrpSolution, vehicle_id: str = 'vehicle-1'
) -> tuple[str, ...]:
    route = next(item for item in solution.routes if item.vehicle_id == vehicle_id)
    return tuple(
        stop.activity_ref.activity_id
        for stop in route.stops
        if isinstance(stop, ActivityStop) and stop.activity_ref.kind == 'delivery'
    )


def _assessment(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    solution: VrpSolution,
    *,
    violate_changed_route_in_second_scenario: bool = False,
) -> ApplicationEvaluation:
    order = _delivery_order(solution)
    objective_values = [50.0] * len(objectives.objectives)
    objective_values[0] = 0.0 if order == _PREFERRED_ORDER else 50.0
    window_position = next(
        index
        for index, item in enumerate(objectives.objectives)
        if item.objective_id == instance.time_window_violation_objective_id
    )
    objective_values[window_position] = 0.0
    packages = {item.package_id: item for item in instance.package_data.packages}
    scenarios: list[VrpScenarioEvaluation] = []
    for scenario_id in ('scenario-1', 'scenario-2'):
        vehicle_days: list[VehicleDayScenarioEvaluation] = []
        for route in solution.routes:
            violation = (
                violate_changed_route_in_second_scenario
                and scenario_id == 'scenario-2'
                and route.vehicle_id == 'vehicle-1'
                and order != ('package-1', 'package-2', 'package-3')
            )
            traces = tuple(
                StopScenarioEvaluation(
                    stop_index=index,
                    stop_kind='activity',
                    node_id=(
                        packages[stop.activity_ref.activity_id].pickup_node_id
                        if stop.activity_ref.kind == 'pickup'
                        else packages[stop.activity_ref.activity_id].delivery_node_id
                    ),
                    arrival_time=2000.0 if violation else 0.0,
                    service_start=2000.0 if violation else 0.0,
                    service_finish=2000.0 if violation else 0.0,
                    departure_time=2000.0 if violation else 0.0,
                    break_start=None,
                    break_finish=None,
                    activity_ref=stop.activity_ref,
                    transfer_step_ref=None,
                    window_assessments_by_objective=(),
                )
                for index, stop in enumerate(route.stops)
                if isinstance(stop, ActivityStop)
            )
            vehicle_days.append(
                VehicleDayScenarioEvaluation(
                    route.vehicle_id,
                    route.day_index,
                    0.0,
                    3000.0,
                    traces,
                    (),
                )
            )
        scenarios.append(VrpScenarioEvaluation(scenario_id, 0.5, tuple(vehicle_days)))
    analysis = VrpOperatorAnalysis(True, tuple(scenarios), (), ())
    return ApplicationEvaluation(
        True,
        True,
        tuple(objective_values),
        analysis,
    )


def _operator_context(
    base: VrpSolution,
    *,
    k: int,
    max_candidates: int = 8,
    seed: int = 17,
) -> CandidateContext:
    return CandidateContext(
        base=base,
        base_evaluation=None,
        k=k,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, Random(seed)),
        max_candidates=max_candidates,
    )


def _route_network_cost(instance: LoadedVrpInstance, route: VehicleDayRoute) -> float:
    arc_costs: dict[str, float] = {}
    for (
        _scenario,
        arc_id,
        _day_index,
        _boundary_index,
    ), value in instance.scenario_bank.travel_times.items():
        arc_costs[arc_id] = min(arc_costs.get(arc_id, float('inf')), value)
    return sum(arc_costs[arc_id] for leg in route.legs for arc_id in leg.arc_ids)


def _operator_arguments(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assessor: VrpPlanAssessor,
    operator_type: Callable[..., Operator],
) -> Operator:
    return operator_type(
        instance,
        objectives,
        assessor,
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=4,
        max_candidate_alternatives=24,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )


def test_regret_reinsertion_ranks_by_target_objective_not_travel_time(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _three_delivery_plan(tmp_path)
    operator = _operator_arguments(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assessment),
        ObjectiveGuidedTourRegretInsertionOperator,
    )

    result = operator.call(_operator_context(base, k=1))

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert _delivery_order(candidate) == _PREFERRED_ORDER
    base_route = next(route for route in base.routes if route.vehicle_id == 'vehicle-1')
    candidate_route = next(
        route for route in candidate.routes if route.vehicle_id == 'vehicle-1'
    )
    assert _route_network_cost(instance, candidate_route) > _route_network_cost(
        instance, base_route
    )


def test_regret_reinsertion_rejects_a_changed_route_late_in_any_scenario(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _three_delivery_plan(tmp_path)

    def assessor(
        checked_instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return _assessment(
            checked_instance,
            declaration,
            solution,
            violate_changed_route_in_second_scenario=True,
        )

    operator = _operator_arguments(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        ObjectiveGuidedTourRegretInsertionOperator,
    )
    result = operator.call(_operator_context(base, k=1))

    assert result.candidates == ()


def test_backtracking_constructor_returns_an_objective_improving_feasible_tour(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _three_delivery_plan(tmp_path)
    operator = _operator_arguments(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assessment),
        ObjectiveGuidedBacktrackingTourConstructionOperator,
    )

    result = operator.call(_operator_context(base, k=3))

    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert _delivery_order(candidate) == _PREFERRED_ORDER
    assert {
        stop.activity_ref.activity_id
        for route in candidate.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop) and stop.activity_ref.kind == 'delivery'
    } == {'package-1', 'package-2', 'package-3', 'package-4'}


def test_feasible_edge_exchange_uses_objective_and_strict_scenario_filter(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _three_delivery_plan(tmp_path)
    operator = _operator_arguments(
        instance,
        objectives,
        cast(VrpPlanAssessor, _assessment),
        ObjectiveGuidedFeasibleDeliveryEdgeExchangeOperator,
    )

    result = operator.call(_operator_context(base, k=3, max_candidates=4))

    assert result.candidates
    assert (
        _delivery_order(cast(VrpSolution, result.candidates[0].candidate.solution))
        == _PREFERRED_ORDER
    )


def test_catalog_registers_tour_search_for_nonwindow_objectives_only(
    tmp_path: Path,
) -> None:
    instance, objectives, _base = _three_delivery_plan(tmp_path)
    catalog = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id='tour-search-test',
        objective_declaration=objectives,
        assess_plan=cast(VrpPlanAssessor, _assessment),
        settings=VrpOperatorCatalogSettings(
            objective_targets=tuple(item.objective_id for item in objectives.objectives)
        ),
    )

    registrations = tuple(
        cast(VrpOperatorId, item.operator_id)
        for item in catalog.operators
        if cast(VrpOperatorId, item.operator_id).operator_spec_id
        in _TOUR_OPERATOR_SPECS
    )
    assert {item.operator_spec_id for item in registrations} == set(
        _TOUR_OPERATOR_SPECS
    )
    assert {item.objective_id for item in registrations} == {
        objectives.objectives[0].objective_id
    }
    assert all(item.objective_id is not None for item in registrations)
