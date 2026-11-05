"""Tests for generic route-order Operators.

Created: 2026-11-03T20:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.callbacks import Operator
from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    build_vrp_objective_declaration,
)
from multiobjective_vns.multiday_vrp.input_loader import load_vrp_instance
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.route_order import (
    ExchangeOperator,
    OrOptChainRelocationOperator,
    RelocateOperator,
    SwapStarOperator,
    TwoOptOperator,
    TwoOptStarOperator,
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


def _test_plan(
    root: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration, VrpSolution]:
    manifest = write_minimal_vrp_bundle(root)
    instance_directory = manifest.parent
    node_ids = ('a', 'b', 'c', 'd', 'e')
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
    base: VrpSolution, *, k: int = 1, ensure_feasible: bool = False
) -> CandidateContext:
    return CandidateContext(
        base=base,
        base_evaluation=None,
        k=k,
        ensure_feasible=ensure_feasible,
        operator_state=None,
        random_source=cast(RandomSource, _ZeroRandomSource()),
    )


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

    assert result.m == 1
    assert result.candidate is not None
    assert result.candidate.solution != base


def test_or_opt_uses_k_as_chain_length(tmp_path: Path) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    operator = _operator(
        OrOptChainRelocationOperator,
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
    )

    result = operator.call(_context(base, k=2))

    assert result.m == 2
    assert result.candidate is not None


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

    assert result.m == 0
    assert result.candidate is None


def test_route_order_operator_rejects_size_above_registered_limit(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)

    operator = _operator(
        TwoOptOperator, instance, objectives, cast(VrpPlanAssessor, _always_assess)
    )

    with pytest.raises(ValueError, match='maximum neighborhood size'):
        operator.call(_context(base, k=6))
