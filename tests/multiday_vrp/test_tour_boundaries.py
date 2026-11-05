"""Tests for explicit multi-tour split and merge Operators.

Created: 2026-11-04T16:47:56+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    LoadedVrpInstance,
)
from multiobjective_vns.multiday_vrp.operators import (
    MergeToursOperator,
    SplitTourOperator,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def random(self) -> float:
        return 0.0


def _context(
    base: VrpSolution,
    *,
    k: int = 1,
    ensure_feasible: bool = False,
) -> CandidateContext:
    return CandidateContext(
        base,
        k,
        ensure_feasible,
        None,
        cast(RandomSource, _ZeroRandomSource()),
    )


def _set_tour_limit(instance: LoadedVrpInstance, maximum: int) -> LoadedVrpInstance:
    vehicles = tuple(
        vehicle._replace(max_tours_per_day=maximum)
        for vehicle in instance.fleet_data.vehicles
    )
    return instance._replace(fleet_data=instance.fleet_data._replace(vehicles=vehicles))


def _mark_all_depot_stops(solution: VrpSolution) -> VrpSolution:
    routes = tuple(
        replace(
            route,
            stops=tuple(replace(stop, tour_end=True) for stop in route.stops),
        )
        for route in solution.routes
    )
    return replace(solution, routes=routes)


def _tour_count(solution: VrpSolution) -> int:
    return sum(
        1 + sum(stop.tour_end for stop in route.stops) for route in solution.routes
    )


def _use_intervention_at_depot(
    instance: LoadedVrpInstance, base: VrpSolution
) -> tuple[LoadedVrpInstance, VrpSolution]:
    intervention = InterventionRecord('intervention-1', 'b', 0.0, 1)
    intervention_data = instance.intervention_data._replace(
        interventions=(intervention,)
    )
    route = base.routes[0]
    stops = (
        ActivityStop(
            ActivityRef('intervention', 'intervention-1'),
            frozenset(),
            0.0,
            0.0,
            0.0,
            False,
        ),
        *route.stops[1:],
    )
    routes = (replace(route, stops=stops), *base.routes[1:])
    return (
        instance._replace(intervention_data=intervention_data),
        replace(base, routes=routes),
    )


def test_split_tour_marks_an_existing_intermediate_depot_visit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 2)
    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=3,
        max_candidate_alternatives=1,
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert _tour_count(changed) == _tour_count(base) + 1
    assert any(stop.tour_end for stop in changed.routes[0].stops)


def test_split_tour_can_insert_and_mark_a_new_depot_visit(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 5)
    base = _mark_all_depot_stops(base)
    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert _tour_count(changed) == _tour_count(base) + 1
    assert (
        sum(len(route.stops) for route in changed.routes)
        == sum(len(route.stops) for route in base.routes) + 1
    )
    assert sum(stop.tour_end for route in changed.routes for stop in route.stops) == (
        sum(stop.tour_end for route in base.routes for stop in route.stops) + 1
    )


def test_split_tour_can_mark_an_activity_stop_at_a_depot(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance, base = _use_intervention_at_depot(instance, base)
    instance = _set_tour_limit(instance, 2)
    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
    )

    result = operator.call(_context(base))

    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert isinstance(changed.routes[0].stops[0], ActivityStop)
    assert changed.routes[0].stops[0].tour_end is True


def test_split_tour_returns_no_move_at_the_vehicle_tour_limit(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 1)
    operator = SplitTourOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
    )

    result = operator.call(_context(base))

    assert result.m == 0
    assert result.candidate is None


def test_merge_tours_clears_boundary_and_preserves_depot_stop(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 3)
    base = _mark_all_depot_stops(base)
    original_stops = base.routes[0].stops
    operator = MergeToursOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=2,
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert _tour_count(changed) == _tour_count(base) - 1
    assert len(changed.routes[0].stops) == len(original_stops)
    assert (
        tuple(replace(stop, tour_end=True) for stop in changed.routes[0].stops)
        == original_stops
    )


def test_merge_tours_can_clear_boundary_on_activity_at_depot(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance, base = _use_intervention_at_depot(instance, base)
    instance = _set_tour_limit(instance, 3)
    route = base.routes[0]
    stops = (replace(route.stops[0], tour_end=True), *route.stops[1:])
    base = replace(base, routes=(replace(route, stops=stops), *base.routes[1:]))
    operator = MergeToursOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
    )

    result = operator.call(_context(base))

    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert isinstance(changed.routes[0].stops[0], ActivityStop)
    assert changed.routes[0].stops[0].tour_end is False


def test_merge_tours_objective_variant_selects_best_boundary(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    instance = _set_tour_limit(instance, 2)
    base = _mark_all_depot_stops(base)

    def assess(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(
            True,
            True,
            (float(candidate.routes[0].stops[0].tour_end),),
        )

    operator = MergeToursOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=8,
    )

    result = operator.call(_context(base))

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    assert changed.routes[0].stops[0].tour_end is False
