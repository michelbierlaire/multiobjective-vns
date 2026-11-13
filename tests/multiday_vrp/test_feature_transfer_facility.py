"""Tests for transfer-facility assignment Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.records import (
    ResourceFinding,
    VrpOperatorAnalysis,
)
from multiobjective_vns.multiday_vrp.input_records import (
    PackageRecord,
    PackageTransferFacilityRecord,
    TransferFacilityRecord,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.transfer_facility import (
    TransferFacilityAssignmentOperator,
    _target_steps,
)
from multiobjective_vns.multiday_vrp.solution import (
    RouteLeg,
    TransferOperationStop,
    TransferRole,
    TransferStep,
    TransferStepRef,
    VrpSolution,
)
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .test_universal_route_order import _always_assess, _test_plan


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 5000)

    def random(self) -> float:
        return next(self._draws)


def test_transfer_facility_assignment_updates_step_and_both_routes(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 1, 0.0, 0.0)
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
    routes = []
    for route_index, role in enumerate(('out', 'in')):
        route = base.routes[route_index]
        stop = TransferOperationStop(
            TransferStepRef('pkg', 1), cast(TransferRole, role), 0.0, 0.0, 0.0, False
        )
        routes.append(
            replace(
                route,
                stops=(stop,),
                legs=(RouteLeg(('a-c',)), RouteLeg(('c-a',))),
            )
        )
    base = replace(
        base,
        routes=tuple(routes),
        transfer_steps=(TransferStep('pkg', 1, 'c'),),
    )
    operator = TransferFacilityAssignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, _always_assess),
        max_neighborhood_size=1,
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
    assert changed.transfer_steps[0].facility_id == 'd'
    arc_endpoints = {
        arc.arc_id: (arc.tail_node_id, arc.head_node_id)
        for arc in instance.network_data.arcs
    }
    for route in changed.routes:
        assert arc_endpoints[route.legs[0].arc_ids[0]][0] == 'a'
        assert arc_endpoints[route.legs[0].arc_ids[-1]][1] == 'd'


def test_storage_target_selection_uses_the_largest_excess_facility(
    tmp_path: Path,
) -> None:
    _instance, _objectives, base = _test_plan(tmp_path)
    solution = replace(
        base,
        transfer_steps=(
            TransferStep('package-a', 1, 'facility-a'),
            TransferStep('package-b', 1, 'facility-b'),
            TransferStep('package-b2', 1, 'facility-b'),
            TransferStep('package-c', 1, 'facility-c'),
        ),
    )
    analysis = VrpOperatorAnalysis(
        is_complete=True,
        scenario_evaluations=(),
        resource_findings=(
            ResourceFinding(
                'transfer_storage',
                'facility-a',
                'scenario-1',
                None,
                None,
                None,
                2.0,
                (),
                TransferStepRef('package-a', 1),
            ),
            ResourceFinding(
                'transfer_storage',
                'facility-b',
                'scenario-1',
                None,
                None,
                None,
                5.0,
                (),
                TransferStepRef('package-b', 1),
            ),
            ResourceFinding(
                'transfer_storage',
                'facility-c',
                'scenario-2',
                None,
                None,
                None,
                1.0,
                (),
                TransferStepRef('package-c', 1),
            ),
        ),
        scenario_objective_costs=(),
    )

    targets = _target_steps(
        solution,
        ApplicationEvaluation(True, True, (0.0,), analysis),
        set(),
    )

    assert targets == (('package-b', 1),)


def test_storage_assignment_returns_candidates_ordered_by_excess_then_objective(
    tmp_path: Path,
) -> None:
    instance, objectives, base = _test_plan(tmp_path)
    package = PackageRecord('pkg', 'b', 'c', 1.0, 0.0, 0.0, 1, 0.0, 0.0)
    facilities = ('c', 'd', 'e', 'f')
    instance = instance._replace(
        network_data=instance.network_data._replace(
            transfer_facilities=tuple(
                TransferFacilityRecord(node, 100.0) for node in facilities
            )
        ),
        package_data=instance.package_data._replace(
            packages=(package,),
            package_transfer_facilities=tuple(
                PackageTransferFacilityRecord('pkg', node) for node in facilities
            ),
        ),
    )
    routes = []
    for route_index, role in enumerate(('out', 'in')):
        route = base.routes[route_index]
        routes.append(
            replace(
                route,
                stops=(
                    TransferOperationStop(
                        TransferStepRef('pkg', 1),
                        cast(TransferRole, role),
                        0.0,
                        0.0,
                        0.0,
                        False,
                    ),
                ),
                legs=(RouteLeg(('a-c',)), RouteLeg(('c-a',))),
            )
        )
    base = replace(
        base,
        routes=tuple(routes),
        transfer_steps=(TransferStep('pkg', 1, 'c'),),
    )

    def assess(
        _instance: object,
        _objectives: object,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        facility = solution.transfer_steps[0].facility_id
        if facility == 'c':
            excess = 0.0
            objective_value = 0.0
            findings: tuple[ResourceFinding, ...] = ()
        else:
            excess, objective_value = {
                'd': (2.0, 0.0),
                'e': (1.0, 2.0),
                'f': (1.0, 1.0),
            }[facility]
            findings = (
                ResourceFinding(
                    'transfer_storage',
                    facility,
                    'scenario-1',
                    None,
                    None,
                    None,
                    excess,
                    (),
                ),
            )
        analysis = VrpOperatorAnalysis(
            True,
            (),
            findings,
            (),
        )
        return ApplicationEvaluation(True, True, (objective_value,), analysis)

    operator = TransferFacilityAssignmentOperator(
        instance,
        objectives,
        cast(VrpPlanAssessor, assess),
        max_neighborhood_size=1,
        objective_id=objectives.objectives[0].objective_id,
        max_candidate_alternatives=3,
        max_paths_per_leg=1,
        max_arc_expansion_steps=500,
    )
    context = CandidateContext(
        base,
        1,
        False,
        None,
        cast(RandomSource, _ZeroRandomSource()),
        max_candidates=3,
    )

    result = operator.call(context)

    assert len(result.candidates) == 3
    assert all(proposal.m == 1 for proposal in result.candidates)
    facilities_in_order = tuple(
        cast(VrpSolution, proposal.candidate.solution).transfer_steps[0].facility_id
        for proposal in result.candidates
    )
    assert facilities_in_order == ('f', 'e', 'd')
