"""Tests for immutable VRP evaluation analysis records.

Created: 2026-11-03T18:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from multiobjective_vns.multiday_vrp.evaluation.records import (
    HardFeasibilityFinding,
    ResourceFinding,
    ScenarioObjectiveCost,
    VrpOperatorAnalysis,
)


def test_vrp_operator_analysis_is_an_immutable_snapshot() -> None:
    finding = ResourceFinding(
        kind='vehicle_availability',
        resource_id='vehicle-1',
        scenario_id='scenario-1',
        vehicle_id='vehicle-1',
        day_index=1,
        activity_ref=None,
        measure=2.5,
        penalty_by_objective=(('travel', 5.0),),
    )
    cost = ScenarioObjectiveCost('travel', 'scenario-1', 10.0)
    analysis = VrpOperatorAnalysis(
        is_complete=True,
        scenario_evaluations=(),
        resource_findings=(finding,),
        scenario_objective_costs=(cost,),
    )

    assert analysis.resource_findings == (finding,)
    assert analysis.scenario_objective_costs == (cost,)
    with pytest.raises(FrozenInstanceError):
        analysis.is_complete = False  # type: ignore[misc]


@pytest.mark.parametrize('measure', [-1.0, float('inf'), float('nan'), 1])
def test_resource_finding_requires_a_finite_nonnegative_float(measure: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ResourceFinding(
            kind='vehicle_availability',
            resource_id='vehicle-1',
            scenario_id=None,
            vehicle_id='vehicle-1',
            day_index=1,
            activity_ref=None,
            measure=measure,  # type: ignore[arg-type]
            penalty_by_objective=(),
        )


def test_analysis_rejects_wrong_collection_items() -> None:
    with pytest.raises(TypeError):
        VrpOperatorAnalysis(True, (), ('not a finding',), ())  # type: ignore[arg-type]


def test_scenario_objective_cost_requires_finite_value() -> None:
    with pytest.raises(ValueError):
        ScenarioObjectiveCost('travel', 'scenario-1', float('inf'))


def test_incomplete_analysis_requires_a_hard_feasibility_finding() -> None:
    failure = HardFeasibilityFinding(
        'static_constraint', 'vehicle exceeds capacity', None
    )
    analysis = VrpOperatorAnalysis(False, (), (), (), failure)

    assert analysis.hard_failure == failure
    with pytest.raises(ValueError):
        VrpOperatorAnalysis(False, (), (), ())
    with pytest.raises(ValueError):
        VrpOperatorAnalysis(True, (), (), (), failure)


def test_hard_finding_validates_kind_message_and_scenario() -> None:
    with pytest.raises(ValueError):
        HardFeasibilityFinding('unknown', 'reason', None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        HardFeasibilityFinding('scenario_constraint', '', 'scenario-1')
