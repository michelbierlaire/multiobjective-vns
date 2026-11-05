"""Immutable VRP feasibility and objective-analysis records.

Created: 2026-11-03T18:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from multiobjective_vns.multiday_vrp.extensions import VrpScenarioEvaluation
from multiobjective_vns.multiday_vrp.solution import ActivityRef

type ResourceFindingKind = Literal[
    'vehicle_availability',
    'personnel_availability',
    'duty_overlap',
    'cargo_capacity',
    'transfer_storage',
    'tool_capacity',
    'driver_work',
    'driving_time',
    'break',
    'rest',
    'hos',
]
type HardFeasibilityFindingKind = Literal[
    'static_constraint', 'scenario_constraint', 'compliance_rule'
]

__all__ = [
    'HardFeasibilityFinding',
    'HardFeasibilityFindingKind',
    'ResourceFinding',
    'ResourceFindingKind',
    'ScenarioObjectiveCost',
    'VrpOperatorAnalysis',
]


@dataclass(frozen=True, slots=True)
class HardFeasibilityFinding:
    """The first hard requirement that stopped VRP plan evaluation.

    :param kind: Whether the failed check is plan-level, scenario-level, or a
        compliance-treated rule.
    :param message: Human-readable description with relevant identifiers.
    :param scenario_id: Scenario where the violation occurred, or ``None`` for
        a plan-level check.
    """

    kind: HardFeasibilityFindingKind
    message: str
    scenario_id: str | None

    def __post_init__(self) -> None:
        if self.kind not in {
            'static_constraint',
            'scenario_constraint',
            'compliance_rule',
        }:
            raise ValueError('kind is not a supported HardFeasibilityFindingKind')
        _require_identifier(self.message, 'message')
        if self.scenario_id is not None:
            _require_identifier(self.scenario_id, 'scenario_id')


@dataclass(frozen=True, slots=True)
class ResourceFinding:
    """One nonnegative resource or rule finding available for Operator guidance.

    :param kind: Closed category identifying the assessed resource or rule.
    :param resource_id: Stable ID of the affected resource or rule.
    :param scenario_id: Scenario ID, or ``None`` for scenario-independent checks.
    :param vehicle_id: Affected vehicle ID when applicable.
    :param day_index: One-based day index when applicable.
    :param activity_ref: Affected service activity when applicable.
    :param measure: Nonnegative violation amount in the model's declared unit.
    :param penalty_by_objective: Objective-specific scaled guidance values.
    """

    kind: ResourceFindingKind
    resource_id: str
    scenario_id: str | None
    vehicle_id: str | None
    day_index: int | None
    activity_ref: ActivityRef | None
    measure: float
    penalty_by_objective: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if self.kind not in {
            'vehicle_availability',
            'personnel_availability',
            'duty_overlap',
            'cargo_capacity',
            'transfer_storage',
            'tool_capacity',
            'driver_work',
            'driving_time',
            'break',
            'rest',
            'hos',
        }:
            raise ValueError('kind is not a supported ResourceFindingKind')
        _require_identifier(self.resource_id, 'resource_id')
        if self.scenario_id is not None:
            _require_identifier(self.scenario_id, 'scenario_id')
        if self.vehicle_id is not None:
            _require_identifier(self.vehicle_id, 'vehicle_id')
        if self.day_index is not None and (
            type(self.day_index) is not int or self.day_index < 1
        ):
            raise ValueError('day_index must be a positive integer or None')
        if self.activity_ref is not None and type(self.activity_ref) is not ActivityRef:
            raise TypeError('activity_ref must be ActivityRef or None')
        _require_finite_nonnegative(self.measure, 'measure')
        if type(self.penalty_by_objective) is not tuple:
            raise TypeError('penalty_by_objective must be a tuple')
        seen_objectives: set[str] = set()
        for pair in self.penalty_by_objective:
            if type(pair) is not tuple or len(pair) != 2:
                raise TypeError('penalty entries must be (objective_id, value) tuples')
            objective_id, value = pair
            _require_identifier(objective_id, 'objective_id')
            if objective_id in seen_objectives:
                raise ValueError('penalty_by_objective objective IDs must be unique')
            _require_finite_nonnegative(value, 'objective penalty guidance')
            seen_objectives.add(objective_id)


@dataclass(frozen=True, slots=True)
class ScenarioObjectiveCost:
    """One objective's complete scenario cost before scenario aggregation."""

    objective_id: str
    scenario_id: str
    value: float

    def __post_init__(self) -> None:
        _require_identifier(self.objective_id, 'objective_id')
        _require_identifier(self.scenario_id, 'scenario_id')
        if type(self.value) is not float or not isfinite(self.value):
            raise ValueError('scenario objective cost must be a finite Python float')


@dataclass(frozen=True, slots=True)
class VrpOperatorAnalysis:
    """Immutable snapshot of evaluation findings that can guide VRP Operators."""

    is_complete: bool
    scenario_evaluations: tuple[VrpScenarioEvaluation, ...]
    resource_findings: tuple[ResourceFinding, ...]
    scenario_objective_costs: tuple[ScenarioObjectiveCost, ...]
    hard_failure: HardFeasibilityFinding | None = None

    def __post_init__(self) -> None:
        if type(self.is_complete) is not bool:
            raise TypeError('is_complete must be bool')
        if (
            self.hard_failure is not None
            and type(self.hard_failure) is not HardFeasibilityFinding
        ):
            raise TypeError('hard_failure must be HardFeasibilityFinding or None')
        if self.is_complete and self.hard_failure is not None:
            raise ValueError('complete analysis cannot contain a hard failure')
        if not self.is_complete and self.hard_failure is None:
            raise ValueError('incomplete analysis requires a hard failure')
        _require_tuple_items(
            self.scenario_evaluations,
            'scenario_evaluations',
            VrpScenarioEvaluation,
        )
        _require_tuple_items(
            self.resource_findings, 'resource_findings', ResourceFinding
        )
        _require_tuple_items(
            self.scenario_objective_costs,
            'scenario_objective_costs',
            ScenarioObjectiveCost,
        )


def _require_identifier(value: object, field_name: str) -> None:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(f'{field_name} must be a nonempty unpadded string')


def _require_finite_nonnegative(value: object, field_name: str) -> None:
    if type(value) is not float:
        raise TypeError(f'{field_name} must be a Python float')
    if not isfinite(value) or value < 0.0:
        raise ValueError(f'{field_name} must be finite and nonnegative')


def _require_tuple_items(
    value: object, field_name: str, item_type: type[object]
) -> None:
    if type(value) is not tuple:
        raise TypeError(f'{field_name} must be a tuple')
    if any(type(item) is not item_type for item in value):
        raise TypeError(f'{field_name} must contain only {item_type.__name__} records')
