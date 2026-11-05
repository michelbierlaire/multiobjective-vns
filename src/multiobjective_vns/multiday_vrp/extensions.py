"""Typed application extension contracts for multi-day VRP instances.

Created: 2026-11-03T16:30:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, NamedTuple, Protocol, cast

from multiobjective_vns.multiday_vrp.input_records import (
    FrozenTomlTable,
    HosRuleDefinition,
    LoadedVrpInstance,
    VrpPersonnelData,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    TransferStepRef,
    VrpSolution,
)

type ViolationCategory = Literal[
    'early_window',
    'late_window',
    'vehicle_availability',
    'personnel_availability',
    'duty_overlap',
    'simple_rule',
    'hos_rule',
]
type ExtensionFamily = Literal[
    'base_measure',
    'scenario_aggregation',
    'penalty_function',
    'penalty_aggregation',
    'hos_regime',
]
type ObjectiveExtensionFamily = Literal[
    'base_measure',
    'scenario_aggregation',
    'penalty_function',
    'penalty_aggregation',
]
type ExtensionMethod = Literal['evaluate', 'aggregate', 'evaluate_scenario']
type ConfigurationMethod = Literal['validate_parameters', 'validate_configuration']

__all__ = [
    'ArcTraversalEvaluation',
    'BaseMeasureCallback',
    'BaseMeasureRegistration',
    'BaseMeasureRequest',
    'ConfigurationMethod',
    'ExtensionFamily',
    'ExtensionMethod',
    'ExtensionParametersRequest',
    'HosConfigurationRequest',
    'HosConfigurationValidator',
    'HosRegimeRegistration',
    'HosRuleFinding',
    'HosScenarioCallback',
    'HosScenarioRequest',
    'LegScenarioEvaluation',
    'ObjectiveExtensionFamily',
    'PenaltyAggregationCallback',
    'PenaltyAggregationRegistration',
    'PenaltyAggregationRequest',
    'PenaltyCategoryInput',
    'PenaltyFunctionCallback',
    'PenaltyFunctionRegistration',
    'PenaltyFunctionRequest',
    'ScenarioAggregationCallback',
    'ScenarioAggregationRegistration',
    'ScenarioAggregationRequest',
    'ScenarioObjectiveInput',
    'StopScenarioEvaluation',
    'ValidateExtensionParameters',
    'VehicleDayScenarioEvaluation',
    'ViolationCategory',
    'VrpExtensionConfigurationError',
    'VrpExtensionEvaluationError',
    'VrpExtensionRegistry',
    'VrpExtensionRegistryError',
    'VrpScenarioEvaluation',
    'WindowScenarioAssessment',
    'empty_vrp_extension_registry',
]


class VrpExtensionRegistryError(ValueError):
    """Invalid registration data for the VRP extension registry."""

    def __init__(
        self,
        reason: str,
        *,
        family: ExtensionFamily | None = None,
        extension_id: str | None = None,
        component_id: str | None = None,
    ) -> None:
        self.reason = reason
        self.family = family
        self.extension_id = extension_id
        self.component_id = component_id
        super().__init__(reason)


class VrpExtensionConfigurationError(ValueError):
    """A registered extension rejected its configuration during loading."""

    def __init__(
        self,
        *,
        family: ExtensionFamily,
        extension_id: str,
        component_id: str,
        method: ConfigurationMethod,
        input_location: str,
        reason: str,
    ) -> None:
        self.family = family
        self.extension_id = extension_id
        self.component_id = component_id
        self.method = method
        self.input_location = input_location
        self.reason = reason
        super().__init__(
            f'{family} extension {extension_id!r} failed {method} at '
            f'{input_location}: {reason}'
        )


class VrpExtensionEvaluationError(RuntimeError):
    """A registered extension returned an invalid result during evaluation."""

    def __init__(
        self,
        *,
        family: ExtensionFamily,
        extension_id: str,
        component_id: str,
        method: ExtensionMethod,
        objective_id: str | None,
        rule_id: str | None,
        scenario_id: str | None,
        reason: str,
    ) -> None:
        self.family = family
        self.extension_id = extension_id
        self.component_id = component_id
        self.method = method
        self.objective_id = objective_id
        self.rule_id = rule_id
        self.scenario_id = scenario_id
        self.reason = reason
        super().__init__(
            f'{family} extension {extension_id!r} failed {method}: {reason}'
        )


class ArcTraversalEvaluation(NamedTuple):
    """One traversed arc in a scenario-specific route projection."""

    arc_id: str
    entry_time: float
    arrival_time: float
    travel_time: float


class LegScenarioEvaluation(NamedTuple):
    """Scenario-specific projection of one route leg."""

    leg_index: int
    arc_traversals: tuple[ArcTraversalEvaluation, ...]


class WindowScenarioAssessment(NamedTuple):
    """Violation amounts and guidance value for one objective's window."""

    objective_id: str
    selected_window_index: int
    early_violation: float
    late_violation: float
    guidance_value: float


class StopScenarioEvaluation(NamedTuple):
    """Scenario-specific projection of one route stop."""

    stop_index: int
    stop_kind: Literal['activity', 'transfer_operation', 'depot_only']
    node_id: str
    arrival_time: float
    service_start: float | None
    service_finish: float | None
    departure_time: float
    break_start: float | None
    break_finish: float | None
    activity_ref: ActivityRef | None
    transfer_step_ref: TransferStepRef | None
    window_assessments_by_objective: tuple[WindowScenarioAssessment, ...]


class VehicleDayScenarioEvaluation(NamedTuple):
    """Scenario-specific projection of one vehicle's daily route."""

    vehicle_id: str
    day_index: int
    duty_start_time: float
    duty_end_time: float
    stops: tuple[StopScenarioEvaluation, ...]
    legs: tuple[LegScenarioEvaluation, ...]


class VrpScenarioEvaluation(NamedTuple):
    """Read-only trace of one complete plan evaluation in one scenario."""

    scenario_id: str
    probability: float
    vehicle_days: tuple[VehicleDayScenarioEvaluation, ...]


class BaseMeasureRequest(NamedTuple):
    """Arguments supplied to a custom scenario-level objective measure."""

    instance: LoadedVrpInstance
    solution: VrpSolution
    objective_id: str
    scenario: VrpScenarioEvaluation
    parameters: FrozenTomlTable
    application_data: FrozenTomlTable | None


class ScenarioObjectiveInput(NamedTuple):
    """One weighted objective value supplied to a custom aggregation."""

    scenario_id: str
    probability: float
    value: float


class ScenarioAggregationRequest(NamedTuple):
    """Arguments supplied to a custom cross-scenario aggregation."""

    objective_id: str
    scenario_values: tuple[ScenarioObjectiveInput, ...]
    parameters: FrozenTomlTable
    application_data: FrozenTomlTable | None


class PenaltyFunctionRequest(NamedTuple):
    """Arguments supplied to one custom violation penalty function."""

    objective_id: str
    violation_category: ViolationCategory
    rule_id: str | None
    violation_measure: float
    parameters: FrozenTomlTable
    application_data: FrozenTomlTable | None


class PenaltyCategoryInput(NamedTuple):
    """Objective-scaled category values supplied to a custom aggregator."""

    category_id: ViolationCategory
    scale: float
    values: tuple[float, ...]


class PenaltyAggregationRequest(NamedTuple):
    """Arguments supplied to a custom penalty aggregation."""

    objective_id: str
    category_values: tuple[PenaltyCategoryInput, ...]
    parameters: FrozenTomlTable
    application_data: FrozenTomlTable | None


class ExtensionParametersRequest(NamedTuple):
    """Configuration fields validated for one custom objective extension."""

    instance: LoadedVrpInstance
    family: ObjectiveExtensionFamily
    extension_id: str
    objective_id: str
    violation_category: ViolationCategory | None
    rule_id: str | None
    input_location: str
    parameters: FrozenTomlTable
    application_data: FrozenTomlTable | None


class HosConfigurationRequest(NamedTuple):
    """Instance data supplied to a registered HOS configuration validator."""

    personnel_data: VrpPersonnelData
    planning_dates: tuple[date, ...]
    time_unit: str
    input_location: str
    hos_configuration: FrozenTomlTable
    application_data: FrozenTomlTable | None


class HosRuleFinding(NamedTuple):
    """One nonnegative HOS violation measure for one person and rule."""

    rule_id: str
    personnel_id: str
    violation_measure: float


class HosScenarioRequest(NamedTuple):
    """Scenario-specific plan data supplied to a registered HOS regime."""

    instance: LoadedVrpInstance
    solution: VrpSolution
    scenario: VrpScenarioEvaluation
    hos_configuration: FrozenTomlTable
    application_data: FrozenTomlTable | None


class BaseMeasureCallback(Protocol):
    """Callable contract for a scenario-level base measure."""

    def __call__(self, request: BaseMeasureRequest) -> float: ...


class ScenarioAggregationCallback(Protocol):
    """Callable contract for a probability-weighted scenario aggregation."""

    def __call__(self, request: ScenarioAggregationRequest) -> float: ...


class PenaltyFunctionCallback(Protocol):
    """Callable contract mapping one violation measure to a penalty."""

    def __call__(self, request: PenaltyFunctionRequest) -> float: ...


class PenaltyAggregationCallback(Protocol):
    """Callable contract combining objective penalty categories."""

    def __call__(self, request: PenaltyAggregationRequest) -> float: ...


class ValidateExtensionParameters(Protocol):
    """Callable contract for checking custom TOML parameters at load time."""

    def __call__(self, request: ExtensionParametersRequest) -> None: ...


class HosConfigurationValidator(Protocol):
    """Callable contract that normalizes selected HOS rules and personnel."""

    def __call__(
        self, request: HosConfigurationRequest
    ) -> tuple[HosRuleDefinition, ...]: ...


class HosScenarioCallback(Protocol):
    """Callable contract for scenario-specific HOS findings."""

    def __call__(self, request: HosScenarioRequest) -> tuple[HosRuleFinding, ...]: ...


@dataclass(frozen=True, slots=True)
class BaseMeasureRegistration:
    """Bind a custom base-measure ID to its validation and evaluation calls."""

    extension_id: str
    component_id: str
    configuration_id: str
    validate_parameters: ValidateExtensionParameters
    evaluate: BaseMeasureCallback


@dataclass(frozen=True, slots=True)
class ScenarioAggregationRegistration:
    """Bind a custom aggregation ID to its validation and calculation calls."""

    extension_id: str
    component_id: str
    configuration_id: str
    validate_parameters: ValidateExtensionParameters
    aggregate: ScenarioAggregationCallback


@dataclass(frozen=True, slots=True)
class PenaltyFunctionRegistration:
    """Bind a custom penalty ID to its validation and calculation calls."""

    extension_id: str
    component_id: str
    configuration_id: str
    validate_parameters: ValidateExtensionParameters
    evaluate: PenaltyFunctionCallback


@dataclass(frozen=True, slots=True)
class PenaltyAggregationRegistration:
    """Bind a custom penalty aggregation ID to its callbacks."""

    extension_id: str
    component_id: str
    configuration_id: str
    validate_parameters: ValidateExtensionParameters
    aggregate: PenaltyAggregationCallback


@dataclass(frozen=True, slots=True)
class HosRegimeRegistration:
    """Bind a custom HOS ID and version to its validation and evaluation calls."""

    extension_id: str
    version: str
    component_id: str
    configuration_id: str
    validate_configuration: HosConfigurationValidator
    evaluate_scenario: HosScenarioCallback


class _RegistrationContract(Protocol):
    extension_id: str
    component_id: str
    configuration_id: str


@dataclass(frozen=True, slots=True)
class VrpExtensionRegistry:
    """Immutable bindings from extension IDs to application callbacks.

    :param registry_id: Stable identity for this registry implementation.
    :param configuration_id: Stable identity for its registrations and settings.
    :param base_measures: Custom objective base-measure registrations.
    :param scenario_aggregations: Custom scenario aggregation registrations.
    :param penalty_functions: Custom violation penalty registrations.
    :param penalty_aggregations: Custom penalty aggregation registrations.
    :param hos_regimes: Custom hours-of-service rule registrations.
    """

    registry_id: str
    configuration_id: str
    base_measures: tuple[BaseMeasureRegistration, ...] = ()
    scenario_aggregations: tuple[ScenarioAggregationRegistration, ...] = ()
    penalty_functions: tuple[PenaltyFunctionRegistration, ...] = ()
    penalty_aggregations: tuple[PenaltyAggregationRegistration, ...] = ()
    hos_regimes: tuple[HosRegimeRegistration, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.registry_id, 'registry_id')
        _require_identifier(self.configuration_id, 'configuration_id')
        component_ids: set[str] = set()
        self._validate_family(
            'base_measure',
            self.base_measures,
            BaseMeasureRegistration,
            ('validate_parameters', 'evaluate'),
            component_ids,
        )
        self._validate_family(
            'scenario_aggregation',
            self.scenario_aggregations,
            ScenarioAggregationRegistration,
            ('validate_parameters', 'aggregate'),
            component_ids,
        )
        self._validate_family(
            'penalty_function',
            self.penalty_functions,
            PenaltyFunctionRegistration,
            ('validate_parameters', 'evaluate'),
            component_ids,
        )
        self._validate_family(
            'penalty_aggregation',
            self.penalty_aggregations,
            PenaltyAggregationRegistration,
            ('validate_parameters', 'aggregate'),
            component_ids,
        )
        self._validate_family(
            'hos_regime',
            self.hos_regimes,
            HosRegimeRegistration,
            ('validate_configuration', 'evaluate_scenario'),
            component_ids,
        )
        for registration in self.hos_regimes:
            _require_identifier(registration.version, 'version', family='hos_regime')

    @staticmethod
    def _validate_family(
        family: ExtensionFamily,
        registrations: object,
        registration_type: type[object],
        callbacks: tuple[str, ...],
        component_ids: set[str],
    ) -> None:
        if type(registrations) is not tuple:
            raise VrpExtensionRegistryError(
                f'{family} registrations must be a tuple', family=family
            )
        values = cast(tuple[object, ...], registrations)
        extension_ids: set[str] = set()
        for item in values:
            if type(item) is not registration_type:
                raise VrpExtensionRegistryError(
                    f'{family} registrations have the wrong record type',
                    family=family,
                )
            registration = cast(_RegistrationContract, item)
            extension_id = _require_identifier(
                registration.extension_id, 'extension_id', family=family
            )
            component_id = _require_identifier(
                registration.component_id, 'component_id', family=family
            )
            _require_identifier(registration.configuration_id, 'configuration_id')
            if extension_id in extension_ids:
                raise VrpExtensionRegistryError(
                    f'duplicate {family} extension ID {extension_id!r}',
                    family=family,
                    extension_id=extension_id,
                    component_id=component_id,
                )
            extension_ids.add(extension_id)
            if component_id in component_ids:
                raise VrpExtensionRegistryError(
                    f'duplicate component ID {component_id!r}',
                    family=family,
                    extension_id=extension_id,
                    component_id=component_id,
                )
            component_ids.add(component_id)
            for callback_name in callbacks:
                if not callable(getattr(item, callback_name, None)):
                    raise VrpExtensionRegistryError(
                        f'{family} registration callback {callback_name!r} '
                        'is not callable',
                        family=family,
                        extension_id=extension_id,
                        component_id=component_id,
                    )


def _require_identifier(
    value: object,
    name: str,
    *,
    family: ExtensionFamily | None = None,
) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise VrpExtensionRegistryError(
            f'{name} must be a nonempty string without surrounding whitespace',
            family=family,
        )
    return value


def empty_vrp_extension_registry() -> VrpExtensionRegistry:
    """Return the package-owned registry containing no custom callbacks."""
    return VrpExtensionRegistry(
        registry_id='vrp.extensions.empty',
        configuration_id='vrp-extensions-empty-v1',
    )
