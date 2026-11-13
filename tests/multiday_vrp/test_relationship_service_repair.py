"""Tests for activity-relationship-guided service repair.

Created: 2026-11-04T09:43:07+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import multiobjective_vns.multiday_vrp.operators.relationship_repair as relationship_repair
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    StopScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import (
    CoLocationRecord,
    InterventionRecord,
    LoadedVrpInstance,
    PackageRecord,
    PrecedenceRecord,
    VrpActivityRelationData,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.relationship_repair import (
    RelationshipGuidedServiceRepairOperator,
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

from .test_universal_route_order import _test_plan


class _FirstRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 10000)

    def random(self) -> float:
        return next(self._draws)


class _SelectedRandomSource:
    def __init__(self, draws: tuple[float, ...]) -> None:
        self._draws: Iterator[float] = iter(draws)

    def random(self) -> float:
        return next(self._draws, 0.0)


def _relationship_plan(
    root: Path,
    *,
    service_coverage_mode: str = 'mandatory',
    precedence: tuple[PrecedenceRecord, ...] = (),
    co_location: tuple[CoLocationRecord, ...] = (),
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _test_plan(root)
    instance = instance._replace(
        settings=instance.settings._replace(
            service_coverage_mode=service_coverage_mode
        ),
        intervention_data=instance.intervention_data._replace(
            interventions=(
                InterventionRecord('a', 'b', 0.0, 1),
                InterventionRecord('b', 'c', 0.0, 1),
            )
        ),
        activity_relations=VrpActivityRelationData(precedence, co_location),
    )
    route = base.routes[0]
    stops = (
        ActivityStop(
            ActivityRef('intervention', 'b'), frozenset(), 0.0, 0.0, 0.0, False
        ),
        ActivityStop(
            ActivityRef('intervention', 'a'), frozenset(), 0.0, 0.0, 0.0, False
        ),
    )
    return (
        instance,
        objectives,
        replace(base, routes=(replace(route, stops=stops), *base.routes[1:])),
    )


def _scenario_analysis(
    solution: VrpSolution,
    *,
    route_start_by_vehicle: dict[str, float] | None = None,
) -> VrpOperatorAnalysis:
    vehicle_days = []
    for route_index, route in enumerate(solution.routes):
        route_start = (
            route_index * 100.0
            if route_start_by_vehicle is None
            else route_start_by_vehicle.get(route.vehicle_id, route_index * 100.0)
        )
        stops = tuple(
            StopScenarioEvaluation(
                stop_index,
                'activity',
                stop.activity_ref.activity_id,
                float(route_start + stop_index * 10),
                float(route_start + stop_index * 10),
                float(route_start + stop_index * 10 + 1),
                float(route_start + stop_index * 10 + 1),
                None,
                None,
                stop.activity_ref,
                None,
                (),
            )
            for stop_index, stop in enumerate(route.stops)
            if isinstance(stop, ActivityStop)
        )
        vehicle_days.append(
            VehicleDayScenarioEvaluation(
                route.vehicle_id,
                route.day_index,
                route.start_time,
                route.start_time,
                stops,
                (),
            )
        )
    return VrpOperatorAnalysis(
        True,
        (VrpScenarioEvaluation('scenario-1', 1.0, tuple(vehicle_days)),),
        (),
        (),
    )


def test_relationship_repair_restores_precedence_from_an_infeasible_base(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _relationship_plan(
        tmp_path,
        precedence=(PrecedenceRecord('intervention', 'a', 'intervention', 'b'),),
    )

    def assessor(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        positions = {
            stop.activity_ref.activity_id: (route_index, stop_index)
            for route_index, route in enumerate(solution.routes)
            for stop_index, stop in enumerate(route.stops)
            if isinstance(stop, ActivityStop)
        }
        feasible = (
            'a' in positions
            and 'b' in positions
            and positions['a'][0] == positions['b'][0]
            and positions['a'][1] < positions['b'][1]
        )
        return ApplicationEvaluation(
            True, feasible, (0.0,), _scenario_analysis(solution)
        )

    operator = RelationshipGuidedServiceRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        max_neighborhood_size=3,
        max_candidate_alternatives=16,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )
    result = operator.call(
        CandidateContext(
            base,
            1,
            True,
            None,
            cast(RandomSource, _FirstRandomSource()),
        )
    )

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    positions = {
        stop.activity_ref.activity_id: (route_index, stop_index)
        for route_index, route in enumerate(changed.routes)
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop)
    }
    assert positions['a'][0] == positions['b'][0]
    assert positions['a'][1] < positions['b'][1]


def test_relationship_repair_inserts_missing_intervention_prerequisite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _relationship_plan(
        tmp_path,
        precedence=(PrecedenceRecord('intervention', 'a', 'intervention', 'b'),),
    )
    route = replace(
        base.routes[0],
        stops=(base.routes[0].stops[0],),
        legs=(RouteLeg(()), RouteLeg(())),
    )
    base = replace(base, routes=(route, *base.routes[1:]))

    def rebuild(
        _instance: object,
        working: VrpSolution,
        stops_by_route: dict[int, tuple[RouteStop, ...]],
        **_kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        routes = list(working.routes)
        for route_index, stops in stops_by_route.items():
            routes[route_index] = replace(
                routes[route_index],
                stops=stops,
                legs=tuple(RouteLeg(()) for _ in range(len(stops) + 1)),
            )
        return (replace(working, routes=tuple(routes)),)

    monkeypatch.setattr(relationship_repair, 'rebuild_changed_routes', rebuild)
    operator = RelationshipGuidedServiceRepairOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda _instance, _objectives, solution: ApplicationEvaluation(
                True, False, (0.0,), _scenario_analysis(solution)
            ),
        ),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _FirstRandomSource()),
        )
    )

    assert result.candidates
    assert result.candidates[0].m == 1
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    prerequisite = next(
        stop
        for route in changed.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('intervention', 'a')
    )
    assert prerequisite.team_member_ids == frozenset({'driver-1'})
    positions = {
        stop.activity_ref.activity_id: stop_index
        for route in changed.routes
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop)
    }
    assert positions['a'] < positions['b']


def test_relationship_repair_can_resolve_optional_colocation_by_omission(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _relationship_plan(
        tmp_path,
        service_coverage_mode='penalized_omission',
        co_location=(CoLocationRecord('intervention', 'a', 'intervention', 'b'),),
    )
    operator = RelationshipGuidedServiceRepairOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda *_args: ApplicationEvaluation(True, True, (0.0,)),
        ),
        max_neighborhood_size=2,
        max_candidate_alternatives=16,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _FirstRandomSource()),
        )
    )

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    activities = tuple(
        stop.activity_ref.activity_id
        for route in changed.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop)
    )
    assert len(activities) == 1


def test_precedence_repair_returns_only_candidates_satisfying_scenario_timing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _relationship_plan(
        tmp_path,
        precedence=(PrecedenceRecord('intervention', 'a', 'intervention', 'b'),),
    )

    def rebuild(
        _instance: object,
        working: VrpSolution,
        stops_by_route: dict[int, tuple[RouteStop, ...]],
        **_kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        routes = list(working.routes)
        for route_index, stops in stops_by_route.items():
            routes[route_index] = replace(
                routes[route_index],
                stops=stops,
                legs=tuple(RouteLeg(()) for _ in range(len(stops) + 1)),
            )
        return (replace(working, routes=tuple(routes)),)

    def assessor(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, False, (0.0,), _scenario_analysis(solution))

    monkeypatch.setattr(relationship_repair, 'rebuild_changed_routes', rebuild)
    operator = RelationshipGuidedServiceRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        max_neighborhood_size=1,
        max_candidate_alternatives=16,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )
    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _SelectedRandomSource((0.0, 0.0, 0.0, 0.99))),
        )
    )

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    positions = {
        stop.activity_ref.activity_id: (route_index, stop_index)
        for route_index, route in enumerate(changed.routes)
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop)
    }
    assert positions['a'][0] == positions['b'][0]
    assert positions['a'][1] < positions['b'][1]


def test_precedence_repair_filters_infeasible_candidates_before_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _relationship_plan(
        tmp_path,
        precedence=(PrecedenceRecord('intervention', 'a', 'intervention', 'b'),),
    )

    def rebuild(
        _instance: object,
        working: VrpSolution,
        stops_by_route: dict[int, tuple[RouteStop, ...]],
        **_kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        routes = list(working.routes)
        for route_index, stops in stops_by_route.items():
            routes[route_index] = replace(
                routes[route_index],
                stops=stops,
                legs=tuple(RouteLeg(()) for _ in range(len(stops) + 1)),
            )
        return (replace(working, routes=tuple(routes)),)

    def assessor(
        _instance: object, _objectives: object, solution: VrpSolution
    ) -> ApplicationEvaluation:
        positions = {
            stop.activity_ref.activity_id: (route_index, stop_index)
            for route_index, route in enumerate(solution.routes)
            for stop_index, stop in enumerate(route.stops)
            if isinstance(stop, ActivityStop)
        }
        feasible = (
            'a' in positions
            and 'b' in positions
            and positions['a'][0] == positions['b'][0]
            and positions['a'][1] < positions['b'][1]
        )
        analysis = _scenario_analysis(
            solution,
            route_start_by_vehicle={'vehicle-1': 100.0, 'vehicle-2': 0.0},
        )
        return ApplicationEvaluation(True, feasible, (0.0,), analysis)

    monkeypatch.setattr(relationship_repair, 'rebuild_changed_routes', rebuild)
    operator = RelationshipGuidedServiceRepairOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        max_neighborhood_size=1,
        max_candidate_alternatives=16,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )
    result = operator.call(
        CandidateContext(
            base,
            1,
            True,
            None,
            cast(RandomSource, _SelectedRandomSource((0.0, 0.0, 0.0, 0.99))),
        )
    )

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    positions = {
        stop.activity_ref.activity_id: (route_index, stop_index)
        for route_index, route in enumerate(changed.routes)
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop)
    }
    assert positions['a'][0] == positions['b'][0]
    assert positions['a'][1] < positions['b'][1]


def test_relationship_repair_applies_k_distinct_pairs_sequentially(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = PrecedenceRecord('intervention', 'a', 'intervention', 'b')
    second = PrecedenceRecord('intervention', 'c', 'intervention', 'd')
    instance, objectives, base = _relationship_plan(
        tmp_path, precedence=(first, second)
    )
    interventions = (
        *instance.intervention_data.interventions,
        InterventionRecord('c', 'd', 0.0, 1),
        InterventionRecord('d', 'e', 0.0, 1),
    )
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            interventions=interventions
        )
    )
    routes = list(base.routes)
    for route_index, activity_ids in ((0, ('b', 'a')), (1, ('d', 'c'))):
        stops = tuple(
            ActivityStop(
                ActivityRef('intervention', activity_id),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            )
            for activity_id in activity_ids
        )
        routes[route_index] = replace(
            routes[route_index],
            stops=stops,
            legs=tuple(RouteLeg(()) for _ in range(len(stops) + 1)),
        )
    base = replace(base, routes=tuple(routes))

    def rebuild(
        _instance: object,
        working: VrpSolution,
        stops_by_route: dict[int, tuple[RouteStop, ...]],
        **_kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        rebuilt_routes = list(working.routes)
        for route_index, stops in stops_by_route.items():
            rebuilt_routes[route_index] = replace(
                rebuilt_routes[route_index],
                stops=stops,
                legs=tuple(RouteLeg(()) for _ in range(len(stops) + 1)),
            )
        return (replace(working, routes=tuple(rebuilt_routes)),)

    monkeypatch.setattr(relationship_repair, 'rebuild_changed_routes', rebuild)
    operator = RelationshipGuidedServiceRepairOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda _instance, _objectives, solution: ApplicationEvaluation(
                True, False, (0.0,), _scenario_analysis(solution)
            ),
        ),
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )
    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _FirstRandomSource()),
        )
    )

    assert result.candidates
    assert result.candidates[0].m == 2
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    positions = {
        stop.activity_ref.activity_id: (route_index, stop_index)
        for route_index, route in enumerate(changed.routes)
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop)
    }
    assert positions['a'][0] == positions['b'][0]
    assert positions['a'][1] < positions['b'][1]
    times = _scenario_analysis(changed).scenario_evaluations[0].vehicle_days
    service_starts = {
        stop.activity_ref.activity_id: stop.service_start
        for route in times
        for stop in route.stops
        if stop.activity_ref is not None and stop.service_start is not None
    }
    service_finishes = {
        stop.activity_ref.activity_id: stop.service_finish
        for route in times
        for stop in route.stops
        if stop.activity_ref is not None and stop.service_finish is not None
    }
    assert service_finishes['c'] <= service_starts['d']


def test_omitting_package_delivery_keeps_pickup_custody_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _relationship_plan(
        tmp_path,
        service_coverage_mode='penalized_omission',
        co_location=(CoLocationRecord('delivery', 'p', 'intervention', 'a'),),
    )
    package = PackageRecord('p', 'b', 'c', 1.0, 0.0, 0.0, 1, 0.0, 0.0)
    instance = instance._replace(
        package_data=instance.package_data._replace(packages=(package,))
    )
    step_ref = TransferStepRef('p', 1)
    custody_stops = (
        ActivityStop(ActivityRef('pickup', 'p'), frozenset(), 0.0, 0.0, 0.0, False),
        TransferOperationStop(step_ref, 'out', 0.0, 0.0, 0.0, False),
        TransferOperationStop(step_ref, 'in', 0.0, 0.0, 0.0, False),
        ActivityStop(ActivityRef('delivery', 'p'), frozenset(), 0.0, 0.0, 0.0, False),
        ActivityStop(
            ActivityRef('intervention', 'a'), frozenset(), 0.0, 0.0, 0.0, False
        ),
    )
    route = replace(
        base.routes[0],
        stops=custody_stops,
        legs=tuple(RouteLeg(()) for _ in range(len(custody_stops) + 1)),
    )
    base = replace(
        base,
        routes=(route, *base.routes[1:]),
        transfer_steps=(TransferStep('p', 1, 'd'),),
    )

    def rebuild(
        _instance: object,
        working: VrpSolution,
        stops_by_route: dict[int, tuple[RouteStop, ...]],
        **_kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        routes = list(working.routes)
        for route_index, stops in stops_by_route.items():
            routes[route_index] = replace(
                routes[route_index],
                stops=stops,
                legs=tuple(RouteLeg(()) for _ in range(len(stops) + 1)),
            )
        return (replace(working, routes=tuple(routes)),)

    monkeypatch.setattr(relationship_repair, 'rebuild_changed_routes', rebuild)
    operator = RelationshipGuidedServiceRepairOperator(
        instance,
        objectives,
        cast(
            VrpPlanAssessor,
            lambda *_args: ApplicationEvaluation(True, True, (0.0,)),
        ),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )
    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _FirstRandomSource()),
        )
    )

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert any(
        isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('pickup', 'p')
        for route in changed.routes
        for stop in route.stops
    )
    assert not any(
        isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('delivery', 'p')
        for route in changed.routes
        for stop in route.stops
    )
    assert (
        sum(
            isinstance(stop, TransferOperationStop)
            and stop.transfer_step_ref.package_id == 'p'
            for route in changed.routes
            for stop in route.stops
        )
        == 2
    )
    assert changed.transfer_steps == (TransferStep('p', 1, 'd'),)
