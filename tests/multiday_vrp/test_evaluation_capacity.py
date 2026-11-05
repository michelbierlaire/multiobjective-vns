"""Tests for VRP package custody, compatibility, and vehicle capacity.

Created: 2026-11-03T19:42:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import inf
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp import (
    VrpApplicationEvaluator,
    build_vrp_objective_declaration,
    load_vrp_instance,
)
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.input_records import (
    FrozenTomlTable,
    InterventionRecord,
    InterventionTimeWindowRecord,
    InterventionToolRecord,
    LoadedVrpInstance,
    PackageRecord,
    PackageTimeWindowRecord,
    PackageTransferFacilityRecord,
    PenaltyFunctionSpec,
    PersonRecord,
    ToolRecord,
    TransferFacilityRecord,
    VehicleOvernightDepotRecord,
    VehiclePackageCompatibilityRecord,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityKind,
    ActivityRef,
    ActivityStop,
    RouteLeg,
    TransferOperationStop,
    TransferStep,
    TransferStepRef,
    VehicleDayRoute,
    VrpSolution,
)

from .input_fixtures import write_minimal_vrp_bundle


def _package_instance(
    root: Path,
    *,
    package_loads: tuple[float, ...],
    vehicle_capacity: float,
    compatibility: bool = True,
) -> LoadedVrpInstance:
    manifest = write_minimal_vrp_bundle(root)
    instance_dir = manifest.parent
    (instance_dir / 'vehicles.csv').write_text(
        'vehicle_id,cargo_capacity,seat_capacity,initial_depot_id,max_tours_per_day\n'
        f'vehicle-1,{vehicle_capacity},1,depot-1,1\n',
        encoding='utf-8',
    )
    instance = load_vrp_instance(manifest)
    package_ids = tuple(
        f'package-{index}' for index in range(1, len(package_loads) + 1)
    )
    packages = tuple(
        PackageRecord(package_id, 'node-a', 'node-a', load, 0.0, 0.0, 0, 0.0, 0.0)
        for package_id, load in zip(package_ids, package_loads, strict=True)
    )
    windows = tuple(
        PackageTimeWindowRecord(package_id, activity, 1, 0.0, 1440.0)
        for package_id in package_ids
        for activity in ('pickup', 'delivery')
    )
    compatibility_records = tuple(
        VehiclePackageCompatibilityRecord('vehicle-1', package_id, compatibility)
        for package_id in package_ids
    )
    package_data = instance.package_data._replace(
        packages=packages,
        package_time_windows=windows,
        vehicle_package_compatibility=compatibility_records,
    )
    objective = instance.objective_data.objectives[0]._replace(
        penalty_scales={
            **instance.objective_data.objectives[0].penalty_scales,
            'early_window': 1.0,
            'late_window': 1.0,
        }
    )
    objective_data = instance.objective_data._replace(
        objectives=(objective,),
        penalty_functions=(
            *instance.objective_data.penalty_functions,
            PenaltyFunctionSpec(
                'vehicles-used',
                'early_window',
                '',
                'linear',
                FrozenTomlTable({'rate': 1.0}),
            ),
            PenaltyFunctionSpec(
                'vehicles-used',
                'late_window',
                '',
                'linear',
                FrozenTomlTable({'rate': 1.0}),
            ),
        ),
    )
    return instance._replace(
        package_data=package_data,
        objective_data=objective_data,
        fleet_data=instance.fleet_data._replace(
            vehicles=(
                instance.fleet_data.vehicles[0]._replace(
                    cargo_capacity=vehicle_capacity
                ),
            )
        ),
    )


def _package_plan(*operations: tuple[str, str]) -> VrpSolution:
    stops = tuple(
        ActivityStop(
            ActivityRef(cast(ActivityKind, activity), package_id),
            frozenset(),
            0.0,
            0.0,
            0.0,
            False,
        )
        for activity, package_id in operations
    )
    return VrpSolution(
        routes=(
            VehicleDayRoute(
                'vehicle-1',
                1,
                'depot-1',
                0.0,
                'driver-1',
                stops,
                tuple(RouteLeg(()) for _ in range(len(stops) + 1)),
            ),
        ),
        transfer_steps=(),
    )


def test_delivery_releases_capacity_for_a_later_pickup(tmp_path: Path) -> None:
    instance = _package_instance(
        tmp_path, package_loads=(5.0, 5.0), vehicle_capacity=5.0
    )
    solution = _package_plan(
        ('pickup', 'package-1'),
        ('delivery', 'package-1'),
        ('pickup', 'package-2'),
        ('delivery', 'package-2'),
    )

    result = VrpApplicationEvaluator(instance).evaluate(
        solution, build_vrp_objective_declaration(instance)
    )

    assert result.feasible is True


@pytest.mark.parametrize(
    ('vehicle_capacity', 'compatibility'),
    [(4.0, True), (5.0, False)],
)
def test_package_resource_violation_returns_hard_infeasibility(
    tmp_path: Path,
    vehicle_capacity: float,
    compatibility: bool,
) -> None:
    instance = _package_instance(
        tmp_path,
        package_loads=(5.0,),
        vehicle_capacity=vehicle_capacity,
        compatibility=compatibility,
    )

    result = VrpApplicationEvaluator(instance).evaluate(
        _package_plan(('pickup', 'package-1'), ('delivery', 'package-1')),
        build_vrp_objective_declaration(instance),
    )

    assert result.feasible is False
    assert result.objective_values == (inf,)
    assert isinstance(result.analysis, VrpOperatorAnalysis)
    assert result.analysis.hard_failure is not None
    assert result.analysis.hard_failure.kind == 'static_constraint'


def test_first_missing_activity_follows_instance_package_order(tmp_path: Path) -> None:
    instance = _package_instance(
        tmp_path, package_loads=(1.0, 1.0), vehicle_capacity=2.0
    )
    package_ids = ('package-first', 'package-second')
    for index in range(100):
        package_ids = (f'package-first-{index}', f'package-second-{index}')
        insertion_order = (
            ActivityRef('pickup', package_ids[0]),
            ActivityRef('pickup', package_ids[1]),
            ActivityRef('delivery', package_ids[0]),
            ActivityRef('delivery', package_ids[1]),
        )
        if next(iter(set(insertion_order))) != ActivityRef('pickup', package_ids[0]):
            break
    else:
        pytest.fail('could not construct a deterministic set-order regression case')

    packages = tuple(
        package._replace(package_id=package_id)
        for package, package_id in zip(
            instance.package_data.packages, package_ids, strict=True
        )
    )
    instance = instance._replace(
        package_data=instance.package_data._replace(
            packages=packages,
            package_time_windows=tuple(
                window._replace(package_id=package_id)
                for package_id in package_ids
                for window in instance.package_data.package_time_windows[:2]
            ),
            vehicle_package_compatibility=tuple(
                record._replace(package_id=package_id)
                for package_id in package_ids
                for record in instance.package_data.vehicle_package_compatibility[:1]
            ),
        )
    )

    result = VrpApplicationEvaluator(instance).evaluate(
        VrpSolution((), ()), build_vrp_objective_declaration(instance)
    )

    assert result.feasible is False
    analysis = result.analysis
    assert isinstance(analysis, VrpOperatorAnalysis)
    assert analysis.hard_failure is not None
    assert package_ids[0] in analysis.hard_failure.message


def test_intervention_tools_consume_capacity_for_their_depot_segment(
    tmp_path: Path,
) -> None:
    instance = _package_instance(tmp_path, package_loads=(), vehicle_capacity=5.0)
    intervention_data = instance.intervention_data._replace(
        interventions=(InterventionRecord('service-1', 'node-a', 0.0, 1),),
        intervention_time_windows=(
            InterventionTimeWindowRecord('service-1', 1, 1, 0.0, 1440.0),
        ),
        tools=(ToolRecord('special-kit', 6.0),),
        intervention_tools=(InterventionToolRecord('service-1', 'special-kit'),),
    )
    instance = instance._replace(intervention_data=intervention_data)
    solution = VrpSolution(
        routes=(
            VehicleDayRoute(
                'vehicle-1',
                1,
                'depot-1',
                0.0,
                'driver-1',
                (
                    ActivityStop(
                        ActivityRef('intervention', 'service-1'),
                        frozenset({'driver-1'}),
                        0.0,
                        0.0,
                        0.0,
                        False,
                    ),
                ),
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


def test_initial_transfer_facility_inventory_obeys_storage_capacity(
    tmp_path: Path,
) -> None:
    instance = _package_instance(
        tmp_path, package_loads=(4.0, 4.0), vehicle_capacity=10.0
    )
    packages = tuple(
        package._replace(pickup_node_id='node-a')
        for package in instance.package_data.packages
    )
    instance = instance._replace(
        settings=instance.settings._replace(service_coverage_mode='penalized_omission'),
        package_data=instance.package_data._replace(packages=packages),
        network_data=instance.network_data._replace(
            transfer_facilities=(TransferFacilityRecord('node-a', 7.0),)
        ),
    )

    result = VrpApplicationEvaluator(instance).evaluate(
        VrpSolution((), ()), build_vrp_objective_declaration(instance)
    )

    assert result.feasible is False
    assert result.objective_values == (inf,)


def test_simultaneous_transfer_operations_are_processed_in_custody_order(
    tmp_path: Path,
) -> None:
    instance = _package_instance(tmp_path, package_loads=(1.0,), vehicle_capacity=5.0)
    package = instance.package_data.packages[0]._replace(
        delivery_node_id='node-b', max_transfers=1
    )
    vehicle_2 = instance.fleet_data.vehicles[0]._replace(
        vehicle_id='vehicle-2', initial_depot_id='depot-2'
    )
    depot_2 = instance.network_data.depots[0]._replace(
        depot_id='depot-2', node_id='node-b'
    )
    instance = instance._replace(
        network_data=instance.network_data._replace(
            depots=(*instance.network_data.depots, depot_2),
            transfer_facilities=(TransferFacilityRecord('node-b', 2.0),),
        ),
        fleet_data=instance.fleet_data._replace(
            vehicles=(*instance.fleet_data.vehicles, vehicle_2),
            vehicle_overnight_depots=(
                *instance.fleet_data.vehicle_overnight_depots,
                VehicleOvernightDepotRecord('vehicle-1', 'depot-2'),
                VehicleOvernightDepotRecord('vehicle-2', 'depot-2'),
            ),
        ),
        package_data=instance.package_data._replace(
            packages=(package,),
            package_transfer_facilities=(
                PackageTransferFacilityRecord('package-1', 'node-b'),
            ),
            vehicle_package_compatibility=(
                *instance.package_data.vehicle_package_compatibility,
                VehiclePackageCompatibilityRecord('vehicle-2', 'package-1', True),
            ),
        ),
        personnel_data=instance.personnel_data._replace(
            personnel=(
                *instance.personnel_data.personnel,
                PersonRecord('driver-2', True, None),
            )
        ),
    )
    transfer_ref = TransferStepRef('package-1', 1)
    transfer_in = TransferOperationStop(transfer_ref, 'in', 0.0, 0.0, 0.0, False)
    transfer_out = TransferOperationStop(transfer_ref, 'out', 0.0, 0.0, 0.0, False)
    solution = VrpSolution(
        routes=(
            VehicleDayRoute(
                'vehicle-1',
                1,
                'depot-2',
                0.0,
                'driver-1',
                (
                    ActivityStop(
                        ActivityRef('pickup', 'package-1'),
                        frozenset(),
                        0.0,
                        0.0,
                        0.0,
                        False,
                    ),
                    transfer_out,
                ),
                (RouteLeg(()), RouteLeg(('arc-1',)), RouteLeg(())),
            ),
            VehicleDayRoute(
                'vehicle-2',
                1,
                'depot-2',
                5.0,
                'driver-2',
                (
                    transfer_in,
                    ActivityStop(
                        ActivityRef('delivery', 'package-1'),
                        frozenset(),
                        0.0,
                        0.0,
                        0.0,
                        False,
                    ),
                ),
                (RouteLeg(()), RouteLeg(()), RouteLeg(())),
            ),
        ),
        transfer_steps=(TransferStep('package-1', 1, 'node-b'),),
    )

    result = VrpApplicationEvaluator(instance).evaluate(
        solution, build_vrp_objective_declaration(instance)
    )

    assert result.feasible is True
