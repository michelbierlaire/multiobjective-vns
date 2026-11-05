"""Tests for the VRP Operator package exports.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.multiday_vrp.operators import (
    ActivityRelocationOperator,
    AggregationContributionOperator,
    DepotVisitOperator,
    DriverScheduleDestroyRepairOperator,
    DriverSwapCycleOperator,
    EventObjectiveRouteRerouteOperator,
    FeatureTimingOperator,
    InterventionTeamReassignmentOperator,
    MergeToursOperator,
    MultiDayDepotReassignmentOperator,
    OvernightDepotChangeOperator,
    PackageChainRelocationOperator,
    ParetoTradeoffOperator,
    RelationshipGuidedServiceRepairOperator,
    ScenarioDifferentialRerouteOperator,
    ServiceCoverageChangeOperator,
    SplitTourOperator,
    TimeWindowFocusedDestroyRepairOperator,
    TimeWindowGuidedRouteRerouteOperator,
    TransferFacilityAssignmentOperator,
    VrpOperatorCatalogSettings,
    VrpOperatorId,
    build_default_vrp_operator_catalog,
)


def test_feature_operators_are_available_from_operator_package() -> None:
    assert VrpOperatorCatalogSettings.__name__ == 'VrpOperatorCatalogSettings'
    assert VrpOperatorId.__name__ == 'VrpOperatorId'
    assert callable(build_default_vrp_operator_catalog)
    assert (
        DriverScheduleDestroyRepairOperator.__name__
        == 'DriverScheduleDestroyRepairOperator'
    )
    assert ActivityRelocationOperator.__name__ == 'ActivityRelocationOperator'
    assert DepotVisitOperator.__name__ == 'DepotVisitOperator'
    assert DriverSwapCycleOperator.__name__ == 'DriverSwapCycleOperator'
    assert (
        EventObjectiveRouteRerouteOperator.__name__
        == 'EventObjectiveRouteRerouteOperator'
    )
    assert FeatureTimingOperator.__name__ == 'FeatureTimingOperator'
    assert (
        InterventionTeamReassignmentOperator.__name__
        == 'InterventionTeamReassignmentOperator'
    )
    assert (
        MultiDayDepotReassignmentOperator.__name__
        == 'MultiDayDepotReassignmentOperator'
    )
    assert OvernightDepotChangeOperator.__name__ == 'OvernightDepotChangeOperator'
    assert PackageChainRelocationOperator.__name__ == 'PackageChainRelocationOperator'
    assert ParetoTradeoffOperator.__name__ == 'ParetoTradeoffOperator'
    assert (
        RelationshipGuidedServiceRepairOperator.__name__
        == 'RelationshipGuidedServiceRepairOperator'
    )
    assert AggregationContributionOperator.__name__ == 'AggregationContributionOperator'
    assert (
        ScenarioDifferentialRerouteOperator.__name__
        == 'ScenarioDifferentialRerouteOperator'
    )
    assert ServiceCoverageChangeOperator.__name__ == 'ServiceCoverageChangeOperator'
    assert (
        TimeWindowFocusedDestroyRepairOperator.__name__
        == 'TimeWindowFocusedDestroyRepairOperator'
    )
    assert (
        TimeWindowGuidedRouteRerouteOperator.__name__
        == 'TimeWindowGuidedRouteRerouteOperator'
    )
    assert (
        TransferFacilityAssignmentOperator.__name__
        == 'TransferFacilityAssignmentOperator'
    )
    assert SplitTourOperator.__name__ == 'SplitTourOperator'
    assert MergeToursOperator.__name__ == 'MergeToursOperator'
