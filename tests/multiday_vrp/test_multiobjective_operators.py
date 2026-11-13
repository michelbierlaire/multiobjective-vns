"""Tests for Pareto trade-off VRP Operators.

Created: 2026-11-04T09:03:56+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    LoadedVrpInstance,
    VrpInterventionData,
)
from multiobjective_vns.multiday_vrp.operators import pareto_tradeoff as tradeoff_module
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.pareto_tradeoff import (
    ParetoTradeoffOperator,
)
from multiobjective_vns.multiday_vrp.operators.path_search import PathSearchIndex
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    RouteStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _test_plan


@dataclass(frozen=True, slots=True)
class _Objective:
    objective_id: str
    component_id: str
    configuration_id: str

    def compare(self, left: object, right: object) -> ObjectiveComparison:
        left_value = cast(float, left)
        right_value = cast(float, right)
        if left_value < right_value:
            return ObjectiveComparison.LESS
        if left_value > right_value:
            return ObjectiveComparison.GREATER
        return ObjectiveComparison.EQUAL

    def format(self, value: object) -> str:
        return str(value)

    def __str__(self) -> str:
        return self.objective_id


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 20000)

    def random(self) -> float:
        return next(self._draws)


class _SequenceRandomSource:
    def __init__(self, draws: tuple[float, ...]) -> None:
        self._draws = iter(draws)

    def random(self) -> float:
        return next(self._draws, 0.0)


def _two_objectives() -> ObjectiveDeclaration:
    return ObjectiveDeclaration(
        (
            _Objective('time', 'test:time', 'test:time-v1'),
            _Objective('risk', 'test:risk', 'test:risk-v1'),
        )
    )


def _context(
    solution: VrpSolution, *, k: int = 1, max_candidates: int = 1
) -> CandidateContext:
    return CandidateContext(
        solution,
        k,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
        max_candidates=max_candidates,
    )


def _activity_plan(
    tmp_path: Path, activity_count: int
) -> tuple[LoadedVrpInstance, VrpSolution]:
    instance, _objectives, base = _test_plan(tmp_path)
    nodes = ('b', 'c')[:activity_count]
    interventions = tuple(
        InterventionRecord(f'pareto-activity-{index}', node_id, 10.0, 1)
        for index, node_id in enumerate(nodes)
    )
    instance = instance._replace(
        intervention_data=instance.intervention_data._replace(
            interventions=interventions
        )
    )
    routes = list(base.routes)
    route = routes[0]
    activity_stops = tuple(
        ActivityStop(
            ActivityRef('intervention', f'pareto-activity-{index}'),
            frozenset(),
            0.0,
            0.0,
            0.0,
            False,
        )
        for index in range(activity_count)
    )
    routes[0] = replace(
        route,
        stops=activity_stops + route.stops[activity_count:],
    )
    return instance, replace(base, routes=tuple(routes))


def test_pareto_timing_move_selects_from_nondominated_alternatives(
    tmp_path: Path,
) -> None:
    instance, _objectives, base = _test_plan(tmp_path)
    objectives = _two_objectives()
    routes = list(base.routes)
    routes[0] = replace(routes[0], start_time=50.0)
    base = replace(base, routes=tuple(routes))

    def assessor(
        _instance: object,
        _objectives: object,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        start_time = solution.routes[0].start_time
        if start_time < 50.0:
            values = (0.0, 2.0)
        elif start_time > 50.0:
            values = (2.0, 0.0)
        else:
            values = (1.0, 1.0)
        return ApplicationEvaluation(True, True, values)

    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='timing',
        max_neighborhood_size=2,
        time_step=1.0,
        max_candidate_alternatives=8,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert candidate.routes[0].start_time in {49.0, 51.0}


def test_pareto_timing_move_returns_randomized_nondominated_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _single_objective, base = _test_plan(tmp_path)
    objectives = _two_objectives()
    routes = list(base.routes)
    first_stop = routes[0].stops[0]
    assert isinstance(first_stop, DepotOnlyStop)
    routes[0] = replace(
        routes[0],
        start_time=50.0,
        stops=(replace(first_stop, wait=4.0, break_duration=1.0), *routes[0].stops[1:]),
    )
    base = replace(base, routes=tuple(routes))
    targets = (
        (0, None, 'start_time'),
        (0, 0, 'wait'),
        (0, 0, 'break_duration'),
    )
    candidate_states: list[tuple[float, float, float]] = []

    def timing_targets(
        _solution: VrpSolution, route_index: int, _time_step: float
    ) -> tuple[tuple[int, int | None, str], ...]:
        return targets if route_index == 0 else ()

    def assessor(
        _instance: object,
        declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        route = solution.routes[0]
        stop = route.stops[0]
        assert isinstance(stop, DepotOnlyStop)
        state = (route.start_time, stop.wait, stop.break_duration)
        candidate_states.append(state)
        if route.start_time < 50.0:
            values = (1.0, 4.0)
        elif route.start_time > 50.0:
            values = (4.0, 1.0)
        elif stop.wait != 4.0:
            values = (2.0, 2.0)
        else:
            values = (5.0, 5.0)
        assert len(declaration.objectives) == 2
        return ApplicationEvaluation(True, True, values)

    monkeypatch.setattr(tradeoff_module, '_timing_targets', timing_targets)
    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='timing',
        max_neighborhood_size=2,
        time_step=1.0,
        max_candidate_alternatives=6,
    )
    # The first six draws choose start-/+, wait-/+, and break-/>+ in order;
    # the final zero draws keep the four-member frontier's generation order.
    rng = _SequenceRandomSource(
        (0.0, 0.0, 0.0, 0.999, 0.5, 0.0, 0.5, 0.999, 0.999, 0.0, 0.999, 0.999)
        + (0.0, 0.0, 0.0)
    )
    context = CandidateContext(
        base, 1, False, None, cast(RandomSource, rng), max_candidates=8
    )

    result = operator.call(context)

    assert len(candidate_states) == 6
    assert len(result.candidates) == 4
    assert tuple(proposal.m for proposal in result.candidates) == (1, 1, 1, 1)
    states = tuple(
        (
            cast(VrpSolution, proposal.candidate.solution).routes[0].start_time,
            cast(
                DepotOnlyStop,
                cast(VrpSolution, proposal.candidate.solution).routes[0].stops[0],
            ).wait,
            cast(
                DepotOnlyStop,
                cast(VrpSolution, proposal.candidate.solution).routes[0].stops[0],
            ).break_duration,
        )
        for proposal in result.candidates
    )
    assert states == (
        (49.0, 4.0, 1.0),
        (51.0, 4.0, 1.0),
        (50.0, 3.0, 1.0),
        (50.0, 5.0, 1.0),
    )


def test_pareto_timing_move_repeats_a_field_within_size_k(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _single_objective, base = _test_plan(tmp_path)
    objectives = _two_objectives()
    routes = list(base.routes)
    routes[0] = replace(routes[0], start_time=50.0)
    base = replace(base, routes=tuple(routes))

    def start_time_only(
        _solution: VrpSolution, route_index: int, _time_step: float
    ) -> tuple[tuple[int, int | None, str], ...]:
        return ((0, None, 'start_time'),) if route_index == 0 else ()

    def assessor(
        _instance: object,
        _objectives: object,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        start_time = solution.routes[0].start_time
        return ApplicationEvaluation(True, True, (start_time, -start_time))

    monkeypatch.setattr(tradeoff_module, '_timing_targets', start_time_only)
    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='timing',
        max_neighborhood_size=2,
        max_candidate_alternatives=1,
    )

    result = operator.call(_context(base, k=2))

    assert len(result.candidates) == 1
    assert result.candidates[0].m == 2
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert candidate.routes[0].start_time == 48.0


def test_timing_target_domain_scan_matches_complete_adjustment_checks(
    tmp_path: Path,
) -> None:
    _instance, _objectives, solution = _test_plan(tmp_path)
    route_index = 0
    time_step = 1.0
    expected: list[tuple[int, int | None, str]] = [(route_index, None, 'start_time')]
    for stop_index, stop in enumerate(solution.routes[route_index].stops):
        fields = (
            ('wait', 'break_duration')
            if isinstance(stop, DepotOnlyStop)
            else ('pre_service_wait', 'wait', 'break_duration')
        )
        for field in fields:
            if any(
                tradeoff_module._adjust_timing(
                    solution, (route_index, stop_index, field), delta
                )
                is not None
                for delta in (-time_step, time_step)
            ):
                expected.append((route_index, stop_index, field))

    assert tradeoff_module._timing_targets(solution, route_index, time_step) == tuple(
        expected
    )


def test_pareto_path_move_returns_a_complete_changed_plan(tmp_path: Path) -> None:
    instance, _single_objective, base = _test_plan(tmp_path)
    objectives = _two_objectives()

    def assessor(
        _instance: object,
        _objectives: object,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        length = float(len(solution.routes[0].legs[0].arc_ids))
        return ApplicationEvaluation(True, True, (length, -length))

    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='path',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert candidate.routes[0].legs[0] != base.routes[0].legs[0]


def test_pareto_path_move_returns_randomized_nondominated_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _single_objective, base = _test_plan(tmp_path)
    objectives = _two_objectives()
    current_path = base.routes[0].legs[0].arc_ids
    path_a = ('a-c', 'c-b')
    path_b = ('a-d', 'd-b')
    path_c = ('a-e', 'e-b')
    path_d = ('a-f', 'f-b')
    dominated_path = ('a-g', 'g-b')
    values_by_path: dict[tuple[str, ...], tuple[float, float]] = {
        path_a: (1.0, 4.0),
        path_b: (2.0, 2.0),
        path_c: (4.0, 1.0),
        path_d: (2.0, 2.0),
        dominated_path: (5.0, 5.0),
    }

    def sample_paths(*_args: object, **_kwargs: object) -> tuple[tuple[str, ...], ...]:
        return (current_path, path_a, path_b, path_c, path_d, dominated_path)

    def assessor(
        _instance: object,
        _objectives: object,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        path = solution.routes[0].legs[0].arc_ids
        values = (0.0, 0.0) if path == current_path else values_by_path[path]
        return ApplicationEvaluation(True, True, values)

    monkeypatch.setattr(tradeoff_module, 'sample_loopless_paths', sample_paths)
    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='path',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
    )
    rng = _SequenceRandomSource((0.0,) * 6 + (0.999,) * 3)
    context = CandidateContext(
        base, 1, False, None, cast(RandomSource, rng), max_candidates=8
    )
    assert assessor(instance, objectives, base).objective_values == (0.0, 0.0)

    result = operator.call(context)

    assert len(result.candidates) == 4
    assert tuple(proposal.m for proposal in result.candidates) == (1, 1, 1, 1)
    candidate_paths = tuple(
        cast(VrpSolution, proposal.candidate.solution).routes[0].legs[0].arc_ids
        for proposal in result.candidates
    )
    assert candidate_paths == (path_d, path_a, path_b, path_c)
    assert dominated_path not in candidate_paths
    assert candidate_paths.count(path_b) == 1
    assert candidate_paths.count(path_d) == 1
    assert all(
        all(value > 0.0 for value in values) for values in values_by_path.values()
    )


def test_pareto_path_move_reports_sequential_move_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance, _single_objective, base = _test_plan(tmp_path)
    objectives = _two_objectives()

    def sample_paths(
        _instance: object,
        origin: str,
        destination: str,
        **_kwargs: object,
    ) -> tuple[tuple[str, ...], ...]:
        return (
            (f'{origin}-{destination}-alternative-a',),
            (f'{origin}-{destination}-alternative-b',),
        )

    def assessor(
        _instance: object,
        _objectives: object,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (0.0, 0.0))

    monkeypatch.setattr(tradeoff_module, 'sample_loopless_paths', sample_paths)
    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='path',
        max_neighborhood_size=2,
        max_candidate_alternatives=2,
    )

    result = operator.call(_context(base, k=2, max_candidates=2))

    assert len(result.candidates) == 2
    assert all(proposal.m == 2 for proposal in result.candidates)


def test_pareto_path_move_reuses_path_search_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _single_objective, base = _test_plan(tmp_path)
    objectives = _two_objectives()
    search_index = PathSearchIndex(frozenset(), {})
    build_calls = 0
    sampled_indexes: list[PathSearchIndex | None] = []

    def build_index(_instance: object) -> PathSearchIndex:
        nonlocal build_calls
        build_calls += 1
        return search_index

    def sample_paths(
        _instance: object,
        _origin: str,
        _destination: str,
        *,
        path_search_index: PathSearchIndex | None = None,
        **_kwargs: object,
    ) -> tuple[tuple[str, ...], ...]:
        sampled_indexes.append(path_search_index)
        return (('alternative',),)

    def assessor(
        _instance: object,
        _objectives: object,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (0.0, 0.0))

    monkeypatch.setattr(tradeoff_module, 'build_path_search_index', build_index)
    monkeypatch.setattr(tradeoff_module, 'sample_loopless_paths', sample_paths)
    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='path',
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
    )

    operator.call(_context(base))
    operator.call(_context(base))

    assert build_calls == 1
    assert len(sampled_indexes) == 2
    assert all(index is search_index for index in sampled_indexes)


def test_pareto_activity_move_returns_nondominated_candidate_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, base = _activity_plan(tmp_path, 1)
    objectives = _two_objectives()
    objective_values = {
        0.0: (0.0, 0.0),
        10.0: (1.0, 4.0),
        11.0: (2.0, 2.0),
        12.0: (4.0, 1.0),
        13.0: (2.0, 2.0),
        14.0: (5.0, 5.0),
    }

    def insertion_positions(
        _solution: VrpSolution, _source: tuple[int, int]
    ) -> tuple[tuple[int, int], ...]:
        return ((1, 0),)

    def rebuild(
        _instance: object,
        solution: VrpSolution,
        stops_by_route: object,
        **_kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        routes = list(solution.routes)
        for route_index, stops in cast(
            dict[int, tuple[RouteStop, ...]], stops_by_route
        ).items():
            routes[route_index] = replace(routes[route_index], stops=stops)
        relocated = replace(solution, routes=tuple(routes))
        return tuple(
            replace(
                relocated,
                routes=(
                    replace(relocated.routes[0], start_time=10.0 + index),
                    *relocated.routes[1:],
                ),
            )
            for index in range(5)
        )

    def assessor(
        _instance: object,
        _objectives: object,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(
            True, True, objective_values[solution.routes[0].start_time]
        )

    monkeypatch.setattr(tradeoff_module, '_insertion_positions', insertion_positions)
    monkeypatch.setattr(tradeoff_module, 'rebuild_changed_routes', rebuild)
    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='activity',
        max_neighborhood_size=1,
        max_candidate_alternatives=5,
    )

    result = operator.call(_context(base, max_candidates=8))

    assert len(result.candidates) == 4
    assert tuple(proposal.m for proposal in result.candidates) == (1, 1, 1, 1)
    assert {
        cast(VrpSolution, proposal.candidate.solution).routes[0].start_time
        for proposal in result.candidates
    } == {10.0, 11.0, 12.0, 13.0}
    assert (
        sum(
            objective_values[
                cast(VrpSolution, proposal.candidate.solution).routes[0].start_time
            ]
            == (2.0, 2.0)
            for proposal in result.candidates
        )
        == 2
    )
    for proposal in result.candidates:
        candidate = cast(VrpSolution, proposal.candidate.solution)
        assert any(
            isinstance(stop, ActivityStop)
            and stop.activity_ref == ActivityRef('intervention', 'pareto-activity-0')
            for stop in candidate.routes[1].stops
        )

    limited = operator.call(_context(base, max_candidates=2))
    assert len(limited.candidates) == 2


def test_pareto_activity_move_reports_sequential_move_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, base = _activity_plan(tmp_path, 2)
    objectives = _two_objectives()
    rebuild_count = 0

    def insertion_positions(
        _solution: VrpSolution, _source: tuple[int, int]
    ) -> tuple[tuple[int, int], ...]:
        return ((1, 0),)

    def rebuild(
        _instance: object,
        solution: VrpSolution,
        stops_by_route: object,
        **_kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        nonlocal rebuild_count
        rebuild_count += 1
        routes = list(solution.routes)
        for route_index, stops in cast(
            dict[int, tuple[RouteStop, ...]], stops_by_route
        ).items():
            routes[route_index] = replace(routes[route_index], stops=stops)
        relocated = replace(solution, routes=tuple(routes))
        return tuple(
            replace(
                relocated,
                routes=(
                    replace(
                        relocated.routes[0], start_time=20.0 + rebuild_count * 2 + index
                    ),
                    *relocated.routes[1:],
                ),
            )
            for index in range(2)
        )

    def assessor(
        _instance: object,
        _objectives: object,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (0.0, 0.0))

    monkeypatch.setattr(tradeoff_module, '_insertion_positions', insertion_positions)
    monkeypatch.setattr(tradeoff_module, 'rebuild_changed_routes', rebuild)
    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='activity',
        max_neighborhood_size=2,
        max_candidate_alternatives=2,
    )

    result = operator.call(_context(base, k=2, max_candidates=2))

    assert rebuild_count == 2
    assert len(result.candidates) == 2
    assert all(proposal.m == 2 for proposal in result.candidates)
    assert all(
        sum(isinstance(stop, ActivityStop) for stop in route.stops)
        == (0 if index == 0 else 2)
        for proposal in result.candidates
        for index, route in enumerate(
            cast(VrpSolution, proposal.candidate.solution).routes
        )
    )


def test_pareto_activity_move_reuses_path_search_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, base = _activity_plan(tmp_path, 1)
    search_index = PathSearchIndex(frozenset(), {})
    build_calls = 0
    received_indexes: list[PathSearchIndex | None] = []

    def build_index(_instance: object) -> PathSearchIndex:
        nonlocal build_calls
        build_calls += 1
        return search_index

    def insertion_positions(
        _solution: VrpSolution, _source: tuple[int, int]
    ) -> tuple[tuple[int, int], ...]:
        return ((1, 0),)

    def rebuild(
        _instance: object,
        _solution: VrpSolution,
        _stops_by_route: object,
        *,
        path_search_index: PathSearchIndex | None = None,
        **_kwargs: object,
    ) -> tuple[VrpSolution, ...]:
        received_indexes.append(path_search_index)
        return ()

    monkeypatch.setattr(tradeoff_module, 'build_path_search_index', build_index)
    monkeypatch.setattr(tradeoff_module, '_insertion_positions', insertion_positions)
    monkeypatch.setattr(tradeoff_module, 'rebuild_changed_routes', rebuild)
    operator = ParetoTradeoffOperator(
        instance,
        _two_objectives(),
        cast(
            VrpPlanAssessor,
            lambda *_args: ApplicationEvaluation(True, True, (0.0, 0.0)),
        ),
        move_family='activity',
        max_neighborhood_size=1,
        max_candidate_alternatives=1,
    )

    operator.call(_context(base))
    operator.call(_context(base))

    assert build_calls == 1
    assert len(received_indexes) == 2
    assert all(index is search_index for index in received_indexes)


def test_pareto_activity_move_relocates_an_activity(tmp_path: Path) -> None:
    instance, _single_objective, base = _test_plan(tmp_path)
    objectives = _two_objectives()
    empty_interventions = VrpInterventionData((), (), (), (), ())
    instance = instance._replace(
        intervention_data=empty_interventions._replace(
            interventions=(InterventionRecord('repair', 'b', 10.0, 1),)
        )
    )
    routes = list(base.routes)
    stops = list(routes[0].stops)
    stops[0] = ActivityStop(
        ActivityRef('intervention', 'repair'),
        frozenset(),
        0.0,
        0.0,
        0.0,
        False,
    )
    routes[0] = replace(routes[0], stops=tuple(stops))
    base = replace(base, routes=tuple(routes))

    def assessor(
        _instance: object,
        _objectives: object,
        _solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, (0.0, 0.0))

    operator = ParetoTradeoffOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assessor),
        move_family='activity',
        max_neighborhood_size=1,
        max_candidate_alternatives=8,
        max_paths_per_leg=8,
        max_arc_expansion_steps=500,
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    candidate = cast(VrpSolution, result.candidates[0].candidate.solution)
    location = next(
        (route_index, stop_index)
        for route_index, route in enumerate(candidate.routes)
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop)
        and stop.activity_ref == ActivityRef('intervention', 'repair')
    )
    assert location != (0, 0)
