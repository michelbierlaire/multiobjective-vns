"""Tests for feature-specific activity relocation Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    VrpObjectiveFunction,
)
from multiobjective_vns.multiday_vrp.evaluation.records import (
    HardFeasibilityFinding,
    ResourceFinding,
    VrpOperatorAnalysis,
)
from multiobjective_vns.multiday_vrp.extensions import (
    StopScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
    WindowScenarioAssessment,
)
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    InterventionTimeWindowRecord,
    LoadedVrpInstance,
    VehicleOvernightDepotRecord,
    VehicleRecord,
    VrpInterventionData,
)
from multiobjective_vns.multiday_vrp.operators import activity_relocation
from multiobjective_vns.multiday_vrp.operators import (
    activity_relocation as relocation_module,
)
from multiobjective_vns.multiday_vrp.operators.activity_relocation import (
    ActivityRelocationMode,
    ActivityRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.path_search import PathSearchIndex
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
    RouteStop,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
)
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    ProcessingStatus,
)

from .test_universal_route_order import _always_assess, _test_plan


def _intervention_plan(
    tmp_path: Path,
    *,
    durations: tuple[float, ...],
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _test_plan(tmp_path)
    interventions = tuple(
        InterventionRecord(f'intervention-{index}', node_id, duration, 1)
        for index, (node_id, duration) in enumerate(
            zip(('b', 'c', 'f')[: len(durations)], durations, strict=True)
        )
    )
    empty = VrpInterventionData((), (), (), (), ())
    instance = instance._replace(
        intervention_data=empty._replace(interventions=interventions)
    )
    activity_stops = tuple(
        ActivityStop(
            ActivityRef('intervention', intervention.intervention_id),
            frozenset(),
            0.0,
            0.0,
            0.0,
            False,
        )
        for intervention in interventions
    )
    route = base.routes[0]
    nodes = ('a', *(item.node_id for item in interventions), 'a')
    legs = tuple(
        RouteLeg((f'{tail}-{head}',))
        for tail, head in zip(nodes[:-1], nodes[1:], strict=True)
    )
    routes = list(base.routes)
    routes[0] = replace(route, stops=activity_stops, legs=legs)
    return instance, objectives, replace(base, routes=tuple(routes))


def _windowed_intervention_plan(
    tmp_path: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _intervention_plan(tmp_path, durations=(1.0, 1.0))
    windows = (
        InterventionTimeWindowRecord('intervention-0', 1, 0, 0.0, 10.0),
        InterventionTimeWindowRecord('intervention-1', 1, 0, 0.0, 10.0),
    )
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            intervention_time_windows=windows
        ),
        time_window_violation_objective_id='vehicles-used',
    )
    return instance, objectives, base


def _scenario_trace(
    scenario_id: str,
    probability: float,
    guidance: tuple[float, ...],
) -> VrpScenarioEvaluation:
    stops = tuple(
        StopScenarioEvaluation(
            index,
            'activity',
            ('b', 'c', 'f')[index],
            0.0,
            0.0,
            1.0,
            1.0,
            None,
            None,
            ActivityRef('intervention', f'intervention-{index}'),
            None,
            (WindowScenarioAssessment('vehicles-used', 0, 0.0, value, value),)
            if value > 0.0
            else (),
        )
        for index, value in enumerate(guidance)
    )
    return VrpScenarioEvaluation(
        scenario_id,
        probability,
        (VehicleDayScenarioEvaluation('vehicle-1', 1, 0.0, 10.0, stops, ()),),
    )


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


def _availability_relocation_plan(
    tmp_path: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    instance, objectives, base = _test_plan(tmp_path)
    routes = (
        replace(
            base.routes[0],
            stops=(DepotOnlyStop('b', 0.25, 1.5, 0.75, False),),
            legs=(RouteLeg(('a-b',)), RouteLeg(('b-a',))),
        ),
        replace(
            base.routes[1],
            stops=(
                DepotOnlyStop('d', 0.5, 2.5, 1.25, False),
                DepotOnlyStop('e', 0.75, 3.5, 1.75, False),
            ),
            legs=(
                RouteLeg(('a-d',)),
                RouteLeg(('d-e',)),
                RouteLeg(('e-a',)),
            ),
        ),
    )
    return instance, objectives, replace(base, routes=routes)


def test_intervention_relocation_moves_activity_and_rebuilds_routes(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    intervention = InterventionRecord('repair', 'b', 10.0, 1)
    empty = VrpInterventionData((), (), (), (), ())
    instance = instance._replace(
        intervention_data=empty._replace(
            interventions=(intervention,),
            intervention_time_windows=(
                InterventionTimeWindowRecord('repair', 1, 0, 0.0, 100.0),
            ),
        )
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

    assert result.candidates[0].m == 1
    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    locations = tuple(
        (route_index, stop_index)
        for route_index, route in enumerate(changed.routes)
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop) and stop.activity_ref.activity_id == 'repair'
    )
    assert locations != ((0, 0),)


def test_intervention_relocation_samples_only_eligible_vehicle_days(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    intervention = InterventionRecord('repair', 'b', 10.0, 1)
    intervention_data = VrpInterventionData((), (), (), (), ())._replace(
        interventions=(intervention,),
        intervention_time_windows=(
            InterventionTimeWindowRecord('repair', 1, 0, 0.0, 20.0),
        ),
    )
    instance = instance._replace(intervention_data=intervention_data)
    instance = instance._replace(
        fleet_data=instance.fleet_data._replace(
            vehicles=(
                *instance.fleet_data.vehicles,
                VehicleRecord('vehicle-3', 100.0, 1, 'depot-a', 1),
            ),
            vehicle_overnight_depots=(
                *instance.fleet_data.vehicle_overnight_depots,
                VehicleOvernightDepotRecord('vehicle-3', 'depot-a'),
            ),
        )
    )
    source_route = replace(
        base.routes[0],
        stops=(
            ActivityStop(
                ActivityRef('intervention', 'repair'),
                frozenset({'team-member-1'}),
                0.0,
                0.0,
                0.0,
                False,
            ),
        ),
        legs=(RouteLeg(('a-b',)), RouteLeg(('b-a',))),
    )
    other_day_route = replace(base.routes[1], day_index=2)
    eligible_empty_route = VehicleDayRoute(
        'vehicle-3',
        1,
        'depot-a',
        0.0,
        'driver-1',
        (),
        (RouteLeg(()),),
    )
    base = replace(base, routes=(source_route, other_day_route, eligible_empty_route))
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='intervention',
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
        max_paths_per_leg=1,
        max_arc_expansion_steps=20,
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

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assigned_route = next(
        route
        for route in changed.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('intervention', 'repair')
    )
    assert assigned_route.day_index == 1
    assert assigned_route.vehicle_id == 'vehicle-3'
    moved_stop = next(
        stop
        for stop in assigned_route.stops
        if isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('intervention', 'repair')
    )
    assert moved_stop.team_member_ids == frozenset({'team-member-1'})


def test_intervention_relocation_applies_k_distinct_occurrences(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _intervention_plan(tmp_path, durations=(1.0, 1.0))
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            intervention_time_windows=(
                InterventionTimeWindowRecord('intervention-0', 1, 0, 0.0, 100.0),
                InterventionTimeWindowRecord('intervention-1', 1, 0, 0.0, 100.0),
            )
        )
    )
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='intervention',
        max_neighborhood_size=2,
        max_candidate_alternatives=8,
        max_paths_per_leg=1,
        max_arc_expansion_steps=100,
    )

    result = operator.call(
        CandidateContext(
            base,
            2,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
        )
    )

    assert result.candidates
    assert result.candidates[0].m == 2
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert {
        stop.activity_ref.activity_id
        for route in changed.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop)
    } == {'intervention-0', 'intervention-1'}


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

    assert result.candidates[0].m == 1
    assert result.candidates


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

    assert result.candidates[0].m == 1
    assert result.candidates


def test_service_order_impact_ignores_activities_without_positive_duration(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _intervention_plan(tmp_path, durations=(0.0,))
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='service_order_impact',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(
        CandidateContext(base, 1, False, None, cast(RandomSource, _ZeroRandomSource()))
    )

    assert result.candidates == ()


def test_service_order_impact_weights_downstream_guidance_by_scenario_probability(
    tmp_path: Path,
) -> None:
    from multiobjective_vns.multiday_vrp.operators.activity_relocation import (
        _downstream_window_scores,
        _eligible_occurrences,
        _positive_duration_positions,
    )

    instance, _objectives, base = _intervention_plan(tmp_path, durations=(1.0, 1.0))
    analysis = VrpOperatorAnalysis(
        True,
        (
            _scenario_trace('scenario-1', 0.25, (0.0, 2.0)),
            _scenario_trace('scenario-2', 0.75, (0.0, 1.0)),
        ),
        (),
        (),
    )

    eligible = _positive_duration_positions(
        instance,
        base,
        tuple((0, index) for index in range(len(base.routes[0].stops))),
    )
    scores = _downstream_window_scores(base, analysis, 'vehicles-used', eligible)

    assert eligible == ((0, 0), (0, 1))
    assert scores == ((1.25, (0, 0)), (0.0, (0, 1)))
    assert _eligible_occurrences(
        instance, base, analysis, 'service_order_impact', 'vehicles-used'
    ) == ((0, 0),)
    zero_guidance = VrpOperatorAnalysis(True, (), (), ())
    assert _eligible_occurrences(
        instance, base, zero_guidance, 'service_order_impact', 'vehicles-used'
    ) == ((0, 0), (0, 1))


def test_window_guided_relocation_uses_base_analysis_and_one_global_trial_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, base_objectives, base = _windowed_intervention_plan(tmp_path)
    objectives = ObjectiveDeclaration(
        (
            VrpObjectiveFunction('other', 'test-other', 'test-other-v1'),
            base_objectives.objectives[0],
        )
    )
    analysis = VrpOperatorAnalysis(
        True,
        (_scenario_trace('scenario-1', 1.0, (1.0, 5.0)),),
        (),
        (),
    )
    base_evaluation = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        ApplicationEvaluation(True, True, (0.0, 0.0), analysis),
        ApplicationAssessment.VALID,
    )
    assessments: list[VrpSolution] = []
    candidate_budgets: list[int] = []
    eligible_targets: list[tuple[tuple[int, int], ...]] = []

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        assert solution != base
        assessments.append(solution)
        return ApplicationEvaluation(True, True, (0.0, 0.0))

    original_eligible = relocation_module._eligible_occurrences

    def record_eligible(
        eligible_instance: LoadedVrpInstance,
        solution: VrpSolution,
        eligible_analysis: VrpOperatorAnalysis | None,
        mode: ActivityRelocationMode,
        objective_id: str | None,
        windowed_activity_keys: frozenset[tuple[str, str, int | None]] | None = None,
    ) -> tuple[tuple[int, int], ...]:
        result = original_eligible(
            eligible_instance,
            solution,
            eligible_analysis,
            mode,
            objective_id,
            windowed_activity_keys,
        )
        eligible_targets.append(result)
        return result

    def rebuild_one(
        _instance: LoadedVrpInstance,
        working: VrpSolution,
        stops_by_route: dict[int, tuple[RouteStop, ...]],
        *,
        max_candidate_alternatives: int,
        **_kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        candidate_budgets.append(max_candidate_alternatives)
        routes = list(working.routes)
        for route_index, stops in stops_by_route.items():
            routes[route_index] = replace(
                routes[route_index],
                stops=stops,
                legs=tuple(RouteLeg(()) for _ in range(len(stops) + 1)),
            )
        return (replace(working, routes=tuple(routes)),)

    monkeypatch.setattr(relocation_module, '_eligible_occurrences', record_eligible)
    monkeypatch.setattr(relocation_module, 'rebuild_changed_routes', rebuild_one)
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='window_guided',
        objective_id='vehicles-used',
        max_neighborhood_size=1,
        max_candidate_alternatives=4,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(
        CandidateContext(
            base,
            1,
            False,
            None,
            cast(RandomSource, _ZeroRandomSource()),
            base_evaluation,
        )
    )

    assert result.candidates
    assert eligible_targets == [((0, 1),)]
    assert len(assessments) == 4
    assert candidate_budgets == [4, 3, 2, 1]


def test_window_guided_relocation_falls_back_without_assessing_the_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _windowed_intervention_plan(tmp_path)
    selected_targets: list[tuple[int, int]] = []

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        assert solution != base
        return ApplicationEvaluation(True, True, (0.0,))

    def record_target(
        _self: ActivityRelocationOperator,
        working: VrpSolution,
        target: tuple[int, int],
        _context: CandidateContext,
        **_kwargs: object,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...], ApplicationEvaluation], ...]:
        selected_targets.append(target)
        selected = working.routes[target[0]].stops[target[1]]
        assert type(selected) is ActivityStop
        source_stops = list(working.routes[target[0]].stops)
        del source_stops[target[1]]
        destination_stops = [selected, *working.routes[1].stops]
        routes = list(working.routes)
        routes[target[0]] = replace(
            routes[target[0]],
            stops=tuple(source_stops),
            legs=tuple(RouteLeg(()) for _ in range(len(source_stops) + 1)),
        )
        routes[1] = replace(
            routes[1],
            stops=tuple(destination_stops),
            legs=tuple(RouteLeg(()) for _ in range(len(destination_stops) + 1)),
        )
        candidate = replace(working, routes=tuple(routes))
        evaluation = ApplicationEvaluation(True, True, (0.0,))
        return ((candidate, (0.0,), evaluation),)

    monkeypatch.setattr(
        ActivityRelocationOperator, '_relocation_candidates', record_target
    )
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='window_guided',
        objective_id='vehicles-used',
        max_neighborhood_size=1,
        max_candidate_alternatives=4,
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

    assert result.candidates
    assert selected_targets == [(0, 0)]


def test_service_order_impact_returns_ranked_candidates_with_global_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _intervention_plan(tmp_path, durations=(1.0,))
    assessments = 0

    def assess(*args: object) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        solution = args[2]
        assert isinstance(solution, VrpSolution)
        location = next(
            (route_index, stop_index)
            for route_index, route in enumerate(solution.routes)
            for stop_index, stop in enumerate(route.stops)
            if type(stop) is ActivityStop
        )
        return ApplicationEvaluation(
            True, True, (float(location[0] * 100 + location[1]),)
        )

    rebuilt_count = 0

    def rebuild_many(
        _instance: object,
        working: VrpSolution,
        stops_by_route: dict[int, tuple[RouteStop, ...]],
        **_kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        nonlocal rebuilt_count
        candidates: list[VrpSolution] = []
        requested_destination = next(
            route_index
            for route_index, stops in stops_by_route.items()
            if any(type(stop) is ActivityStop for stop in stops)
        )
        requested_stops = list(stops_by_route[requested_destination])
        activity = next(stop for stop in requested_stops if type(stop) is ActivityStop)
        other_stops = [
            stop for stop in requested_stops if type(stop) is not ActivityStop
        ]
        for insertion_position in (2, 0, 1, 1):
            rebuilt_count += 1
            routes = list(working.routes)
            for route_index, stops in stops_by_route.items():
                candidate_stops = stops
                if route_index == requested_destination:
                    reordered_stops = list(other_stops)
                    reordered_stops.insert(insertion_position, activity)
                    candidate_stops = tuple(reordered_stops)
                routes[route_index] = replace(
                    routes[route_index],
                    stops=candidate_stops,
                    legs=tuple(RouteLeg(()) for _ in range(len(candidate_stops) + 1)),
                    start_time=float(rebuilt_count),
                )
            candidates.append(replace(working, routes=tuple(routes)))
        return tuple(candidates)

    monkeypatch.setattr(activity_relocation, 'rebuild_changed_routes', rebuild_many)

    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='service_order_impact',
        objective_id=objectives.objectives[0].objective_id,
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
    values = tuple(
        next(
            (route_index * 100 + stop_index)
            for route_index, route in enumerate(
                cast(VrpSolution, proposal.candidate.solution).routes
            )
            for stop_index, stop in enumerate(route.stops)
            if type(stop) is ActivityStop
        )
        for proposal in result.candidates
    )
    assert values == (100, 101)
    # One base assessment plus four complete relocation plans, despite each
    # sampled insertion returning four route-rebuild alternatives.
    assert rebuilt_count == 4
    assert assessments == 5


def test_objective_targeted_relocation_returns_ranked_candidate_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, base_objectives, base = _intervention_plan(tmp_path, durations=(1.0,))
    objectives = ObjectiveDeclaration(
        (
            VrpObjectiveFunction('other', 'test-other', 'test-other-v1'),
            base_objectives.objectives[0],
        )
    )
    assessments: list[tuple[int, int]] = []
    assessed_vector_lengths: list[int] = []
    candidate_budgets: list[int] = []
    target_values = {0: 3.0, 1: 1.0, 2: 2.0}

    def assess(
        _instance: LoadedVrpInstance,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        location = next(
            (route_index, stop_index)
            for route_index, route in enumerate(solution.routes)
            for stop_index, stop in enumerate(route.stops)
            if type(stop) is ActivityStop
        )
        assessments.append(location)
        assessed_vector_lengths.append(len(declaration.objectives))
        # The registered target is objective 1. Objective 0 intentionally
        # prefers a different order to ensure candidates use the target only.
        other_value = 100.0 - location[1]
        targeted_value = target_values.get(location[1], 5.0)
        return ApplicationEvaluation(
            True,
            True,
            (other_value, targeted_value),
        )

    def rebuild_one(
        _instance: LoadedVrpInstance,
        working: VrpSolution,
        stops_by_route: dict[int, tuple[RouteStop, ...]],
        **kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        candidate_budget = kwargs.get('max_candidate_alternatives')
        assert type(candidate_budget) is int
        candidate_budgets.append(candidate_budget)
        routes = list(working.routes)
        for route_index, stops in stops_by_route.items():
            routes[route_index] = replace(
                routes[route_index],
                stops=stops,
                legs=tuple(RouteLeg(()) for _ in range(len(stops) + 1)),
            )
        return (replace(working, routes=tuple(routes)),)

    monkeypatch.setattr(activity_relocation, 'rebuild_changed_routes', rebuild_one)
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='objective_targeted',
        objective_id=base_objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=3,
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
    assert tuple(proposal.m for proposal in result.candidates) == (1, 1)
    candidate_locations = tuple(
        next(
            (route_index, stop_index)
            for route_index, route in enumerate(
                cast(VrpSolution, proposal.candidate.solution).routes
            )
            for stop_index, stop in enumerate(route.stops)
            if type(stop) is ActivityStop
        )
        for proposal in result.candidates
    )
    assert candidate_locations == ((1, 1), (1, 2))
    assert set(assessments) == {(1, 0), (1, 1), (1, 2)}
    assert set(assessed_vector_lengths) == {2}
    assert candidate_budgets == [1, 1, 1]


def test_objective_targeted_relocation_applies_up_to_k_moves(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _intervention_plan(tmp_path, durations=(1.0, 1.0))
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='objective_targeted',
        objective_id=objectives.objectives[0].objective_id,
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
            cast(RandomSource, _SequenceRandomSource((0.0, 0.5) + (0.0,) * 100)),
            max_candidates=3,
        )
    )

    assert result.candidates
    assert all(proposal.m == 2 for proposal in result.candidates)


def test_activity_relocation_reuses_path_search_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, objectives, base = _intervention_plan(tmp_path, durations=(1.0,))
    search_index = PathSearchIndex(frozenset(), {})
    build_calls = 0
    received_indexes: list[PathSearchIndex | None] = []

    def build_index(_instance: LoadedVrpInstance) -> PathSearchIndex:
        nonlocal build_calls
        build_calls += 1
        return search_index

    def rebuild_without_candidates(
        _instance: LoadedVrpInstance,
        _working: VrpSolution,
        _stops_by_route: dict[int, tuple[RouteStop, ...]],
        **kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        path_index = kwargs.get('path_search_index')
        assert path_index is None or isinstance(path_index, PathSearchIndex)
        received_indexes.append(path_index)
        return ()

    monkeypatch.setattr(activity_relocation, 'build_path_search_index', build_index)
    monkeypatch.setattr(
        activity_relocation,
        'rebuild_changed_routes',
        rebuild_without_candidates,
    )
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='objective_targeted',
        objective_id=objectives.objectives[0].objective_id,
        max_neighborhood_size=1,
        max_candidate_alternatives=3,
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

    operator.call(context)
    operator.call(context)

    assert build_calls == 1
    assert len(received_indexes) == 6
    assert all(index is search_index for index in received_indexes)


def test_service_order_impact_applies_up_to_k_relocations(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _intervention_plan(tmp_path, durations=(1.0, 1.0))
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        mode='service_order_impact',
        objective_id=objectives.objectives[0].objective_id,
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
            cast(RandomSource, _SequenceRandomSource((0.0, 0.5) + (0.0,) * 100)),
            max_candidates=3,
        )
    )

    assert result.candidates
    assert all(proposal.m == 2 for proposal in result.candidates)


def test_availability_relocation_ranks_distinct_pairs_and_uses_largest_finding(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _availability_relocation_plan(tmp_path)
    objective_id = str(objectives.objectives[0].objective_id)
    analysis = VrpOperatorAnalysis(
        True,
        (),
        (
            ResourceFinding(
                'vehicle_availability',
                'vehicle-1',
                'scenario-1',
                'vehicle-1',
                1,
                None,
                1.0,
                ((objective_id, 1.5),),
            ),
            ResourceFinding(
                'vehicle_availability',
                'vehicle-1',
                'scenario-2',
                'vehicle-1',
                1,
                None,
                1.0,
                ((objective_id, 3.0),),
            ),
            ResourceFinding(
                'vehicle_availability',
                'vehicle-2',
                'scenario-1',
                'vehicle-2',
                1,
                None,
                1.0,
                ((objective_id, 3.0),),
            ),
        ),
        (),
    )
    assessments = 0

    def score(solution: VrpSolution) -> float:
        return float(
            sum(
                (route_index + 1)
                * sum(
                    (stop_index + 1) * ord(stop.node_id[0])
                    for stop_index, stop in enumerate(route.stops)
                    if type(stop) is DepotOnlyStop
                )
                for route_index, route in enumerate(solution.routes)
            )
        )

    def assess(*args: object) -> ApplicationEvaluation:
        nonlocal assessments
        assessments += 1
        solution = args[2]
        assert isinstance(solution, VrpSolution)
        return ApplicationEvaluation(True, True, (score(solution),), analysis)

    base_assessment = ApplicationEvaluation(True, True, (score(base),), analysis)
    stored_evaluation = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        base_assessment,
        ApplicationAssessment.VALID,
    )
    operator = ActivityRelocationOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        mode='vehicle_availability',
        objective_id=objectives.objectives[0].objective_id,
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
            cast(RandomSource, _SequenceRandomSource((0.9, 0.0))),
            base_evaluation=stored_evaluation,
            max_candidates=3,
        )
    )

    assert len(result.candidates) == 3
    assert all(proposal.m == 1 for proposal in result.candidates)
    solutions = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    # vehicle-1's findings sum to 4.5, but findings are compared per scenario.
    # The largest values tie at 3.0, and the first draw selects vehicle-2.
    assert all(
        any(
            type(stop) is DepotOnlyStop and stop.node_id == 'b'
            for stop in solution.routes[0].stops
        )
        for solution in solutions
    )
    moved_stop = base.routes[1].stops[0]
    assert all(
        any(moved_stop in route.stops for route in solution.routes)
        for solution in solutions
    )
    objective = objectives.objectives[0]
    scores = tuple(score(solution) for solution in solutions)
    assert all(
        objective.compare(left, right) is not ObjectiveComparison.GREATER
        for left, right in zip(scores[:-1], scores[1:], strict=True)
    )
    assert assessments == 3


def test_availability_relocation_uses_partial_positive_findings_without_ranking(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _availability_relocation_plan(tmp_path)
    analysis = VrpOperatorAnalysis(
        False,
        (),
        (
            ResourceFinding(
                'vehicle_availability',
                'vehicle-1',
                'scenario-1',
                'vehicle-1',
                1,
                None,
                100.0,
                (),
            ),
            ResourceFinding(
                'vehicle_availability',
                'vehicle-2',
                'scenario-1',
                'vehicle-2',
                1,
                None,
                1.0,
                (),
            ),
        ),
        (),
        HardFeasibilityFinding('static_constraint', 'test-hard-failure', None),
    )
    base_evaluation = ApplicationEvaluation(True, False, (0.0,), analysis)
    stored_evaluation = EvaluationRecord(
        ProcessingStatus.COMPLETED,
        base_evaluation,
        ApplicationAssessment.VALID,
    )
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
            cast(RandomSource, _SequenceRandomSource((0.9, 0.0))),
            base_evaluation=stored_evaluation,
            max_candidates=2,
        )
    )

    assert len(result.candidates) == 2
    assert all(proposal.m == 1 for proposal in result.candidates)
    changed = tuple(
        cast(VrpSolution, proposal.candidate.solution) for proposal in result.candidates
    )
    # Uniform selection over stops on both positive routes picks vehicle-2's
    # final stop with this draw, despite vehicle-1's larger measure.
    assert any(
        type(stop) is DepotOnlyStop and stop.node_id == 'e'
        for solution in changed
        for stop in solution.routes[0].stops
    )
