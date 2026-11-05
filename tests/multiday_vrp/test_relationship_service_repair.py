"""Tests for activity-relationship-guided service repair.

Created: 2026-11-04T09:43:07+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from multiobjective_vns.multiday_vrp.input_records import (
    CoLocationRecord,
    InterventionRecord,
    LoadedVrpInstance,
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
        return ApplicationEvaluation(True, feasible, (0.0,))

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

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    positions = {
        stop.activity_ref.activity_id: (route_index, stop_index)
        for route_index, route in enumerate(changed.routes)
        for stop_index, stop in enumerate(route.stops)
        if isinstance(stop, ActivityStop)
    }
    assert positions['a'][0] == positions['b'][0]
    assert positions['a'][1] < positions['b'][1]


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

    assert result.m == 1
    assert result.candidate is not None
    changed = cast(VrpSolution, result.candidate.solution)
    activities = tuple(
        stop.activity_ref.activity_id
        for route in changed.routes
        for stop in route.stops
        if isinstance(stop, ActivityStop)
    )
    assert len(activities) == 1
