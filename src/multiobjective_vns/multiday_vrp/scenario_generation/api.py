"""Coordinate file-based scenario generation and its reporting contracts.

Created: 2026-11-03T17:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from datetime import date, timedelta
from math import isfinite
from pathlib import Path
from types import MappingProxyType

from multiobjective_vns.multiday_vrp.input_errors import VrpInputError
from multiobjective_vns.multiday_vrp.scenario_generation.alignment import (
    BlockData,
    align_history,
    extract_block,
)
from multiobjective_vns.multiday_vrp.scenario_generation.errors import (
    ScenarioGenerationError,
    ScenarioInputError,
    ScenarioReductionError,
)
from multiobjective_vns.multiday_vrp.scenario_generation.extensions import (
    complete_block_profile,
    fit_imputer,
    imputer_identity,
    resolve_post_horizon_rule,
    validate_options,
    verify_post_horizon_rule,
)
from multiobjective_vns.multiday_vrp.scenario_generation.fifo import project_fifo
from multiobjective_vns.multiday_vrp.scenario_generation.input_data import (
    InputData,
    read_generation_input,
)
from multiobjective_vns.multiday_vrp.scenario_generation.output import (
    publish_scenario_bank,
    validate_output_destination,
)
from multiobjective_vns.multiday_vrp.scenario_generation.records import (
    BlockImputationSummary,
    ExcludedBlock,
    FifoAdjustment,
    FifoAdjustmentSummary,
    GenerationReport,
    GenerationResult,
    ImputationSummary,
    ProfileCoordinate,
    ProtectedTrafficGroup,
    ScenarioGenerationOptions,
)
from multiobjective_vns.multiday_vrp.scenario_generation.reduction import (
    coalesce_profiles,
    reduce_profiles,
)

__all__ = ['generate_scenario_bank']


def generate_scenario_bank(
    input_manifest_path: Path,
    output_directory: Path,
    *,
    options: ScenarioGenerationOptions | None = None,
) -> GenerationResult:
    """Generate and publish a standalone, probability-weighted ScenarioBank.

    :param input_manifest_path: Path to the scenario-generation TOML manifest.
    :param output_directory: Absent or empty directory for the completed bank.
    :param options: Optional imputer, rule registry, weights, and protected groups.
    :returns: Published directory and complete immutable generation report.
    :raises ScenarioInputError: If an input, option, registration, or destination is invalid.
    :raises ScenarioGenerationError: If generation, an extension, validation, or publication fails.
    :raises ScenarioReductionError: If protected groups cannot fit within the requested count.
    """
    input_path = Path(input_manifest_path)
    destination = Path(output_directory)
    validate_output_destination(destination)
    effective_options = options if options is not None else ScenarioGenerationOptions()
    if not isinstance(effective_options, ScenarioGenerationOptions):
        raise ScenarioInputError(reason='options must be ScenarioGenerationOptions')
    protected_groups = tuple(effective_options.protected_traffic_groups)
    try:
        data = read_generation_input(input_path)
    except VrpInputError as error:
        raise ScenarioInputError(
            file=error.file,
            row=error.row,
            column=error.column,
            reason=error.reason,
        ) from error
    validate_options(data, effective_options)
    effective_imputer_identity = imputer_identity(effective_options)
    post_rule = resolve_post_horizon_rule(data, effective_options)
    alignment = align_history(data)
    selected_ids = tuple(day.isoformat() for day in data.date_selected_starts)
    mutable_group_map: dict[str, set[str]] = {start: set() for start in selected_ids}
    for group in protected_groups:
        for start in group.block_start_dates:
            mutable_group_map[start].add(group.group_id)
    group_by_start = {
        start: frozenset(group_ids) for start, group_ids in mutable_group_map.items()
    }
    eligible_blocks: list[BlockData] = []
    excluded_blocks: list[ExcludedBlock] = []
    for start_date in data.date_selected_starts:
        missing_dates = _missing_block_dates(data, start_date)
        if missing_dates:
            excluded_blocks.append(
                ExcludedBlock(
                    block_start_date=start_date.isoformat(),
                    reason='incomplete_date_span',
                    missing_dates=missing_dates,
                    missing_coordinates=(),
                )
            )
            continue
        block = extract_block(data, alignment, start_date)
        if (
            block.missing_history_coordinates
            and effective_options.joint_imputer is None
        ):
            excluded_blocks.append(
                ExcludedBlock(
                    block_start_date=start_date.isoformat(),
                    reason='unresolved_missing_values',
                    missing_dates=(),
                    missing_coordinates=block.missing_history_coordinates,
                )
            )
            continue
        eligible_blocks.append(block)
    if not eligible_blocks:
        raise ScenarioGenerationError(
            reason=(
                'no eligible historical blocks remain after date and coverage checks; '
                f'{len(excluded_blocks)} blocks were excluded'
            ),
            operation='select_candidate_blocks',
        )
    eligible_start_dates = tuple(
        block.start_date.isoformat() for block in eligible_blocks
    )
    _validate_protected_group_coverage(
        protected_groups,
        eligible_start_dates,
    )
    block_probabilities = _block_probabilities(data, eligible_blocks)
    unresolved = tuple(
        block for block in eligible_blocks if block.missing_history_coordinates
    )
    imputation_summaries: list[BlockImputationSummary] = []
    random_source = random.Random(data.random_seed)
    if unresolved:
        imputer = effective_options.joint_imputer
        assert imputer is not None
        fit_imputer(imputer, alignment.aligned, random_source)
    block_profiles: list[
        tuple[str, Mapping[ProfileCoordinate, float], float, frozenset[str]]
    ] = []
    fifo_effective: dict[tuple[str, str, int, int], float] = {}
    fifo_adjustments: list[FifoAdjustment] = []
    for block in eligible_blocks:
        profile = complete_block_profile(
            block,
            effective_options,
            random_source,
            imputation_summaries,
        )
        projected, used_weights, adjustments = project_fifo(
            data,
            block.start_date.isoformat(),
            profile,
            effective_options.fifo_projection_weights,
        )
        fifo_effective.update(used_weights)
        fifo_adjustments.extend(adjustments)
        verify_post_horizon_rule(data, projected, post_rule)
        block_profiles.append(
            (
                block.start_date.isoformat(),
                MappingProxyType(projected),
                block_probabilities[block.start_date],
                group_by_start[block.start_date.isoformat()],
            )
        )
    coordinate_order = tuple(
        (arc_id, day_index, boundary_index)
        for arc_id in data.arc_ids
        for day_index, day in enumerate(data.target_boundaries, start=1)
        for boundary_index in range(len(day))
    )
    candidates = coalesce_profiles(block_profiles, coordinate_order)
    reduction = reduce_profiles(
        candidates,
        data.scenario_count,
        effective_options.scenario_distance_weights,
        protected_groups,
    )
    retained_profiles = {
        reduction.probabilities[position].scenario_id: reduction.candidates[
            index
        ].profile
        for position, index in enumerate(reduction.retained_indices)
    }
    unused_fifo_keys = _unused_fifo_keys(
        data,
        effective_options,
        {block.start_date for block in eligible_blocks},
    )
    report = GenerationReport(
        report_version=1,
        time_zone=data.time_zone_name,
        time_unit=data.time_unit,
        requested_scenario_count=data.scenario_count,
        maximum_interpolation_gap=data.maximum_interpolation_gap,
        random_seed=data.random_seed,
        joint_imputer_id=effective_imputer_identity[0],
        joint_imputer_configuration_id=effective_imputer_identity[1],
        post_horizon_rule_id=data.post_horizon_rule_id,
        post_horizon_rule_configuration_id=data.post_horizon_rule_configuration_id,
        date_selected_block_start_dates=selected_ids,
        eligible_block_start_dates=eligible_start_dates,
        source_data_coverage=alignment.coverage,
        interpolations=alignment.interpolations,
        imputation_summary=ImputationSummary(
            sum(item.imputed_value_count for item in imputation_summaries),
            tuple(imputation_summaries),
        ),
        effective_fifo_projection_weights=fifo_effective,
        unused_fifo_projection_weight_keys=unused_fifo_keys,
        effective_scenario_distance_weights=reduction.effective_distance_weights,
        protected_traffic_groups=protected_groups,
        protected_group_representatives=reduction.group_representatives,
        fifo_adjustments=_fifo_summary(fifo_adjustments),
        excluded_blocks=tuple(excluded_blocks),
        candidate_profile_count=len(block_profiles),
        candidate_scenario_count=len(candidates),
        retained_scenario_count=len(reduction.probabilities),
        candidate_assignments=reduction.assignments,
        reduction_cost=reduction.reduction_cost,
        scenario_probabilities=reduction.probabilities,
    )
    publish_scenario_bank(
        data,
        destination,
        retained_profiles,
        reduction.probabilities,
        data.post_horizon_rule_id,
        data.post_horizon_rule_configuration_id,
    )
    return GenerationResult(destination, report)


def _missing_block_dates(data: InputData, start: date) -> tuple[date, ...]:
    known = set(data.historical_dates)
    return tuple(
        start + timedelta(days=offset)
        for offset in range(len(data.target_dates))
        if start + timedelta(days=offset) not in known
    )


def _validate_protected_group_coverage(
    groups: tuple[ProtectedTrafficGroup, ...], eligible_starts: tuple[str, ...]
) -> None:
    eligible = set(eligible_starts)
    for group in groups:
        available_dates = group.block_start_dates & eligible
        if len(available_dates) < group.minimum_representatives:
            raise ScenarioReductionError(
                reason=(
                    f'protected group {group.group_id!r} has only {len(available_dates)} '
                    f'eligible source blocks and requires {group.minimum_representatives}'
                )
            )


def _block_probabilities(data: InputData, blocks: list[BlockData]) -> dict[date, float]:
    masses = {
        block.start_date: (
            data.block_weights[block.start_date]
            if data.block_weights is not None
            else 1.0
        )
        for block in blocks
    }
    total = sum(masses.values())
    if not isfinite(total) or total <= 0.0:
        raise ScenarioInputError(reason='eligible block weights have an invalid total')
    probabilities = {day: value / total for day, value in masses.items()}
    if any(value <= 0.0 for value in probabilities.values()):
        raise ScenarioInputError(
            reason='eligible block weights are too imbalanced for positive float probabilities'
        )
    return probabilities


def _unused_fifo_keys(
    data: InputData,
    options: ScenarioGenerationOptions,
    eligible_starts: set[date],
) -> tuple[tuple[str, str, int, int], ...]:
    arc_order = {arc_id: index for index, arc_id in enumerate(data.arc_ids)}
    unused = [
        key
        for key in options.fifo_projection_weights
        if date.fromisoformat(key[0]) not in eligible_starts
    ]
    return tuple(
        sorted(
            unused,
            key=lambda key: (key[0], arc_order[key[1]], key[2], key[3]),
        )
    )


def _fifo_summary(adjustments: list[FifoAdjustment]) -> FifoAdjustmentSummary:
    values = tuple(adjustments)
    total_change = sum(item.absolute_change for item in values)
    if not isfinite(total_change):
        raise ScenarioGenerationError(
            reason='total FIFO adjustment magnitude is not finite',
            operation='fifo_projection',
        )
    return FifoAdjustmentSummary(
        maximum_absolute_change=max(
            (item.absolute_change for item in values), default=0.0
        ),
        total_absolute_change=total_change,
        adjustments=values,
    )
