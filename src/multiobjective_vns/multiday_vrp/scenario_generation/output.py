"""Serialize, validate, and atomically publish a ScenarioBank directory.

Created: 2026-11-03T17:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from multiobjective_vns.multiday_vrp.input_errors import VrpInputError
from multiobjective_vns.multiday_vrp.scenario_bank import load_scenario_bank
from multiobjective_vns.multiday_vrp.scenario_generation.errors import (
    ScenarioGenerationError,
    ScenarioInputError,
)
from multiobjective_vns.multiday_vrp.scenario_generation.input_data import InputData
from multiobjective_vns.multiday_vrp.scenario_generation.records import (
    ProfileCoordinate,
    ScenarioProbability,
)

__all__ = ['publish_scenario_bank', 'validate_output_destination']


def validate_output_destination(destination: Path) -> None:
    """Validate an absent or empty output directory without changing it."""
    if destination.is_symlink():
        raise ScenarioInputError(
            reason='output destination must not be a symbolic link'
        )
    if destination.exists():
        if not destination.is_dir():
            raise ScenarioInputError(reason='output destination must be a directory')
        try:
            next(destination.iterdir())
        except StopIteration:
            return
        except OSError as error:
            raise ScenarioInputError(
                reason=f'cannot inspect output destination {destination}: {error}'
            ) from error
        raise ScenarioInputError(reason='output destination must be absent or empty')


def publish_scenario_bank(
    data: InputData,
    destination: Path,
    profiles: Mapping[str, Mapping[ProfileCoordinate, float]],
    probabilities: tuple[ScenarioProbability, ...],
    post_horizon_rule_id: str,
    post_horizon_rule_configuration_id: str,
) -> None:
    """Write a complete validated bank and publish it with a directory rename."""
    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ScenarioGenerationError(
            reason=str(error), operation='create_output_parent', path=parent
        ) from error
    staging: Path | None = None
    try:
        staging = Path(
            tempfile.mkdtemp(prefix=f'.{destination.name}.scenario-bank-', dir=parent)
        )
        _write_bank_files(
            staging,
            data,
            profiles,
            probabilities,
            post_horizon_rule_id,
            post_horizon_rule_configuration_id,
        )
        try:
            load_scenario_bank(staging / 'scenario-bank.toml')
        except VrpInputError as error:
            raise ScenarioGenerationError(
                reason=f'serialized bank validation failed: {error}',
                operation='validate_serialized_bank',
                path=staging,
            ) from error
        _publish(staging, destination)
        staging = None
    except ScenarioGenerationError:
        raise
    except OSError as error:
        operation = 'write_or_publish'
        failed_path = destination if staging is None else staging
        raise ScenarioGenerationError(
            reason=str(error), operation=operation, path=failed_path
        ) from error
    finally:
        if staging is not None and staging.exists():
            try:
                shutil.rmtree(staging)
            except OSError as error:
                raise ScenarioGenerationError(
                    reason=f'failed to remove staging directory: {error}',
                    operation='remove_staging_directory',
                    path=staging,
                ) from error


def _write_bank_files(
    directory: Path,
    data: InputData,
    profiles: Mapping[str, Mapping[ProfileCoordinate, float]],
    probabilities: tuple[ScenarioProbability, ...],
    post_horizon_rule_id: str,
    post_horizon_rule_configuration_id: str,
) -> None:
    _write_csv(
        directory / 'arc-catalog.csv', ('arc_id',), ((arc,) for arc in data.arc_ids)
    )
    _write_csv(
        directory / 'target-grid.csv',
        ('day_index', 'planning_date', 'boundary_index', 'boundary_time'),
        (
            (day, planning_date.isoformat(), boundary, timestamp)
            for day, planning_date, boundary, timestamp in data.target_grid_rows
        ),
    )
    _write_csv(
        directory / 'scenarios.csv',
        ('scenario_id', 'probability'),
        ((item.scenario_id, repr(item.probability)) for item in probabilities),
    )
    _write_csv(
        directory / 'travel-times.csv',
        ('scenario_id', 'arc_id', 'day_index', 'boundary_index', 'travel_time'),
        (
            (scenario_id, arc_id, day_index, boundary_index, repr(value))
            for scenario_id, profile in profiles.items()
            for arc_id in data.arc_ids
            for day_index, day in enumerate(data.target_boundaries, start=1)
            for boundary_index in range(len(day))
            for value in (profile[(arc_id, day_index, boundary_index)],)
        ),
    )
    manifest = '\n'.join(
        (
            'format_version = 1',
            "arc_catalog_file = 'arc-catalog.csv'",
            "target_grid_file = 'target-grid.csv'",
            f'time_zone = {_toml_string(data.time_zone_name)}',
            f'time_unit = {_toml_string(data.time_unit)}',
            f'scenario_count = {len(probabilities)}',
            "scenarios_file = 'scenarios.csv'",
            "travel_times_file = 'travel-times.csv'",
            f'post_horizon_rule_id = {_toml_string(post_horizon_rule_id)}',
            'post_horizon_rule_configuration_id = '
            f'{_toml_string(post_horizon_rule_configuration_id)}',
            '',
        )
    )
    (directory / 'scenario-bank.toml').write_text(manifest, encoding='utf-8')


def _write_csv(
    path: Path,
    columns: tuple[str, ...],
    rows: Iterable[Sequence[object]],
) -> None:
    with path.open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow(columns)
        writer.writerows(rows)


def _publish(staging: Path, destination: Path) -> None:
    if destination.is_symlink():
        raise ScenarioGenerationError(
            reason='destination became a symbolic link before publication',
            operation='publish',
            path=destination,
        )
    if destination.exists():
        if not destination.is_dir():
            raise ScenarioGenerationError(
                reason='destination became a non-directory before publication',
                operation='publish',
                path=destination,
            )
        try:
            next(destination.iterdir())
        except StopIteration:
            destination.rmdir()
        else:
            raise ScenarioGenerationError(
                reason='destination became nonempty before publication',
                operation='publish',
                path=destination,
            )
    os.replace(staging, destination)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
