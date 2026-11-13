"""Shared utilities and application Operators for the multi-day VRP.

Created: 2026-11-03T18:58:00+01:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.multiday_vrp.operators.activity_relocation import (
    ActivityRelocationMode,
    ActivityRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators.aggregation_contribution import (
    AggregationContributionOperator,
)
from multiobjective_vns.multiday_vrp.operators.catalog import (
    VrpOperatorCatalog,
    VrpOperatorCatalogSettings,
    VrpOperatorId,
    VrpOperatorSpecId,
    build_default_vrp_operator_catalog,
)
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    candidate_is_eligible,
    choose_uniformly,
    operator_result,
    randomized_order,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.depot_visit import (
    DepotVisitMode,
    DepotVisitOperator,
)
from multiobjective_vns.multiday_vrp.operators.driver_reassignment import (
    DriverReassignmentOperator,
)
from multiobjective_vns.multiday_vrp.operators.driver_schedule_repair import (
    DriverScheduleDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.feature_path import (
    EventObjectiveRouteRerouteOperator,
    ScenarioDifferentialRerouteOperator,
    TimeWindowGuidedRouteRerouteOperator,
)
from multiobjective_vns.multiday_vrp.operators.feature_timing import (
    FeatureTimingMode,
    FeatureTimingOperator,
)
from multiobjective_vns.multiday_vrp.operators.package_capacity import (
    CapacityAwarePackageChainRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators.package_chain import (
    PackageChainMode,
    PackageChainRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators.pareto_tradeoff import (
    ParetoTradeoffOperator,
)
from multiobjective_vns.multiday_vrp.operators.personnel_features import (
    InterventionTeamReassignmentOperator,
    TeamAssignmentMode,
)
from multiobjective_vns.multiday_vrp.operators.personnel_repair import (
    PersonnelRepairMode,
    PersonnelRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.relationship_repair import (
    RelationshipGuidedServiceRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.route_order import (
    ExchangeOperator,
    OrOptChainRelocationOperator,
    RelocateOperator,
    SwapStarOperator,
    TwoOptOperator,
    TwoOptStarOperator,
)
from multiobjective_vns.multiday_vrp.operators.route_path import (
    AdditivePathCostCallback,
    AdditivePathCostRegistration,
    AdditivePathCostRequest,
    ObjectiveSpecificShortestPathOperator,
    RouteLegRerouteOperator,
)
from multiobjective_vns.multiday_vrp.operators.schedule_features import (
    DriverSwapCycleOperator,
    MultiDayDepotReassignmentOperator,
    OvernightDepotChangeOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_coverage import (
    ServiceCoverageChangeOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_cluster import (
    WindowCompatiblePackageClusterReassignmentOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_common import (
    select_window_first_candidates,
)
from multiobjective_vns.multiday_vrp.operators.service_window_ejection_chain import (
    WindowPrioritizedEjectionChainConsolidationOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_regret import (
    TimeWindowRegretBasedReinsertionOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_repair import (
    TimeWindowFocusedDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_route_cluster import (
    SpatialTimeWindowRouteClusterRebuildOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_schedule import (
    TimeWindowScheduleComplianceOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_structural import (
    SlackGuidedOrOptRelocationOperator,
    WindowFeasibleSwapStarOperator,
)
from multiobjective_vns.multiday_vrp.operators.service_window_tour import (
    ObjectiveGuidedBacktrackingTourConstructionOperator,
    ObjectiveGuidedFeasibleDeliveryEdgeExchangeOperator,
    ObjectiveGuidedTourRegretInsertionOperator,
    TimeWindowFeasibleTourInsertionOperator,
)
from multiobjective_vns.multiday_vrp.operators.spatial_strip_sweep import (
    DaganzoStripSweepRouteRebuildOperator,
)
from multiobjective_vns.multiday_vrp.operators.timing import TimingAdjustmentOperator
from multiobjective_vns.multiday_vrp.operators.tool_capacity import (
    ToolReservationGuidedInterventionRelocationOperator,
)
from multiobjective_vns.multiday_vrp.operators.tour_boundaries import (
    MergeToursOperator,
    SplitTourOperator,
)
from multiobjective_vns.multiday_vrp.operators.transfer_facility import (
    TransferFacilityAssignmentOperator,
)
from multiobjective_vns.multiday_vrp.operators.transfer_step import (
    TransferStepMode,
    TransferStepOperator,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_elimination import (
    VehicleEliminationDestroyRepairOperator,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_consolidation import (
    VehicleRouteConsolidationOperator,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_split import (
    VehicleRouteSplitOperator,
)

__all__ = [
    'VrpOperatorCatalog',
    'VrpPlanAssessor',
    'AggregationContributionOperator',
    'CapacityAwarePackageChainRelocationOperator',
    'VrpOperatorCatalogSettings',
    'VrpOperatorId',
    'DaganzoStripSweepRouteRebuildOperator',
    'VrpOperatorSpecId',
    'ActivityRelocationMode',
    'ActivityRelocationOperator',
    'AdditivePathCostCallback',
    'AdditivePathCostRegistration',
    'AdditivePathCostRequest',
    'DriverReassignmentOperator',
    'DriverScheduleDestroyRepairOperator',
    'DriverSwapCycleOperator',
    'MergeToursOperator',
    'DepotVisitMode',
    'DepotVisitOperator',
    'ExchangeOperator',
    'EventObjectiveRouteRerouteOperator',
    'FeatureTimingMode',
    'FeatureTimingOperator',
    'InterventionTeamReassignmentOperator',
    'MultiDayDepotReassignmentOperator',
    'OvernightDepotChangeOperator',
    'PackageChainMode',
    'PackageChainRelocationOperator',
    'ParetoTradeoffOperator',
    'PersonnelRepairMode',
    'PersonnelRepairOperator',
    'RelationshipGuidedServiceRepairOperator',
    'ObjectiveSpecificShortestPathOperator',
    'OrOptChainRelocationOperator',
    'RelocateOperator',
    'RouteLegRerouteOperator',
    'ScenarioDifferentialRerouteOperator',
    'ServiceCoverageChangeOperator',
    'SwapStarOperator',
    'TimingAdjustmentOperator',
    'ToolReservationGuidedInterventionRelocationOperator',
    'TransferStepMode',
    'TransferStepOperator',
    'TimeWindowRegretBasedReinsertionOperator',
    'TimeWindowScheduleComplianceOperator',
    'ObjectiveGuidedTourRegretInsertionOperator',
    'ObjectiveGuidedBacktrackingTourConstructionOperator',
    'ObjectiveGuidedFeasibleDeliveryEdgeExchangeOperator',
    'TimeWindowFeasibleTourInsertionOperator',
    'SlackGuidedOrOptRelocationOperator',
    'WindowFeasibleSwapStarOperator',
    'WindowCompatiblePackageClusterReassignmentOperator',
    'SpatialTimeWindowRouteClusterRebuildOperator',
    'WindowPrioritizedEjectionChainConsolidationOperator',
    'select_window_first_candidates',
    'VehicleEliminationDestroyRepairOperator',
    'VehicleRouteConsolidationOperator',
    'VehicleRouteSplitOperator',
    'TeamAssignmentMode',
    'TimeWindowGuidedRouteRerouteOperator',
    'TimeWindowFocusedDestroyRepairOperator',
    'SplitTourOperator',
    'TransferFacilityAssignmentOperator',
    'TwoOptOperator',
    'TwoOptStarOperator',
    'candidate_is_eligible',
    'build_default_vrp_operator_catalog',
    'choose_uniformly',
    'operator_result',
    'randomized_order',
    'randomized_sample',
    'require_vrp_base',
    'validate_neighborhood_size',
]
