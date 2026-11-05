"""Helpers for standalone ScenarioBank generation tests.

Created: 2026-11-03T16:45:00+01:00
Author: Michel Bierlaire, EPFL
"""

from datetime import date
from pathlib import Path
from typing import Iterable


def write_scenario_input(
    root: Path,
    *,
    historical_start: date,
    historical_end: date,
    target_grid_rows: Iterable[str],
    observation_rows: Iterable[str],
    scenario_count: int = 1,
    time_unit: str = 'hours',
    maximum_interpolation_gap: float = 0.0,
    arc_ids: tuple[str, ...] = ('arc-1',),
    time_zone: str = 'Europe/Zurich',
    random_seed: int = 11,
    eligible_block_start_dates: tuple[date, ...] = (),
    block_weights: dict[date, float] | None = None,
    extra_manifest: str = '',
) -> Path:
    """Write a file-backed scenario-generation input directory.

    :param root: Temporary directory receiving the files.
    :param historical_start: First historical local-calendar date.
    :param historical_end: Last historical local-calendar date.
    :param target_grid_rows: CSV data rows for ``target-grid.csv``.
    :param observation_rows: CSV data rows for the observations file.
    :param scenario_count: Maximum retained scenario count.
    :param time_unit: Unit for all travel-time data and interpolation gaps.
    :param maximum_interpolation_gap: Maximum bracketing gap.
    :param arc_ids: Ordered directed arc catalog.
    :param time_zone: IANA time zone used by all timestamps.
    :param random_seed: Deterministic imputation seed.
    :param eligible_block_start_dates: Optional ordered candidate starts.
    :param block_weights: Optional start-date weights.
    :param extra_manifest: Additional top-level TOML settings.
    :returns: The scenario-generation manifest path.
    """
    input_dir = root / 'scenario-input'
    input_dir.mkdir(parents=True)
    manifest_lines = [
        'format_version = 1',
        "arc_catalog_file = 'arc-catalog.csv'",
        "target_grid_file = 'target-grid.csv'",
        "observations_file = 'historical-travel-times.csv'",
        f'scenario_count = {scenario_count}',
        f"time_zone = '{time_zone}'",
        f"time_unit = '{time_unit}'",
        f'maximum_interpolation_gap = {maximum_interpolation_gap!r}',
        f'historical_date_start = {historical_start.isoformat()}',
        f'historical_date_end = {historical_end.isoformat()}',
        f'random_seed = {random_seed}',
        extra_manifest.strip(),
    ]
    if eligible_block_start_dates:
        manifest_lines.extend(
            [
                '',
                '[date_selection]',
                'eligible_block_start_dates = [',
                *[f'    {value.isoformat()},' for value in eligible_block_start_dates],
                ']',
            ]
        )
    if block_weights is not None:
        manifest_lines.append("block_weights_file = 'block-weights.csv'")
    manifest = input_dir / 'scenario-generation.toml'
    manifest.write_text('\n'.join(manifest_lines) + '\n', encoding='utf-8')
    (input_dir / 'arc-catalog.csv').write_text(
        'arc_id\n' + ''.join(f'{arc_id}\n' for arc_id in arc_ids),
        encoding='utf-8',
    )
    (input_dir / 'target-grid.csv').write_text(
        'day_index,planning_date,boundary_index,boundary_time\n'
        + ''.join(f'{row}\n' for row in target_grid_rows),
        encoding='utf-8',
    )
    (input_dir / 'historical-travel-times.csv').write_text(
        'arc_id,departure_time,travel_time\n'
        + ''.join(f'{row}\n' for row in observation_rows),
        encoding='utf-8',
    )
    if block_weights is not None:
        (input_dir / 'block-weights.csv').write_text(
            'block_start_date,weight\n'
            + ''.join(
                f'{block_date.isoformat()},{weight!r}\n'
                for block_date, weight in block_weights.items()
            ),
            encoding='utf-8',
        )
    return manifest
