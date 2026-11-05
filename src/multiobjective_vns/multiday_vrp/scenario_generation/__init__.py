"""Standalone scenario generation from synchronized historical travel times.

Created: 2026-11-03T17:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.multiday_vrp.scenario_generation.api import (
    generate_scenario_bank,
)
from multiobjective_vns.multiday_vrp.scenario_generation.errors import (
    ScenarioGenerationError,
    ScenarioInputError,
    ScenarioReductionError,
)
from multiobjective_vns.multiday_vrp.scenario_generation.records import (
    AlignedHistoricalData,
    BlockImputationSummary,
    CandidateAssignment,
    ExcludedBlock,
    FifoAdjustment,
    FifoAdjustmentSummary,
    FifoWeightKey,
    FiniteNumber,
    GenerationReport,
    GenerationResult,
    HistoricalCoordinate,
    HistoricalDateCoverage,
    ImputationRequest,
    ImputationSummary,
    InterpolationRecord,
    JointImputer,
    PostHorizonRule,
    ProfileCoordinate,
    ProtectedTrafficGroup,
    ScenarioGenerationOptions,
    ScenarioProbability,
    SourceDataCoverage,
    SourceLabel,
)

__all__ = [
    'AlignedHistoricalData',
    'BlockImputationSummary',
    'CandidateAssignment',
    'ExcludedBlock',
    'FiniteNumber',
    'FifoAdjustment',
    'FifoAdjustmentSummary',
    'FifoWeightKey',
    'GenerationReport',
    'GenerationResult',
    'HistoricalCoordinate',
    'HistoricalDateCoverage',
    'ImputationRequest',
    'ImputationSummary',
    'InterpolationRecord',
    'JointImputer',
    'PostHorizonRule',
    'ProfileCoordinate',
    'ProtectedTrafficGroup',
    'ScenarioGenerationError',
    'ScenarioGenerationOptions',
    'ScenarioInputError',
    'ScenarioProbability',
    'ScenarioReductionError',
    'SourceDataCoverage',
    'SourceLabel',
    'generate_scenario_bank',
]
