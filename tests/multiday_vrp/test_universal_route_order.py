"""Tests for generic route-order Operators.

Created: 2026-11-03T20:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.callbacks import Operator
from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    build_vrp_objective_declaration,
)
from multiobjective_vns.multiday_vrp.input_loader import load_vrp_instance
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    randomized_sample,
)
from multiobjective_vns.multiday_vrp.operators.route_order import (
    ExchangeOperator,
    OrOptChainRelocationOperator,
    RelocateOperator,
    SwapStarOperator,
    TwoOptOperator,
    TwoOptStarOperator,
    _build_or_opt,
    _build_or_opt_candidate,
    _build_relocate,
    _build_relocate_candidate,
    _build_swap_star,
    _build_swap_star_candidate,
    _exchange_target_at,
    _exchange_target_count,
    _exchange_targets,
    _or_opt_alternative_targets,
    _or_opt_targets,
    _relocate_alternative_targets,
    _relocate_targets,
    _sample_exchange_target,
    _sample_index_positions,
    _sample_swap_star_target,
    _sample_two_opt_star_target,
    _sample_two_opt_targets,
    _swap_star_insertion_pool,
    _swap_star_target_at,
    _swap_star_target_count,
    _swap_star_targets,
    _two_opt_star_target_at,
    _two_opt_star_target_count,
    _two_opt_star_targets,
    _two_opt_target_at,
    _two_opt_targets,
    _unique_solutions,
)
from multiobjective_vns.multiday_vrp.solution import (
    DepotOnlyStop,
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .input_fixtures import write_minimal_vrp_bundle


class _ZeroRandomSource:
    component_id = 'test-rng'
    configuration_id = 'test-rng-v1'

    def random(self) -> float:
        return 0.0

    def capture_state(self) -> object:
        return None

    def restore_state(self, payload: object) -> None:
        del payload


class _SequenceRandomSource:
    def __init__(self, draws: tuple[float, ...]) -> None:
        self._draws = draws
        self._position = 0

    def random(self) -> float:
        draw = self._draws[self._position]
        self._position += 1
        return draw


def _test_plan(
    root: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    manifest = write_minimal_vrp_bundle(root)
    instance_directory = manifest.parent
    node_ids = ('a', 'b', 'c', 'd', 'e', 'f', 'g')
    arc_ids = tuple(
        f'{tail}-{head}' for tail in node_ids for head in node_ids if tail != head
    )
    (instance_directory / 'nodes.csv').write_text(
        'node_id\n' + ''.join(f'{node_id}\n' for node_id in node_ids),
        encoding='utf-8',
    )
    (instance_directory / 'depots.csv').write_text(
        'depot_id,node_id\n' + ''.join(f'depot-{node},{node}\n' for node in node_ids),
        encoding='utf-8',
    )
    (instance_directory / 'vehicles.csv').write_text(
        'vehicle_id,cargo_capacity,seat_capacity,initial_depot_id,max_tours_per_day\n'
        'vehicle-1,100.0,1,depot-a,1\n'
        'vehicle-2,100.0,1,depot-a,1\n',
        encoding='utf-8',
    )
    (instance_directory / 'arcs.csv').write_text(
        'arc_id,tail_node_id,head_node_id\n'
        + ''.join(
            f'{tail}-{head},{tail},{head}\n'
            for tail in node_ids
            for head in node_ids
            if tail != head
        ),
        encoding='utf-8',
    )
    scenario_directory = root / 'scenario-bank'
    (scenario_directory / 'arc-catalog.csv').write_text(
        'arc_id\n' + ''.join(f'{arc_id}\n' for arc_id in arc_ids),
        encoding='utf-8',
    )
    (scenario_directory / 'travel-times.csv').write_text(
        'scenario_id,arc_id,day_index,boundary_index,travel_time\n'
        + ''.join(
            f'scenario-000001,{arc_id},1,{boundary_index},5.0\n'
            for arc_id in arc_ids
            for boundary_index in range(3)
        ),
        encoding='utf-8',
    )
    (instance_directory / 'vehicle-overnight-depots.csv').write_text(
        'vehicle_id,depot_id\nvehicle-1,depot-a\nvehicle-2,depot-a\n',
        encoding='utf-8',
    )
    instance = load_vrp_instance(manifest)

    def route(vehicle_id: str, stop_nodes: tuple[str, ...]) -> VehicleDayRoute:
        nodes = ('a', *stop_nodes, 'a')
        stops = tuple(
            DepotOnlyStop(node, 0.0, float(index + 1), 0.0, False)
            for index, node in enumerate(stop_nodes)
        )
        legs = tuple(
            RouteLeg(()) if tail == head else RouteLeg((f'{tail}-{head}',))
            for tail, head in zip(nodes[:-1], nodes[1:], strict=True)
        )
        return VehicleDayRoute(
            vehicle_id,
            1,
            'depot-a',
            0.0,
            'driver-1',
            stops,
            legs,
        )

    solution = VrpSolution(
        (route('vehicle-1', ('b', 'c')), route('vehicle-2', ('d', 'e'))),
        (),
    )
    return instance, build_vrp_objective_declaration(instance), solution


def _always_assess(
    _instance: LoadedVrpInstance,
    _objectives: ObjectiveDeclaration,
    _solution: VrpSolution,
) -> ApplicationEvaluation:
    return ApplicationEvaluation(True, True, (0.0,))


def _operator(
    operator_type: Callable[..., Operator],
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
) -> Operator:
    return operator_type(
        instance,
        objectives,
        assess_plan,
        max_neighborhood_size=5,
        max_candidate_alternatives=8,
        max_paths_per_leg=4,
        max_arc_expansion_steps=100,
    )


def _context(
    base: VrpSolution,
    *,
    k: int = 1,
    ensure_feasible: bool = False,
    max_candidates: int = 1,
) -> CandidateContext:
    return CandidateContext(
        base=base,
        base_evaluation=None,
        k=k,
        ensure_feasible=ensure_feasible,
        operator_state=None,
        random_source=cast(RandomSource, _ZeroRandomSource()),
        max_candidates=max_candidates,
    )


def _six_stop_plan(base: VrpSolution) -> VrpSolution:
    stop_nodes = ('b', 'c', 'd', 'e', 'f', 'g')
    stops = tuple(
        DepotOnlyStop(node, 0.0, float(index + 1), 0.0, False)
        for index, node in enumerate(stop_nodes)
    )
    route_nodes = ('a', *stop_nodes, 'a')
    legs = tuple(
        RouteLeg(()) if tail == head else RouteLeg((f'{tail}-{head}',))
        for tail, head in zip(route_nodes[:-1], route_nodes[1:], strict=True)
    )
    long_route = replace(base.routes[0], stops=stops, legs=legs)
    empty_route = replace(base.routes[1], stops=(), legs=(RouteLeg(()),))
    return VrpSolution((long_route, empty_route), ())


@pytest.mark.parametrize(
    'operator_type',
    [
        TwoOptOperator,
        RelocateOperator,
        OrOptChainRelocationOperator,
        ExchangeOperator,
        TwoOptStarOperator,
        SwapStarOperator,
    ],
)
def test_route_order_operators_return_changed_candidates(
    tmp_path: Path,
    operator_type: Callable[..., Operator],
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    operator = _operator(
        operator_type, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )

    result = operator.call(_context(base))

    assert result.candidates[0].m == 1
    assert result.candidates
    assert result.candidates[0].candidate.solution != base


def test_exchange_excludes_a_sequence_of_swaps_that_returns_to_base(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = _operator(
        ExchangeOperator, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )

    result = operator.call(_context(base, k=2))

    assert result.candidates == ()


def test_exchange_index_sampler_matches_materialized_pair_order(
    tmp_path: Path,
) -> None:
    _, _, base = _test_plan(tmp_path)
    day_two_route = replace(base.routes[1], day_index=2)
    third_day_one_route = replace(base.routes[0], vehicle_id='vehicle-3')
    solution = replace(
        base, routes=(base.routes[0], day_two_route, third_day_one_route)
    )
    expected = _exchange_targets(solution, 1)

    assert _exchange_target_count(solution) == len(expected)
    assert (
        tuple(_exchange_target_at(solution, index) for index in range(len(expected)))
        == expected
    )

    for draw in (0.0, 0.11, 0.53, 0.99):
        assert (
            _sample_exchange_target(
                solution, cast(RandomSource, _SequenceRandomSource((draw,)))
            )
            == randomized_sample(
                expected,
                1,
                cast(RandomSource, _SequenceRandomSource((draw,))),
            )[0]
        )


def test_two_opt_star_index_sampler_matches_materialized_target_order(
    tmp_path: Path,
) -> None:
    _, _, base = _test_plan(tmp_path)
    day_two_route = replace(base.routes[1], day_index=2)
    one_stop_route = replace(
        base.routes[0],
        vehicle_id='vehicle-3',
        stops=base.routes[0].stops[:1],
        legs=(RouteLeg(('a-b',)), RouteLeg(('b-a',))),
    )
    solution = replace(base, routes=(base.routes[0], day_two_route, one_stop_route))
    expected = _two_opt_star_targets(solution, 1)

    assert _two_opt_star_target_count(solution) == len(expected)
    assert (
        tuple(
            _two_opt_star_target_at(solution, index) for index in range(len(expected))
        )
        == expected
    )
    for draw in (0.0, 0.11, 0.53, 0.99):
        assert (
            _sample_two_opt_star_target(
                solution, cast(RandomSource, _SequenceRandomSource((draw,)))
            )
            == randomized_sample(
                expected,
                1,
                cast(RandomSource, _SequenceRandomSource((draw,))),
            )[0]
        )


def test_two_opt_star_cut_zero_exchanges_each_complete_suffix(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = _operator(
        TwoOptStarOperator, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )

    result = operator.call(_context(base))

    assert result.candidates
    changed = cast(VrpSolution, result.candidates[0].candidate.solution)
    assert changed.routes[0].stops == base.routes[1].stops
    assert changed.routes[1].stops == base.routes[0].stops
    for actual, original in zip(changed.routes, base.routes, strict=True):
        assert actual.vehicle_id == original.vehicle_id
        assert actual.day_index == original.day_index
        assert actual.overnight_depot_id == original.overnight_depot_id
        assert actual.start_time == original.start_time
        assert actual.driver_id == original.driver_id


def test_two_opt_star_cut_at_stop_count_has_empty_suffixes(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = _operator(
        TwoOptStarOperator, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )
    context = CandidateContext(
        base=base,
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, _SequenceRandomSource((0.99,))),
        max_candidates=1,
    )

    assert operator.call(context).candidates == ()


def test_two_opt_star_discards_a_chain_that_restores_original_stop_sequences(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = _operator(
        TwoOptStarOperator, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )

    result = operator.call(_context(base, k=2))

    assert result.candidates == ()


def test_swap_star_target_sampler_matches_materialized_pair_order(
    tmp_path: Path,
) -> None:
    _, _, base = _test_plan(tmp_path)
    day_two_route = replace(base.routes[1], day_index=2)
    third_day_one_route = replace(base.routes[0], vehicle_id='vehicle-3')
    solution = replace(
        base, routes=(base.routes[0], day_two_route, third_day_one_route)
    )
    expected = _swap_star_targets(solution, 1)

    assert _swap_star_target_count(solution) == len(expected)
    assert (
        tuple(_swap_star_target_at(solution, index) for index in range(len(expected)))
        == expected
    )
    for draw in (0.0, 0.11, 0.53, 0.99):
        assert (
            _sample_swap_star_target(
                solution, cast(RandomSource, _SequenceRandomSource((draw,)))
            )
            == randomized_sample(
                expected,
                1,
                cast(RandomSource, _SequenceRandomSource((draw,))),
            )[0]
        )


def test_swap_star_insertion_pool_matches_unique_complete_plan_pool(
    tmp_path: Path,
) -> None:
    _, _, base = _test_plan(tmp_path)
    left_stops = (base.routes[0].stops[0], base.routes[0].stops[1]) * 2
    right_stops = (base.routes[0].stops[1], base.routes[0].stops[0]) * 2
    left_nodes = ('a', 'b', 'c', 'b', 'c', 'a')
    right_nodes = ('a', 'c', 'b', 'c', 'b', 'a')
    left_legs = tuple(
        RouteLeg(()) if tail == head else RouteLeg((f'{tail}-{head}',))
        for tail, head in zip(left_nodes[:-1], left_nodes[1:], strict=True)
    )
    right_legs = tuple(
        RouteLeg(()) if tail == head else RouteLeg((f'{tail}-{head}',))
        for tail, head in zip(right_nodes[:-1], right_nodes[1:], strict=True)
    )
    solution = replace(
        base,
        routes=(
            replace(base.routes[0], stops=left_stops, legs=left_legs),
            replace(base.routes[1], stops=right_stops, legs=right_legs),
        ),
    )

    for target in _swap_star_targets(solution, 1):
        expected = _unique_solutions(_build_swap_star(solution, target, 1))
        insertion_pool = _swap_star_insertion_pool(solution, target)
        actual = tuple(
            _build_swap_star_candidate(
                solution, target, insertion_pool.target_at(index)
            )
            for index in range(insertion_pool.alternative_count)
        )

        assert actual == expected
        sample_size = min(4, len(expected))
        draws = (0.17, 0.71, 0.32, 0.55)
        assert _sample_index_positions(
            insertion_pool.alternative_count,
            sample_size,
            cast(RandomSource, _SequenceRandomSource(draws)),
        ) == randomized_sample(
            tuple(range(insertion_pool.alternative_count)),
            sample_size,
            cast(RandomSource, _SequenceRandomSource(draws)),
        )
        assert randomized_sample(
            actual,
            sample_size,
            cast(RandomSource, _SequenceRandomSource(draws)),
        ) == randomized_sample(
            expected,
            sample_size,
            cast(RandomSource, _SequenceRandomSource(draws)),
        )


def test_swap_star_insertion_pool_excludes_unchanged_pair(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    shared_stop = base.routes[0].stops[0]
    routes = tuple(
        replace(
            route,
            stops=(shared_stop, shared_stop),
            legs=(RouteLeg(('a-b',)), RouteLeg(()), RouteLeg(('b-a',))),
        )
        for route in base.routes
    )
    solution = replace(base, routes=routes)
    target = (0, 0, 1, 0)
    insertion_pool = _swap_star_insertion_pool(solution, target)

    assert _build_swap_star(solution, target, 1) == ()
    assert insertion_pool.alternative_count == 0
    operator = _operator(
        SwapStarOperator, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )

    assert operator.call(_context(solution)).candidates == ()


def test_swap_star_modification_count_is_number_of_pair_exchanges(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = _operator(
        SwapStarOperator, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )

    result = operator.call(_context(base, k=2))

    assert result.candidates
    proposal = result.candidates[0]
    assert proposal.m == 2
    assert proposal.candidate.solution != base


def test_or_opt_uses_k_as_chain_length(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    operator = _operator(
        OrOptChainRelocationOperator,
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
    )

    result = operator.call(_context(base, k=2))

    assert result.candidates
    proposal = result.candidates[0]
    changed = cast(VrpSolution, proposal.candidate.solution)
    assert proposal.m == 2
    assert changed.routes[0].stops == ()
    assert changed.routes[1].stops[:2] == base.routes[0].stops


def test_two_opt_returns_requested_number_of_distinct_candidates(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    base = _six_stop_plan(base)
    operator = _operator(
        TwoOptOperator, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )

    result = operator.call(_context(base, max_candidates=10))

    candidates = tuple(proposal.candidate.solution for proposal in result.candidates)
    assert len(candidates) == 10
    assert all(candidate != base for candidate in candidates)
    assert all(
        left != right
        for index, left in enumerate(candidates)
        for right in candidates[index + 1 :]
    )
    assert all(proposal.m == 1 for proposal in result.candidates)


def test_two_opt_sparse_target_sampling_preserves_reference_order(
    tmp_path: Path,
) -> None:
    _instance, _objectives, base = _test_plan(tmp_path)
    all_targets = _two_opt_targets(base, 1)
    assert (
        tuple(_two_opt_target_at(base, index) for index in range(len(all_targets)))
        == all_targets
    )

    base = _six_stop_plan(base)
    expected = randomized_sample(
        _two_opt_targets(base, 1), 8, cast(RandomSource, _ZeroRandomSource())
    )

    actual = _sample_two_opt_targets(base, 8, cast(RandomSource, _ZeroRandomSource()))

    assert actual == expected

    draws = (0.53, 0.1, 0.9, 0.0, 0.32, 0.8, 0.2, 0.62)
    expected = randomized_sample(
        _two_opt_targets(base, 1),
        8,
        cast(RandomSource, _SequenceRandomSource(draws)),
    )
    actual = _sample_two_opt_targets(
        base, 8, cast(RandomSource, _SequenceRandomSource(draws))
    )
    assert actual == expected


def test_relocate_alternative_pool_matches_unique_plan_pool(tmp_path: Path) -> None:
    _, _, base = _test_plan(tmp_path)
    duplicate_route = replace(base.routes[0], stops=(base.routes[0].stops[0],) * 2)
    duplicate_stop_plan = replace(base, routes=(duplicate_route, base.routes[1]))

    for solution in (base, duplicate_stop_plan):
        for target in _relocate_targets(solution, 1):
            expected = _unique_solutions(_build_relocate(solution, target, 1))
            insertion_targets = _relocate_alternative_targets(solution, target)
            actual = tuple(
                _build_relocate_candidate(solution, target, insertion_target)
                for insertion_target in insertion_targets
            )

            assert actual == expected
            sample_size = min(3, len(expected))
            draws = (0.17, 0.71, 0.32)
            assert randomized_sample(
                actual,
                sample_size,
                cast(RandomSource, _SequenceRandomSource(draws)),
            ) == randomized_sample(
                expected,
                sample_size,
                cast(RandomSource, _SequenceRandomSource(draws)),
            )


def test_or_opt_alternative_pool_matches_unique_plan_pool(tmp_path: Path) -> None:
    _, _, base = _test_plan(tmp_path)
    repeated_stops = (base.routes[0].stops[0], base.routes[0].stops[1]) * 3
    route_nodes = ('a', 'b', 'c', 'b', 'c', 'b', 'c', 'a')
    legs = tuple(
        RouteLeg(()) if tail == head else RouteLeg((f'{tail}-{head}',))
        for tail, head in zip(route_nodes[:-1], route_nodes[1:], strict=True)
    )
    repeated_route = replace(base.routes[0], stops=repeated_stops, legs=legs)
    different_day_route = replace(base.routes[1], day_index=2)
    solution = replace(base, routes=(repeated_route, different_day_route))
    size = 2

    for target in _or_opt_targets(solution, size):
        expected = _unique_solutions(_build_or_opt(solution, target, size))
        insertion_targets = _or_opt_alternative_targets(solution, target, size)
        actual = tuple(
            _build_or_opt_candidate(solution, target, size, insertion_target)
            for insertion_target in insertion_targets
        )

        assert actual == expected
        sample_size = min(3, len(expected))
        draws = (0.17, 0.71, 0.32)
        assert randomized_sample(
            actual,
            sample_size,
            cast(RandomSource, _SequenceRandomSource(draws)),
        ) == randomized_sample(
            expected,
            sample_size,
            cast(RandomSource, _SequenceRandomSource(draws)),
        )


def test_two_opt_returns_all_distinct_candidates_when_fewer_than_requested(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    operator = _operator(
        TwoOptOperator, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )

    result = operator.call(_context(base, max_candidates=10))

    candidates = tuple(proposal.candidate.solution for proposal in result.candidates)
    assert len(candidates) == 2
    assert all(candidate != base for candidate in candidates)
    assert candidates[0] != candidates[1]


def test_two_opt_candidate_batch_preserves_size_modification_counts(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    base = _six_stop_plan(base)
    operator = _operator(
        TwoOptOperator, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )

    result = operator.call(_context(base, k=2, max_candidates=3))

    assert len(result.candidates) == 3
    assert all(proposal.m == 2 for proposal in result.candidates)


def test_route_order_operator_returns_no_candidate_when_feasibility_fails(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    def assessor(
        _instance: LoadedVrpInstance,
        _objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        feasible = solution == base
        return ApplicationEvaluation(True, feasible, (0.0,))

    operator = _operator(
        TwoOptOperator, instance, objectives, cast(VrpPlanAssessor, assessor)
    )

    result = operator.call(_context(base, ensure_feasible=True))

    assert result.candidates == ()
    assert not result.candidates


def test_route_order_operator_rejects_size_above_registered_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    operator = _operator(
        TwoOptOperator, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )

    with pytest.raises(ValueError, match='maximum neighborhood size'):
        operator.call(_context(base, k=6))
