"""Tests for the public multi-day VRP solution exports.

Created: 2026-11-03T16:02:26+01:00
Author: Michel Bierlaire, EPFL
"""

from typing import get_args

from multiobjective_vns.multiday_vrp import (
    ActivityRef,
    ActivityStop,
    DepotOnlyStop,
    HardFeasibilityFinding,
    NodeCoordinateRecord,
    RouteLeg,
    TransferOperationStop,
    TransferStep,
    TransferStepRef,
    VehicleDayRoute,
    VrpApplicationEvaluator,
    VrpExtensionRegistry,
    VrpFeatureId,
    VrpInputError,
    VrpObjectiveFunction,
    VrpOperatorCatalog,
    VrpSolution,
    VrpSolutionCodec,
    VrpSpatialData,
    build_vrp_objective_declaration,
    load_vrp_instance,
)
from multiobjective_vns.multiday_vrp.operators import (
    DaganzoStripSweepRouteRebuildOperator,
)


def test_solution_record_types_are_exported_from_the_application_subpackage() -> None:
    assert ActivityRef.__name__ == 'ActivityRef'
    assert ActivityStop.__name__ == 'ActivityStop'
    assert DepotOnlyStop.__name__ == 'DepotOnlyStop'
    assert RouteLeg.__name__ == 'RouteLeg'
    assert TransferOperationStop.__name__ == 'TransferOperationStop'
    assert TransferStep.__name__ == 'TransferStep'
    assert TransferStepRef.__name__ == 'TransferStepRef'
    assert VehicleDayRoute.__name__ == 'VehicleDayRoute'
    assert VrpSolution.__name__ == 'VrpSolution'
    assert VrpSolutionCodec.__name__ == 'VrpSolutionCodec'
    assert VrpExtensionRegistry.__name__ == 'VrpExtensionRegistry'
    assert VrpInputError.__name__ == 'VrpInputError'
    assert 'directed_route_paths' in get_args(VrpFeatureId)
    assert load_vrp_instance.__name__ == 'load_vrp_instance'
    assert build_vrp_objective_declaration.__name__ == (
        'build_vrp_objective_declaration'
    )
    assert VrpApplicationEvaluator.__name__ == 'VrpApplicationEvaluator'
    assert HardFeasibilityFinding.__name__ == 'HardFeasibilityFinding'
    assert VrpObjectiveFunction.__name__ == 'VrpObjectiveFunction'
    assert VrpOperatorCatalog.__name__ == 'VrpOperatorCatalog'
    assert NodeCoordinateRecord.__name__ == 'NodeCoordinateRecord'
    assert VrpSpatialData.__name__ == 'VrpSpatialData'
    assert DaganzoStripSweepRouteRebuildOperator.__name__ == (
        'DaganzoStripSweepRouteRebuildOperator'
    )
