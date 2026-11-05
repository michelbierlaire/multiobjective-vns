"""Objective and application-rule declaration loading.

Created: 2026-11-03T16:34:50+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar, cast

from multiobjective_vns.multiday_vrp.extensions import (
    BaseMeasureRegistration,
    ExtensionParametersRequest,
    HosConfigurationRequest,
    ObjectiveExtensionFamily,
    PenaltyAggregationRegistration,
    PenaltyFunctionRegistration,
    ScenarioAggregationRegistration,
    VrpExtensionConfigurationError,
    VrpExtensionRegistry,
)
from multiobjective_vns.multiday_vrp.input_errors import VrpInputError
from multiobjective_vns.multiday_vrp.input_records import (
    AggregationSpec,
    FrozenTomlTable,
    HosRuleDefinition,
    LoadedVrpInstance,
    ObjectiveSpec,
    PenaltyFunctionSpec,
    VrpEventData,
    VrpInstanceSettings,
    VrpInterventionData,
    VrpNetworkData,
    VrpObjectiveData,
    VrpPackageData,
    VrpPersonnelData,
)
from multiobjective_vns.multiday_vrp.input_schema import (
    BUILTIN_AGGREGATIONS,
    BUILTIN_BASE_MEASURES,
    BUILTIN_PENALTY_AGGREGATIONS,
    PENALTY_CATEGORIES,
)
from multiobjective_vns.multiday_vrp.input_utils import (
    read_toml_table,
)
from multiobjective_vns.multiday_vrp.input_validation import (
    _as_table,
    _exact_dict_keys,
    _foreign,
    _id_value,
    _toml_float,
    _toml_integer,
    _toml_string,
    _unique,
)

_Registration = TypeVar('_Registration')


def _parse_objectives(
    path: Path,
) -> tuple[VrpObjectiveData, dict[str, tuple[str, str, str]]]:
    document = read_toml_table(path)
    allowed_keys = {'format_version', 'objectives', 'penalty_functions'}
    extra = document.keys() - allowed_keys
    missing = allowed_keys - document.keys()
    if extra:
        raise VrpInputError(file=path.name, column=min(extra), reason='unknown key')
    if missing:
        raise VrpInputError(
            file=path.name, column=min(missing), reason='required key is missing'
        )
    if _toml_integer(document, 'format_version', path.name) != 1:
        raise VrpInputError(
            file=path.name,
            column='format_version',
            reason='supported format_version is 1',
        )
    raw_objectives = document['objectives']
    raw_penalties = document['penalty_functions']
    if type(raw_objectives) is not list or not raw_objectives:
        raise VrpInputError(
            file=path.name,
            column='objectives',
            reason='must be a nonempty array of tables',
        )
    if type(raw_penalties) is not list:
        raise VrpInputError(
            file=path.name,
            column='penalty_functions',
            reason='must be an array of tables',
        )
    objectives: list[ObjectiveSpec] = []
    locations: dict[str, tuple[str, str, str]] = {}
    for index, raw in enumerate(cast(list[object], raw_objectives)):
        location_prefix = f'objectives.toml.objectives[{index}]'
        table = _as_table(raw, path.name, location_prefix)
        expected = {
            'objective_id',
            'base_measure_id',
            'base_measure_parameters',
            'aggregation',
            'penalty_aggregation',
            'penalty_scales',
        }
        _exact_dict_keys(table, expected, path.name, location_prefix)
        objective_id = _toml_string(table, 'objective_id', path.name, location_prefix)
        _id_value(objective_id, path.name, index + 1, 'objective_id')
        base_measure_id = _toml_string(
            table, 'base_measure_id', path.name, location_prefix
        )
        _id_value(base_measure_id, path.name, index + 1, 'base_measure_id')
        base_parameters = _as_table(
            table['base_measure_parameters'],
            path.name,
            f'{location_prefix}.base_measure_parameters',
        )
        aggregation, aggregation_path = _parse_aggregation(
            table['aggregation'],
            path.name,
            f'{location_prefix}.aggregation',
            allow_q=True,
        )
        penalty_aggregation, penalty_aggregation_path = _parse_aggregation(
            table['penalty_aggregation'],
            path.name,
            f'{location_prefix}.penalty_aggregation',
            allow_q=False,
        )
        if base_measure_id in BUILTIN_BASE_MEASURES and base_parameters:
            raise VrpInputError(
                file=path.name,
                column=f'{location_prefix}.base_measure_parameters',
                reason='built-in base measures take an empty parameter table',
            )
        if aggregation.id in BUILTIN_AGGREGATIONS and aggregation.parameters:
            raise VrpInputError(
                file=path.name,
                column=f'{location_prefix}.aggregation.parameters',
                reason='built-in scenario aggregations take no parameters beyond q',
            )
        if (
            penalty_aggregation.id in BUILTIN_PENALTY_AGGREGATIONS
            and penalty_aggregation.parameters
        ):
            raise VrpInputError(
                file=path.name,
                column=f'{location_prefix}.penalty_aggregation.parameters',
                reason='built-in penalty aggregations take no parameters',
            )
        scales_value = table['penalty_scales']
        scales_table = _as_table(
            scales_value, path.name, f'{location_prefix}.penalty_scales'
        )
        scales: dict[str, float] = {}
        for category, raw_scale in scales_table.items():
            if category not in PENALTY_CATEGORIES:
                raise VrpInputError(
                    file=path.name,
                    column=f'{location_prefix}.penalty_scales.{category}',
                    reason='unknown violation category',
                )
            scales[category] = _toml_float(
                raw_scale,
                path.name,
                f'{location_prefix}.penalty_scales.{category}',
                minimum=0.0,
                strict_minimum=True,
            )
        objectives.append(
            ObjectiveSpec(
                objective_id,
                base_measure_id,
                FrozenTomlTable(base_parameters),
                aggregation,
                penalty_aggregation,
                MappingProxyType(scales),
            )
        )
        locations[objective_id] = (
            f'{location_prefix}.base_measure_parameters',
            aggregation_path,
            penalty_aggregation_path,
        )
    _unique((item.objective_id for item in objectives), path, 'objective_id')

    penalties: list[PenaltyFunctionSpec] = []
    penalty_keys: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(cast(list[object], raw_penalties)):
        location = f'objectives.toml.penalty_functions[{index}]'
        table = _as_table(raw, path.name, location)
        _exact_dict_keys(
            table,
            {
                'objective_id',
                'violation_category',
                'rule_id',
                'function_id',
                'parameters',
            },
            path.name,
            location,
        )
        objective_id = _toml_string(table, 'objective_id', path.name, location)
        category = _toml_string(table, 'violation_category', path.name, location)
        rule_id = _toml_string(table, 'rule_id', path.name, location)
        function_id = _toml_string(table, 'function_id', path.name, location)
        _id_value(function_id, path.name, index + 1, f'{location}.function_id')
        if rule_id:
            _id_value(rule_id, path.name, index + 1, f'{location}.rule_id')
        if category not in PENALTY_CATEGORIES:
            raise VrpInputError(
                file=path.name,
                column=f'{location}.violation_category',
                reason='unknown violation category',
            )
        if rule_id and category not in {'simple_rule', 'hos_rule'}:
            raise VrpInputError(
                file=path.name,
                column=f'{location}.rule_id',
                reason='rule-specific binding applies only to simple_rule or hos_rule',
            )
        parameters = FrozenTomlTable(
            _as_table(table['parameters'], path.name, f'{location}.parameters')
        )
        key = objective_id, category, rule_id
        if key in penalty_keys:
            raise VrpInputError(
                file=path.name,
                column=f'{location}.rule_id',
                reason='duplicate penalty-function binding',
            )
        penalty_keys.add(key)
        penalties.append(
            PenaltyFunctionSpec(
                objective_id, category, rule_id, function_id, parameters
            )
        )
    _foreign(
        (item.objective_id for item in penalties),
        {item.objective_id for item in objectives},
        path,
        'objective_id',
    )
    return VrpObjectiveData(tuple(objectives), tuple(penalties), ()), locations


def _parse_aggregation(
    raw: object,
    filename: str,
    location: str,
    *,
    allow_q: bool,
) -> tuple[AggregationSpec, str]:
    table = _as_table(raw, filename, location)
    allowed = {'id', 'parameters'} | ({'q'} if allow_q else set())
    missing = {'id'} - table.keys()
    extra = table.keys() - allowed
    if missing or extra:
        key = min(missing) if missing else min(extra)
        reason = 'required key is missing' if missing else 'unknown key'
        raise VrpInputError(file=filename, column=f'{location}.{key}', reason=reason)
    extension_id = _toml_string(table, 'id', filename, location)
    _id_value(extension_id, filename, 0, f'{location}.id')
    q_value: float | None = None
    if 'q' in table:
        q_value = _toml_float(
            table['q'], filename, f'{location}.q', minimum=0.0, strict_minimum=True
        )
        if q_value > 1.0:
            raise VrpInputError(
                file=filename, column=f'{location}.q', reason='must be at most 1'
            )
    if extension_id == 'weighted_quantile' and q_value is None:
        raise VrpInputError(
            file=filename,
            column=f'{location}.q',
            reason='required for weighted_quantile',
        )
    if extension_id != 'weighted_quantile' and q_value is not None:
        raise VrpInputError(
            file=filename,
            column=f'{location}.q',
            reason='only applies to weighted_quantile',
        )
    if 'parameters' in table:
        parameters = FrozenTomlTable(
            _as_table(table['parameters'], filename, f'{location}.parameters')
        )
    else:
        parameters = FrozenTomlTable({})
    return AggregationSpec(extension_id, q_value, parameters), f'{location}.parameters'


def _validate_objectives(
    data: VrpObjectiveData,
    active_categories: frozenset[str],
    events: VrpEventData,
    registry: VrpExtensionRegistry,
    filename: str,
) -> None:
    for objective in data.objectives:
        objective_id = objective.objective_id
        if (
            objective.base_measure_id not in BUILTIN_BASE_MEASURES
            and not _find_registration(
                registry.base_measures, objective.base_measure_id
            )
        ):
            raise VrpInputError(
                file=filename,
                column='base_measure_id',
                reason=f'unknown base measure {objective.base_measure_id!r}',
            )
        if (
            objective.aggregation.id not in BUILTIN_AGGREGATIONS
            and not _find_registration(
                registry.scenario_aggregations, objective.aggregation.id
            )
        ):
            raise VrpInputError(
                file=filename,
                column='aggregation.id',
                reason=f'unknown scenario aggregation {objective.aggregation.id!r}',
            )
        if (
            objective.penalty_aggregation.id not in BUILTIN_PENALTY_AGGREGATIONS
            and not _find_registration(
                registry.penalty_aggregations, objective.penalty_aggregation.id
            )
        ):
            raise VrpInputError(
                file=filename,
                column='penalty_aggregation.id',
                reason=f'unknown penalty aggregation {objective.penalty_aggregation.id!r}',
            )
        missing_scales = active_categories - objective.penalty_scales.keys()
        if missing_scales:
            raise VrpInputError(
                file=filename,
                column='penalty_scales',
                reason=f'missing active category scale {min(missing_scales)!r}',
            )
        if objective.base_measure_id in {
            'event_count',
            'maximum_event_exposure',
        } and not any(
            item.objective_id == objective_id for item in events.objective_event_types
        ):
            raise VrpInputError(
                file=filename,
                column='objective_event_types',
                reason=f'objective {objective_id!r} needs at least one event type',
            )
        if objective.base_measure_id not in {
            'event_count',
            'maximum_event_exposure',
        } and any(
            item.objective_id == objective_id for item in events.objective_event_types
        ):
            raise VrpInputError(
                file=filename,
                column='objective_event_types',
                reason=f'objective {objective_id!r} does not use event types',
            )
    category_level = {
        (item.objective_id, item.violation_category)
        for item in data.penalty_functions
        if item.rule_id == ''
    }
    for objective in data.objectives:
        for category in active_categories:
            if (objective.objective_id, category) not in category_level:
                raise VrpInputError(
                    file=filename,
                    column='penalty_functions',
                    reason=f'objective {objective.objective_id!r} requires a category-level {category!r} binding',
                )
    for function in data.penalty_functions:
        if function.function_id != 'linear' and not _find_registration(
            registry.penalty_functions, function.function_id
        ):
            raise VrpInputError(
                file=filename,
                column='function_id',
                reason=f'unknown penalty function {function.function_id!r}',
            )
        if function.function_id == 'linear':
            rate = function.parameters.get('rate')
            if type(rate) not in {int, float} or type(rate) is bool:
                raise VrpInputError(
                    file=filename,
                    column='parameters.rate',
                    reason='linear penalty requires a finite positive rate',
                )
            rate_value = float(cast(int | float, rate))
            if not math.isfinite(rate_value) or rate_value <= 0:
                raise VrpInputError(
                    file=filename,
                    column='parameters.rate',
                    reason='linear penalty requires a finite positive rate',
                )
            if set(function.parameters) != {'rate'}:
                raise VrpInputError(
                    file=filename,
                    column='parameters',
                    reason='linear penalty accepts only rate',
                )


def _active_penalty_categories(
    settings: VrpInstanceSettings,
    packages: VrpPackageData,
    interventions: VrpInterventionData,
    network: VrpNetworkData,
    personnel: VrpPersonnelData,
) -> frozenset[str]:
    categories: set[str] = set()
    if packages.packages or interventions.interventions:
        categories.update({'early_window', 'late_window'})
    if network.depots and personnel.personnel:
        categories.add('duty_overlap')
    if network.depots:
        categories.add('vehicle_availability')
    if personnel.personnel:
        categories.add('personnel_availability')
    if settings.rule_treatment == 'penalty' and settings.active_simple_rules:
        categories.add('simple_rule')
    if settings.rule_treatment == 'penalty' and settings.hos_regime_id != 'none':
        categories.add('hos_rule')
    return frozenset(categories)


def _check_hos_selection(
    settings: VrpInstanceSettings,
    configuration: FrozenTomlTable | None,
    registry: VrpExtensionRegistry,
    manifest_name: str,
) -> None:
    if settings.hos_regime_id == 'none':
        if configuration is not None:
            raise VrpInputError(
                file=manifest_name,
                column='hos_configuration_file',
                reason='requires an active HOS regime',
            )
        return
    if configuration is None:
        raise VrpInputError(
            file=manifest_name,
            column='hos_configuration_file',
            reason='required for an active HOS regime',
        )
    registration = _find_registration(registry.hos_regimes, settings.hos_regime_id)
    if registration is None:
        raise VrpInputError(
            file=manifest_name,
            column='hos_regime_id',
            reason=f'unknown HOS regime {settings.hos_regime_id!r}',
        )
    if registration.version != settings.hos_regime_version:
        raise VrpInputError(
            file=manifest_name,
            column='hos_regime_version',
            reason=f'registered version is {registration.version!r}',
        )


def _validate_hos_configuration(
    instance: LoadedVrpInstance,
    registry: VrpExtensionRegistry,
    raw_configuration_path: object,
) -> LoadedVrpInstance:
    regime_id = instance.settings.hos_regime_id
    if regime_id == 'none':
        return instance
    registration = _find_registration(registry.hos_regimes, regime_id)
    if registration is None or instance.hos_configuration is None:
        raise AssertionError('HOS selection must be checked before its configuration')
    try:
        definitions = registration.validate_configuration(
            HosConfigurationRequest(
                personnel_data=instance.personnel_data,
                planning_dates=instance.scenario_bank.planning_dates,
                time_unit=instance.scenario_bank.time_unit,
                input_location=f'instance.toml.{raw_configuration_path}',
                hos_configuration=instance.hos_configuration,
                application_data=instance.application_data,
            )
        )
    except Exception as error:
        raise VrpExtensionConfigurationError(
            family='hos_regime',
            extension_id=registration.extension_id,
            component_id=registration.component_id,
            method='validate_configuration',
            input_location=f'instance.toml.{raw_configuration_path}',
            reason=str(error),
        ) from error
    if type(definitions) is not tuple or not definitions:
        raise VrpExtensionConfigurationError(
            family='hos_regime',
            extension_id=registration.extension_id,
            component_id=registration.component_id,
            method='validate_configuration',
            input_location=f'instance.toml.{raw_configuration_path}',
            reason='validator must return a nonempty tuple of HosRuleDefinition records',
        )
    seen_rules: set[str] = set()
    personnel_ids = {item.person_id for item in instance.personnel_data.personnel}
    for definition in definitions:
        if type(definition) is not HosRuleDefinition:
            raise VrpExtensionConfigurationError(
                family='hos_regime',
                extension_id=registration.extension_id,
                component_id=registration.component_id,
                method='validate_configuration',
                input_location=f'instance.toml.{raw_configuration_path}',
                reason='validator returned an invalid rule definition record',
            )
        if (
            type(definition.rule_id) is not str
            or not definition.rule_id
            or definition.rule_id.strip() != definition.rule_id
            or definition.rule_id in seen_rules
        ):
            raise VrpExtensionConfigurationError(
                family='hos_regime',
                extension_id=registration.extension_id,
                component_id=registration.component_id,
                method='validate_configuration',
                input_location=f'instance.toml.{raw_configuration_path}',
                reason='rule IDs must be unique nonempty strings',
            )
        if (
            type(definition.personnel_ids) is not tuple
            or not definition.personnel_ids
            or any(type(person_id) is not str for person_id in definition.personnel_ids)
            or len(set(definition.personnel_ids)) != len(definition.personnel_ids)
        ):
            raise VrpExtensionConfigurationError(
                family='hos_regime',
                extension_id=registration.extension_id,
                component_id=registration.component_id,
                method='validate_configuration',
                input_location=f'instance.toml.{raw_configuration_path}',
                reason='each rule needs a nonempty duplicate-free personnel list',
            )
        if not set(definition.personnel_ids) <= personnel_ids:
            raise VrpExtensionConfigurationError(
                family='hos_regime',
                extension_id=registration.extension_id,
                component_id=registration.component_id,
                method='validate_configuration',
                input_location=f'instance.toml.{raw_configuration_path}',
                reason='rule definition references unknown personnel',
            )
        seen_rules.add(definition.rule_id)
    return instance._replace(hos_rule_definitions=definitions)


def _validate_objective_rule_bindings(
    instance: LoadedVrpInstance, filename: str
) -> None:
    simple_rules = instance.settings.active_simple_rules
    hos_pairs = {
        (definition.rule_id, person_id)
        for definition in instance.hos_rule_definitions
        for person_id in definition.personnel_ids
    }
    hos_ids = {rule_id for rule_id, _ in hos_pairs}
    for binding in instance.objective_data.penalty_functions:
        if binding.rule_id:
            if (
                binding.violation_category == 'simple_rule'
                and binding.rule_id not in simple_rules
            ):
                raise VrpInputError(
                    file=filename,
                    column='rule_id',
                    reason=f'inactive simple rule {binding.rule_id!r}',
                )
            if (
                binding.violation_category == 'hos_rule'
                and binding.rule_id not in hos_ids
            ):
                raise VrpInputError(
                    file=filename,
                    column='rule_id',
                    reason=f'unknown HOS rule {binding.rule_id!r}',
                )


def _validate_custom_parameters(
    instance: LoadedVrpInstance,
    registry: VrpExtensionRegistry,
    locations: dict[str, tuple[str, str, str]],
) -> None:
    for objective in instance.objective_data.objectives:
        base_location, aggregation_location, penalty_aggregation_location = locations[
            objective.objective_id
        ]
        custom_entries: tuple[
            tuple[ObjectiveExtensionFamily, str, FrozenTomlTable, str, str | None], ...
        ] = (
            (
                'base_measure',
                objective.base_measure_id,
                objective.base_measure_parameters,
                base_location,
                None,
            ),
            (
                'scenario_aggregation',
                objective.aggregation.id,
                objective.aggregation.parameters,
                aggregation_location,
                None,
            ),
            (
                'penalty_aggregation',
                objective.penalty_aggregation.id,
                objective.penalty_aggregation.parameters,
                penalty_aggregation_location,
                None,
            ),
        )
        for (
            family,
            extension_id,
            parameters,
            input_location,
            category,
        ) in custom_entries:
            if _is_builtin(family, extension_id):
                continue
            registration = _registration_for(registry, family, extension_id)
            if registration is None:
                raise VrpInputError(
                    file='objectives.toml',
                    column=input_location,
                    reason=f'unknown {family} extension {extension_id!r}',
                )
            request = ExtensionParametersRequest(
                instance=instance,
                family=family,
                extension_id=extension_id,
                objective_id=objective.objective_id,
                violation_category=cast(Any, category),
                rule_id=None,
                input_location=input_location,
                parameters=parameters,
                application_data=instance.application_data,
            )
            _run_parameter_validator(registration, request)
    for index, binding in enumerate(instance.objective_data.penalty_functions):
        if binding.function_id == 'linear':
            continue
        registration = _find_registration(
            registry.penalty_functions, binding.function_id
        )
        if registration is None:
            raise VrpInputError(
                file='objectives.toml',
                column=f'penalty_functions[{index}].function_id',
                reason=f'unknown penalty function {binding.function_id!r}',
            )
        category = cast(Any, binding.violation_category)
        request = ExtensionParametersRequest(
            instance=instance,
            family='penalty_function',
            extension_id=binding.function_id,
            objective_id=binding.objective_id,
            violation_category=category,
            rule_id=binding.rule_id or None,
            input_location=f'objectives.toml.penalty_functions[{index}].parameters',
            parameters=binding.parameters,
            application_data=instance.application_data,
        )
        _run_parameter_validator(registration, request)


def _run_parameter_validator(
    registration: (
        BaseMeasureRegistration
        | ScenarioAggregationRegistration
        | PenaltyFunctionRegistration
        | PenaltyAggregationRegistration
    ),
    request: ExtensionParametersRequest,
) -> None:
    callback = cast(
        Callable[[ExtensionParametersRequest], object],
        registration.validate_parameters,
    )
    try:
        result = callback(request)
    except Exception as error:
        raise VrpExtensionConfigurationError(
            family=request.family,
            extension_id=registration.extension_id,
            component_id=registration.component_id,
            method='validate_parameters',
            input_location=request.input_location,
            reason=str(error),
        ) from error
    if result is not None:
        raise VrpExtensionConfigurationError(
            family=request.family,
            extension_id=registration.extension_id,
            component_id=registration.component_id,
            method='validate_parameters',
            input_location=request.input_location,
            reason='validator must return None',
        )


def _registration_for(
    registry: VrpExtensionRegistry,
    family: ObjectiveExtensionFamily,
    extension_id: str,
) -> (
    BaseMeasureRegistration
    | ScenarioAggregationRegistration
    | PenaltyFunctionRegistration
    | PenaltyAggregationRegistration
    | None
):
    if family == 'base_measure':
        return _find_registration(registry.base_measures, extension_id)
    if family == 'scenario_aggregation':
        return _find_registration(registry.scenario_aggregations, extension_id)
    if family == 'penalty_function':
        return _find_registration(registry.penalty_functions, extension_id)
    return _find_registration(registry.penalty_aggregations, extension_id)


def _is_builtin(family: ObjectiveExtensionFamily, extension_id: str) -> bool:
    if family == 'base_measure':
        return extension_id in BUILTIN_BASE_MEASURES
    if family == 'scenario_aggregation':
        return extension_id in BUILTIN_AGGREGATIONS
    if family == 'penalty_aggregation':
        return extension_id in BUILTIN_PENALTY_AGGREGATIONS
    return family == 'penalty_function' and extension_id == 'linear'


def _find_registration(
    registrations: tuple[_Registration, ...], extension_id: str
) -> _Registration | None:
    return next(
        (
            item
            for item in registrations
            if getattr(item, 'extension_id', None) == extension_id
        ),
        None,
    )
