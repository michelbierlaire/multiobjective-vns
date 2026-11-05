"""Validation and invocation of scenario-generation extension callbacks.

Created: 2026-11-03T17:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from datetime import date
from math import isfinite
from types import MappingProxyType

from multiobjective_vns.multiday_vrp.scenario_generation.alignment import (
    BlockData,
)
from multiobjective_vns.multiday_vrp.scenario_generation.errors import (
    ScenarioGenerationError,
    ScenarioInputError,
)
from multiobjective_vns.multiday_vrp.scenario_generation.input_data import InputData
from multiobjective_vns.multiday_vrp.scenario_generation.records import (
    AlignedHistoricalData,
    BlockImputationSummary,
    ImputationRequest,
    JointImputer,
    PostHorizonRule,
    ProfileCoordinate,
    ScenarioGenerationOptions,
)

_BUILTIN_RULE_ID = 'constant_last_boundary'
_BUILTIN_RULE_CONFIGURATION_ID = 'builtin-v1'

__all__ = [
    'complete_block_profile',
    'imputer_identity',
    'resolve_post_horizon_rule',
    'validate_options',
    'verify_post_horizon_rule',
]


def validate_options(data: InputData, options: ScenarioGenerationOptions) -> None:
    """Validate option keys and extension registration identities against inputs."""
    profile_keys = {
        (arc_id, day_index, boundary_index)
        for arc_id in data.arc_ids
        for day_index, day in enumerate(data.target_boundaries, start=1)
        for boundary_index in range(len(day))
    }
    selected = {item.isoformat() for item in data.date_selected_starts}
    for distance_key in options.scenario_distance_weights:
        if distance_key not in profile_keys:
            raise ScenarioInputError(
                reason=f'unknown scenario-distance weight key {distance_key!r}'
            )
    for fifo_key in options.fifo_projection_weights:
        try:
            parsed_start = date.fromisoformat(fifo_key[0])
        except ValueError as error:
            raise ScenarioInputError(
                reason=f'invalid FIFO weight date {fifo_key[0]!r}'
            ) from error
        if (
            parsed_start.isoformat() != fifo_key[0]
            or fifo_key[0] not in selected
            or (fifo_key[1], fifo_key[2], fifo_key[3]) not in profile_keys
        ):
            raise ScenarioInputError(
                reason=f'unknown FIFO projection weight key {fifo_key!r}'
            )
    for group in options.protected_traffic_groups:
        unknown_starts = group.block_start_dates - selected
        if unknown_starts:
            raise ScenarioInputError(
                reason=(
                    f'protected group {group.group_id!r} refers to unselected block starts '
                    f'{sorted(unknown_starts)!r}'
                )
            )
    if options.joint_imputer is not None:
        _require_identity(
            options.joint_imputer,
            'extension_id',
            'configuration_id',
            'joint imputer',
        )
        if not callable(getattr(options.joint_imputer, 'fit', None)) or not callable(
            getattr(options.joint_imputer, 'impute_missing', None)
        ):
            raise ScenarioInputError(
                reason='joint imputer must implement fit and impute_missing'
            )
    for rule_key, rule in options.post_horizon_rules.items():
        rule_id, configuration_id = rule_key
        actual_id = getattr(rule, 'rule_id', None)
        actual_configuration = getattr(rule, 'configuration_id', None)
        if (actual_id, actual_configuration) != (rule_id, configuration_id):
            raise ScenarioInputError(
                reason=(
                    f'post-horizon registry key {rule_key!r} does not match rule attributes'
                )
            )
        if not callable(getattr(rule, 'travel_time_at', None)):
            raise ScenarioInputError(
                reason=f'post-horizon rule {rule_key!r} lacks travel_time_at'
            )
        if rule_key == (_BUILTIN_RULE_ID, _BUILTIN_RULE_CONFIGURATION_ID):
            raise ScenarioInputError(
                reason='the built-in post-horizon rule ID is reserved'
            )


def imputer_identity(
    options: ScenarioGenerationOptions,
) -> tuple[str | None, str | None]:
    """Return the registered imputer identity, when one is configured."""
    if options.joint_imputer is None:
        return None, None
    return _require_identity(
        options.joint_imputer,
        'extension_id',
        'configuration_id',
        'joint imputer',
    )


def resolve_post_horizon_rule(
    data: InputData, options: ScenarioGenerationOptions
) -> PostHorizonRule | None:
    """Resolve the manifest rule pair; ``None`` selects the built-in rule."""
    if (
        data.post_horizon_rule_id,
        data.post_horizon_rule_configuration_id,
    ) == (_BUILTIN_RULE_ID, _BUILTIN_RULE_CONFIGURATION_ID):
        return None
    key = data.post_horizon_rule_id, data.post_horizon_rule_configuration_id
    rule = options.post_horizon_rules.get(key)
    if rule is None:
        raise ScenarioInputError(reason=f'no post-horizon rule registered for {key!r}')
    return rule


def fit_imputer(
    imputer: JointImputer,
    aligned: AlignedHistoricalData,
    random_source: random.Random,
) -> None:
    """Fit the joint imputer once and normalize callback failures."""
    extension_id, configuration_id = _require_identity(
        imputer, 'extension_id', 'configuration_id', 'joint imputer'
    )
    try:
        fit_method = getattr(imputer, 'fit')
        result = fit_method(aligned, random_source)
    except Exception as error:
        raise ScenarioGenerationError(
            reason=f'imputer fit failed: {error}',
            extension_id=extension_id,
            configuration_id=configuration_id,
            method='fit',
        ) from error
    if result is not None:
        raise ScenarioGenerationError(
            reason='imputer fit must return None',
            extension_id=extension_id,
            configuration_id=configuration_id,
            method='fit',
        )


def complete_block_profile(
    block: BlockData,
    options: ScenarioGenerationOptions,
    random_source: random.Random,
    summaries: list[BlockImputationSummary],
) -> dict[ProfileCoordinate, float]:
    """Impute every missing coordinate in a candidate block when configured."""
    values = dict(block.values)
    missing = {key for key, value in values.items() if value is None}
    if missing:
        imputer = options.joint_imputer
        assert imputer is not None
        extension_id, configuration_id = _require_identity(
            imputer, 'extension_id', 'configuration_id', 'joint imputer'
        )
        request = ImputationRequest(
            block.start_date.isoformat(),
            MappingProxyType(values),
            block.source,
        )
        try:
            imputed = imputer.impute_missing(request, random_source)
        except Exception as error:
            raise ScenarioGenerationError(
                reason=f'imputer failed for block {block.start_date.isoformat()}: {error}',
                extension_id=extension_id,
                configuration_id=configuration_id,
                method='impute_missing',
            ) from error
        if not isinstance(imputed, Mapping) or set(imputed) != missing:
            raise ScenarioGenerationError(
                reason=(
                    f'imputer returned invalid keys for block {block.start_date.isoformat()}; '
                    f'expected exactly {len(missing)} missing coordinates'
                ),
                extension_id=extension_id,
                configuration_id=configuration_id,
                method='impute_missing',
            )
        if any(
            type(key) is not tuple
            or len(key) != 3
            or type(key[0]) is not str
            or type(key[1]) is not int
            or type(key[2]) is not int
            for key in imputed
        ):
            raise ScenarioGenerationError(
                reason=(
                    'imputer returned malformed coordinate keys for block '
                    f'{block.start_date.isoformat()}'
                ),
                extension_id=extension_id,
                configuration_id=configuration_id,
                method='impute_missing',
            )
        for key in missing:
            value = imputed[key]
            if type(value) not in (int, float):
                raise ScenarioGenerationError(
                    reason=f'imputer returned a malformed value at {key!r}',
                    extension_id=extension_id,
                    configuration_id=configuration_id,
                    method='impute_missing',
                )
            try:
                converted = float(value)
            except (OverflowError, ValueError) as error:
                raise ScenarioGenerationError(
                    reason=f'imputer value cannot convert to float at {key!r}',
                    extension_id=extension_id,
                    configuration_id=configuration_id,
                    method='impute_missing',
                ) from error
            if not isfinite(converted) or converted < 0.0:
                raise ScenarioGenerationError(
                    reason=f'imputer returned a non-finite or negative value at {key!r}',
                    extension_id=extension_id,
                    configuration_id=configuration_id,
                    method='impute_missing',
                )
            values[key] = converted
        summaries.append(
            BlockImputationSummary(
                block.start_date.isoformat(), len(missing), len(missing)
            )
        )
    return {key: float(value) for key, value in values.items() if value is not None}


def verify_post_horizon_rule(
    data: InputData,
    profile: Mapping[ProfileCoordinate, float],
    rule: PostHorizonRule | None,
) -> None:
    """Check custom extension output at the horizon boundary for every arc."""
    if rule is None:
        return
    rule_id, configuration_id = _require_identity(
        rule, 'rule_id', 'configuration_id', 'post-horizon rule'
    )
    final_boundary = data.target_boundaries[-1][-1]
    final_profile = MappingProxyType(
        {
            arc_id: profile[
                (
                    arc_id,
                    len(data.target_boundaries),
                    len(data.target_boundaries[-1]) - 1,
                )
            ]
            for arc_id in data.arc_ids
        }
    )
    for arc_id in data.arc_ids:
        try:
            raw = rule.travel_time_at(
                arc_id,
                final_boundary,
                final_boundary,
                final_profile,
                data.time_unit,
            )
        except Exception as error:
            raise ScenarioGenerationError(
                reason=(
                    f'post-horizon rule failed at final boundary for arc {arc_id!r}: '
                    f'{error}'
                ),
                extension_id=rule_id,
                configuration_id=configuration_id,
                method='travel_time_at',
            ) from error
        if type(raw) not in (int, float):
            raise ScenarioGenerationError(
                reason=f'post-horizon rule returned an invalid value for arc {arc_id!r}',
                extension_id=rule_id,
                configuration_id=configuration_id,
                method='travel_time_at',
            )
        try:
            value = float(raw)
        except (OverflowError, ValueError) as error:
            raise ScenarioGenerationError(
                reason=(
                    f'post-horizon rule returned an unrepresentable value for arc {arc_id!r}'
                ),
                extension_id=rule_id,
                configuration_id=configuration_id,
                method='travel_time_at',
            ) from error
        expected = final_profile[arc_id]
        tolerance = 1e-9 + 1e-12 * max(abs(value), abs(expected))
        if not isfinite(value) or value < 0.0 or abs(value - expected) > tolerance:
            raise ScenarioGenerationError(
                reason=f'post-horizon rule does not match final value for arc {arc_id!r}',
                extension_id=rule_id,
                configuration_id=configuration_id,
                method='travel_time_at',
            )


def _require_identity(
    extension: object, first_name: str, second_name: str, label: str
) -> tuple[str, str]:
    first = getattr(extension, first_name, None)
    second = getattr(extension, second_name, None)
    if (
        type(first) is not str
        or not first.strip()
        or first.strip() != first
        or type(second) is not str
        or not second.strip()
        or second.strip() != second
    ):
        raise ScenarioInputError(
            reason=f'{label} needs nonempty stable identity attributes'
        )
    return first, second
