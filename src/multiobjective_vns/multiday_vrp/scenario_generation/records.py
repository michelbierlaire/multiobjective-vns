"""Public records and extension contracts for scenario generation.

Created: 2026-11-03T17:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol, TypeAlias, TypeVar, cast

from multiobjective_vns.multiday_vrp.scenario_generation.errors import (
    ScenarioInputError,
)

ProfileCoordinate: TypeAlias = tuple[str, int, int]
HistoricalCoordinate: TypeAlias = tuple[date, str, int, int]
FifoWeightKey: TypeAlias = tuple[str, str, int, int]
SourceLabel: TypeAlias = Literal['observed', 'interpolated', 'missing']
FiniteNumber: TypeAlias = int | float
_Key = TypeVar('_Key')
_Value = TypeVar('_Value')

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
    'ScenarioGenerationOptions',
    'ScenarioProbability',
    'SourceDataCoverage',
    'SourceLabel',
]


def _freeze_mapping(
    mapping: Mapping[_Key, _Value],
) -> Mapping[_Key, _Value]:
    return MappingProxyType(dict(mapping))


def _finite_float(value: object, *, label: str, minimum: float | None = None) -> float:
    if type(value) not in (int, float):
        raise ScenarioInputError(
            reason=f'{label} must be a built-in int or float, excluding bool'
        )
    try:
        converted = float(cast(int | float, value))
    except (OverflowError, ValueError) as error:
        raise ScenarioInputError(
            reason=f'{label} must convert to a finite float'
        ) from error
    if not math.isfinite(converted):
        raise ScenarioInputError(reason=f'{label} must be finite')
    if minimum is not None and converted < minimum:
        raise ScenarioInputError(reason=f'{label} must be at least {minimum}')
    return converted


def _positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ScenarioInputError(
            reason=f'{label} must be a positive built-in int, excluding bool'
        )
    return value


@dataclass(frozen=True, slots=True)
class ProtectedTrafficGroup:
    """A minimum-representation requirement for named source blocks."""

    group_id: str
    block_start_dates: frozenset[str]
    minimum_representatives: int

    def __post_init__(self) -> None:
        if (
            type(self.group_id) is not str
            or not self.group_id.strip()
            or self.group_id.strip() != self.group_id
        ):
            raise ScenarioInputError(
                reason='group_id must be a nonempty string without surrounding whitespace'
            )
        dates = frozenset(self.block_start_dates)
        if not dates or any(type(item) is not str or not item for item in dates):
            raise ScenarioInputError(
                reason='block_start_dates must be a nonempty set of date strings'
            )
        for item in dates:
            try:
                if date.fromisoformat(item).isoformat() != item:
                    raise ValueError
            except ValueError as error:
                raise ScenarioInputError(
                    reason='block_start_dates must use ISO YYYY-MM-DD'
                ) from error
        _positive_int(self.minimum_representatives, label='minimum_representatives')
        object.__setattr__(self, 'block_start_dates', dates)


class JointImputer(Protocol):
    """Jointly impute unresolved coordinates for an eligible historical block."""

    extension_id: str
    configuration_id: str

    def fit(
        self,
        aligned_history: AlignedHistoricalData,
        random_source: random.Random,
    ) -> None: ...

    def impute_missing(
        self,
        request: ImputationRequest,
        random_source: random.Random,
    ) -> Mapping[ProfileCoordinate, FiniteNumber]: ...


class PostHorizonRule(Protocol):
    """Provide travel times for departures after the planning horizon."""

    rule_id: str
    configuration_id: str

    def travel_time_at(
        self,
        arc_id: str,
        departure_time: datetime,
        final_boundary_time: datetime,
        final_boundary_profile: Mapping[str, float],
        time_unit: str,
    ) -> FiniteNumber: ...


@dataclass(frozen=True, slots=True)
class AlignedHistoricalData:
    """Dense immutable history indexed by date, arc, day position, and boundary."""

    time_zone: str
    time_unit: str
    historical_dates: tuple[date, ...]
    arc_ids: tuple[str, ...]
    target_boundary_times: Mapping[tuple[int, int], datetime]
    values: Mapping[HistoricalCoordinate, float | None]
    source: Mapping[HistoricalCoordinate, SourceLabel]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, 'target_boundary_times', _freeze_mapping(self.target_boundary_times)
        )
        object.__setattr__(self, 'values', _freeze_mapping(self.values))
        object.__setattr__(self, 'source', _freeze_mapping(self.source))


@dataclass(frozen=True, slots=True)
class ImputationRequest:
    """Complete immutable values and source labels for one candidate block."""

    block_start_date: str
    values: Mapping[ProfileCoordinate, float | None]
    source: Mapping[ProfileCoordinate, SourceLabel]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'values', _freeze_mapping(self.values))
        object.__setattr__(self, 'source', _freeze_mapping(self.source))


@dataclass(frozen=True, slots=True)
class ScenarioGenerationOptions:
    """Per-call extension objects, weights, and protected-group constraints.

    Mapping arguments and group sequences are copied into immutable snapshots
    during construction.
    """

    joint_imputer: JointImputer | None = None
    fifo_projection_weights: Mapping[FifoWeightKey, FiniteNumber] = field(
        default_factory=dict
    )
    scenario_distance_weights: Mapping[ProfileCoordinate, FiniteNumber] = field(
        default_factory=dict
    )
    protected_traffic_groups: Sequence[ProtectedTrafficGroup] = ()
    post_horizon_rules: Mapping[tuple[str, str], PostHorizonRule] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        fifo_weights: dict[FifoWeightKey, float] = {}
        for key, value in self.fifo_projection_weights.items():
            if (
                type(key) is not tuple
                or len(key) != 4
                or type(key[0]) is not str
                or type(key[1]) is not str
                or not key[0].strip()
                or key[0].strip() != key[0]
                or not key[1].strip()
                or key[1].strip() != key[1]
                or type(key[2]) is not int
                or type(key[3]) is not int
                or key[2] < 1
                or key[3] < 0
            ):
                raise ScenarioInputError(reason='invalid FIFO projection weight key')
            fifo_weights[key] = _finite_float(value, label='FIFO weight', minimum=0.0)
            if fifo_weights[key] == 0.0:
                raise ScenarioInputError(reason='FIFO weight must be positive')
        distance_weights: dict[ProfileCoordinate, float] = {}
        for distance_key, value in self.scenario_distance_weights.items():
            if (
                type(distance_key) is not tuple
                or len(distance_key) != 3
                or type(distance_key[0]) is not str
                or not distance_key[0].strip()
                or distance_key[0].strip() != distance_key[0]
                or type(distance_key[1]) is not int
                or type(distance_key[2]) is not int
                or distance_key[1] < 1
                or distance_key[2] < 0
            ):
                raise ScenarioInputError(reason='invalid scenario-distance weight key')
            distance_weights[distance_key] = _finite_float(
                value, label='scenario-distance weight', minimum=0.0
            )
        rules = dict(self.post_horizon_rules)
        for rule_key in rules:
            if (
                type(rule_key) is not tuple
                or len(rule_key) != 2
                or any(
                    type(part) is not str or not part.strip() or part.strip() != part
                    for part in rule_key
                )
            ):
                raise ScenarioInputError(
                    reason='post-horizon rule keys must be nonempty ID pairs'
                )
        groups = tuple(self.protected_traffic_groups)
        if any(not isinstance(group, ProtectedTrafficGroup) for group in groups):
            raise ScenarioInputError(
                reason='protected_traffic_groups must contain ProtectedTrafficGroup'
            )
        group_ids = tuple(group.group_id for group in groups)
        if len(set(group_ids)) != len(group_ids):
            raise ScenarioInputError(
                reason='protected traffic group IDs must be unique'
            )
        object.__setattr__(
            self, 'fifo_projection_weights', MappingProxyType(fifo_weights)
        )
        object.__setattr__(
            self, 'scenario_distance_weights', MappingProxyType(distance_weights)
        )
        object.__setattr__(self, 'post_horizon_rules', MappingProxyType(rules))
        object.__setattr__(self, 'protected_traffic_groups', groups)


@dataclass(frozen=True, slots=True)
class HistoricalDateCoverage:
    """Aligned-value counts for one historical local-calendar date."""

    historical_date: date
    expected_value_count: int
    observed_value_count: int
    interpolated_value_count: int
    unresolved_missing_value_count: int


@dataclass(frozen=True, slots=True)
class SourceDataCoverage:
    """Aggregate aligned-value counts and their per-date breakdown."""

    expected_value_count: int
    observed_value_count: int
    interpolated_value_count: int
    unresolved_missing_value_count: int
    by_date: tuple[HistoricalDateCoverage, ...]


@dataclass(frozen=True, slots=True)
class InterpolationRecord:
    """One interpolation and the two observations used to calculate it."""

    coordinate: HistoricalCoordinate
    interpolated_value: float
    left_observation_time: datetime
    left_observation_value: float
    right_observation_time: datetime
    right_observation_value: float


@dataclass(frozen=True, slots=True)
class BlockImputationSummary:
    """Number of missing and imputed coordinates for one source block."""

    block_start_date: str
    missing_value_count: int
    imputed_value_count: int


@dataclass(frozen=True, slots=True)
class ImputationSummary:
    """Total imputed coordinates and the per-block summary."""

    imputed_value_count: int
    by_block: tuple[BlockImputationSummary, ...]


@dataclass(frozen=True, slots=True)
class ExcludedBlock:
    """One date-selected block excluded before profile construction."""

    block_start_date: str
    reason: Literal['incomplete_date_span', 'unresolved_missing_values']
    missing_dates: tuple[date, ...]
    missing_coordinates: tuple[HistoricalCoordinate, ...]


@dataclass(frozen=True, slots=True)
class FifoAdjustment:
    """Travel-time boundary value changed by FIFO projection."""

    block_start_date: str
    arc_id: str
    day_index: int
    boundary_index: int
    original_travel_time: float
    projected_travel_time: float
    absolute_change: float


@dataclass(frozen=True, slots=True)
class FifoAdjustmentSummary:
    """Aggregate and coordinate-level effects of FIFO projection."""

    maximum_absolute_change: float
    total_absolute_change: float
    adjustments: tuple[FifoAdjustment, ...]


@dataclass(frozen=True, slots=True)
class CandidateAssignment:
    """Probability-mass assignment from one coalesced candidate profile."""

    source_block_start_dates: tuple[str, ...]
    source_probability: float
    retained_scenario_id: str
    retained: bool
    transferred_probability_mass: float


@dataclass(frozen=True, slots=True)
class ScenarioProbability:
    """Identifier and final normalized probability of one retained scenario."""

    scenario_id: str
    probability: float


@dataclass(frozen=True, slots=True)
class GenerationReport:
    """Immutable provenance, data-quality, FIFO, and reduction report."""

    report_version: int
    time_zone: str
    time_unit: str
    requested_scenario_count: int
    maximum_interpolation_gap: float
    random_seed: int
    joint_imputer_id: str | None
    joint_imputer_configuration_id: str | None
    post_horizon_rule_id: str
    post_horizon_rule_configuration_id: str
    date_selected_block_start_dates: tuple[str, ...]
    eligible_block_start_dates: tuple[str, ...]
    source_data_coverage: SourceDataCoverage
    interpolations: tuple[InterpolationRecord, ...]
    imputation_summary: ImputationSummary
    effective_fifo_projection_weights: Mapping[FifoWeightKey, float]
    unused_fifo_projection_weight_keys: tuple[FifoWeightKey, ...]
    effective_scenario_distance_weights: Mapping[ProfileCoordinate, float]
    protected_traffic_groups: tuple[ProtectedTrafficGroup, ...]
    protected_group_representatives: Mapping[str, tuple[str, ...]]
    fifo_adjustments: FifoAdjustmentSummary
    excluded_blocks: tuple[ExcludedBlock, ...]
    candidate_profile_count: int
    candidate_scenario_count: int
    retained_scenario_count: int
    candidate_assignments: tuple[CandidateAssignment, ...]
    reduction_cost: float
    scenario_probabilities: tuple[ScenarioProbability, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'effective_fifo_projection_weights',
            _freeze_mapping(self.effective_fifo_projection_weights),
        )
        object.__setattr__(
            self,
            'effective_scenario_distance_weights',
            _freeze_mapping(self.effective_scenario_distance_weights),
        )
        object.__setattr__(
            self,
            'protected_group_representatives',
            _freeze_mapping(self.protected_group_representatives),
        )


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Published ScenarioBank directory and its structured report."""

    output_directory: Path
    report: GenerationReport
