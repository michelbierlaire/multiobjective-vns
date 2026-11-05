"""Tests for scenario-level service precedence feasibility.

Created: 2026-11-03T19:48:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import inf
from pathlib import Path

from multiobjective_vns.multiday_vrp import (
    VrpApplicationEvaluator,
    build_vrp_objective_declaration,
)
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    InterventionTimeWindowRecord,
    PersonRecord,
    PrecedenceRecord,
    VehicleOvernightDepotRecord,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)

from .test_evaluation_capacity import _package_instance


def test_precedence_is_checked_across_routes_in_each_scenario(tmp_path: Path) -> None:
    instance = _package_instance(tmp_path, package_loads=(), vehicle_capacity=5.0)
    vehicle = instance.fleet_data.vehicles[0]._replace(vehicle_id='vehicle-2')
    fleet = instance.fleet_data._replace(
        vehicles=(*instance.fleet_data.vehicles, vehicle),
        vehicle_overnight_depots=(
            *instance.fleet_data.vehicle_overnight_depots,
            VehicleOvernightDepotRecord('vehicle-2', 'depot-1'),
        ),
        vehicle_availability=(
            *instance.fleet_data.vehicle_availability,
            instance.fleet_data.vehicle_availability[0]._replace(
                vehicle_id='vehicle-2'
            ),
        ),
    )
    personnel = instance.personnel_data._replace(
        personnel=(
            *instance.personnel_data.personnel,
            PersonRecord('driver-2', True, None),
        ),
        personnel_availability=(
            *instance.personnel_data.personnel_availability,
            instance.personnel_data.personnel_availability[0]._replace(
                person_id='driver-2'
            ),
        ),
    )
    interventions = instance.intervention_data._replace(
        interventions=(
            InterventionRecord('before', 'node-a', 0.0, 1),
            InterventionRecord('after', 'node-a', 0.0, 1),
        ),
        intervention_time_windows=(
            InterventionTimeWindowRecord('before', 1, 1, 0.0, 1440.0),
            InterventionTimeWindowRecord('after', 1, 1, 0.0, 1440.0),
        ),
    )
    relations = instance.activity_relations._replace(
        precedence=(
            PrecedenceRecord('intervention', 'before', 'intervention', 'after'),
        )
    )
    instance = instance._replace(
        fleet_data=fleet,
        personnel_data=personnel,
        intervention_data=interventions,
        activity_relations=relations,
    )
    before = ActivityStop(
        ActivityRef('intervention', 'before'),
        frozenset({'driver-1'}),
        0.0,
        0.0,
        0.0,
        False,
    )
    after = ActivityStop(
        ActivityRef('intervention', 'after'),
        frozenset({'driver-2'}),
        0.0,
        0.0,
        0.0,
        False,
    )
    solution = VrpSolution(
        routes=(
            VehicleDayRoute(
                'vehicle-1',
                1,
                'depot-1',
                10.0,
                'driver-1',
                (before,),
                (RouteLeg(()), RouteLeg(())),
            ),
            VehicleDayRoute(
                'vehicle-2',
                1,
                'depot-1',
                0.0,
                'driver-2',
                (after,),
                (RouteLeg(()), RouteLeg(())),
            ),
        ),
        transfer_steps=(),
    )

    result = VrpApplicationEvaluator(instance).evaluate(
        solution, build_vrp_objective_declaration(instance)
    )

    assert result.feasible is False
    assert result.objective_values == (inf,)
    analysis = result.analysis
    assert isinstance(analysis, VrpOperatorAnalysis)
    assert analysis.hard_failure is not None
    assert analysis.hard_failure.kind == 'scenario_constraint'
    assert analysis.hard_failure.scenario_id == 'scenario-000001'
