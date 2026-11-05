"""Tests for standalone traffic scenario generation.

Created: 2026-11-03T17:05:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import csv
import random
from collections.abc import Mapping, MutableMapping
from datetime import date, datetime
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp import (
    generate_scenario_bank as package_generate_scenario_bank,
)
from multiobjective_vns.multiday_vrp.scenario_bank import load_scenario_bank
from multiobjective_vns.multiday_vrp.scenario_generation import (
    AlignedHistoricalData,
    GenerationResult,
    ImputationRequest,
    JointImputer,
    ProfileCoordinate,
    ProtectedTrafficGroup,
    ScenarioGenerationError,
    ScenarioGenerationOptions,
    ScenarioInputError,
    ScenarioReductionError,
    generate_scenario_bank,
)

from .scenario_generation_fixtures import write_scenario_input


def _rows_for_values(values: tuple[float, ...], *, day: date) -> list[str]:
    return [
        f'arc-1,{day.isoformat()}T{clock}:00:00+01:00,{value}'
        for clock, value in zip(('00', '12'), values, strict=True)
    ]


def _write_two_point_input(
    root: Path,
    *,
    values_by_date: dict[date, tuple[float, float]],
    scenario_count: int = 1,
    extra_manifest: str = '',
    observations: list[str] | None = None,
) -> Path:
    start = min(values_by_date)
    end = max(values_by_date)
    if observations is None:
        observations = [
            row
            for day, values in values_by_date.items()
            for row in _rows_for_values(values, day=day)
        ]
    manifest = write_scenario_input(
        root,
        historical_start=start,
        historical_end=end,
        target_grid_rows=(
            '1,2027-01-01,0,2027-01-01T00:00:00+01:00',
            '1,2027-01-01,1,2027-01-01T12:00:00+01:00',
            '1,2027-01-01,2,2027-01-02T00:00:00+01:00',
        ),
        observation_rows=observations,
        scenario_count=scenario_count,
        extra_manifest=extra_manifest,
    )
    return manifest


def test_generate_writes_a_loadable_bank_and_complete_report(tmp_path: Path) -> None:
    first = date(2024, 1, 1)
    second = date(2024, 1, 2)
    manifest = _write_two_point_input(
        tmp_path,
        values_by_date={first: (2.0, 3.0), second: (4.0, 5.0)},
        scenario_count=2,
    )

    result = generate_scenario_bank(manifest, tmp_path / 'bank')

    assert isinstance(result, GenerationResult)
    assert result.output_directory == tmp_path / 'bank'
    assert result.report.candidate_profile_count == 2
    assert result.report.retained_scenario_count == 2
    assert [item.probability for item in result.report.scenario_probabilities] == [
        0.5,
        0.5,
    ]
    bank = load_scenario_bank(result.output_directory / 'scenario-bank.toml')
    assert bank.arc_ids == ('arc-1',)
    assert bank.time_zone == 'Europe/Zurich'
    assert len(bank.scenarios) == 2
    assert bank.travel_times[('scenario-000001', 'arc-1', 1, 0)] == 2.0


def test_generation_reduces_profiles_and_transfers_probability(tmp_path: Path) -> None:
    dates = [date(2024, 1, day) for day in (1, 2, 3)]
    manifest = _write_two_point_input(
        tmp_path,
        values_by_date={
            dates[0]: (0.0, 0.0),
            dates[1]: (1.0, 1.0),
            dates[2]: (10.0, 10.0),
        },
        scenario_count=2,
    )

    result = generate_scenario_bank(manifest, tmp_path / 'bank')

    assert result.report.candidate_profile_count == 3
    assert result.report.candidate_scenario_count == 3
    assert result.report.retained_scenario_count == 2
    assert sum(item.probability for item in result.report.scenario_probabilities) == 1.0
    assert sum(
        item.transferred_probability_mass
        for item in result.report.candidate_assignments
    ) == pytest.approx(1 / 3)
    assert result.report.reduction_cost > 0


def test_identical_profiles_are_coalesced_before_reduction(tmp_path: Path) -> None:
    dates = [date(2024, 1, day) for day in (1, 2)]
    manifest = _write_two_point_input(
        tmp_path,
        values_by_date={dates[0]: (3.0, 3.0), dates[1]: (3.0, 3.0)},
        scenario_count=2,
    )

    result = generate_scenario_bank(manifest, tmp_path / 'bank')

    assert result.report.candidate_profile_count == 2
    assert result.report.candidate_scenario_count == 1
    assert result.report.retained_scenario_count == 1
    assert result.report.scenario_probabilities[0].probability == 1.0
    assert result.report.candidate_assignments[0].source_block_start_dates == (
        '2024-01-01',
        '2024-01-02',
    )


def test_short_internal_gaps_are_interpolated_and_reported(tmp_path: Path) -> None:
    day = date(2024, 1, 1)
    observations = [
        'arc-1,2024-01-01T00:00:00+01:00,2.0',
        'arc-1,2024-01-01T12:00:00+01:00,4.0',
    ]
    manifest = write_scenario_input(
        tmp_path,
        historical_start=day,
        historical_end=day,
        target_grid_rows=(
            '1,2027-01-01,0,2027-01-01T00:00:00+01:00',
            '1,2027-01-01,1,2027-01-01T06:00:00+01:00',
            '1,2027-01-01,2,2027-01-01T12:00:00+01:00',
            '1,2027-01-01,3,2027-01-02T00:00:00+01:00',
        ),
        observation_rows=observations,
        maximum_interpolation_gap=13,
    )

    result = generate_scenario_bank(manifest, tmp_path / 'bank')

    assert result.report.source_data_coverage.interpolated_value_count == 1
    assert len(result.report.interpolations) == 1
    assert result.report.interpolations[0].interpolated_value == 3.0


def test_unresolved_missing_values_exclude_blocks_without_an_imputer(
    tmp_path: Path,
) -> None:
    first = date(2024, 1, 1)
    second = date(2024, 1, 2)
    manifest = _write_two_point_input(
        tmp_path,
        values_by_date={first: (2.0, 3.0), second: (4.0, 5.0)},
        observations=['arc-1,2024-01-01T00:00:00+01:00,2.0'],
        scenario_count=1,
    )

    with pytest.raises(ScenarioGenerationError, match='eligible'):
        generate_scenario_bank(manifest, tmp_path / 'bank')


class _Imputer:
    extension_id = 'test-imputer'
    configuration_id = 'v1'

    def __init__(self) -> None:
        self.fit_calls = 0
        self.requests: list[object] = []

    def fit(
        self, aligned_history: AlignedHistoricalData, random_source: random.Random
    ) -> None:
        self.fit_calls += 1

    def impute_missing(
        self, request: ImputationRequest, random_source: random.Random
    ) -> dict[ProfileCoordinate, float]:
        self.requests.append(request)
        return {key: 7.0 for key, value in request.values.items() if value is None}


def test_joint_imputer_fills_exact_missing_coordinates(tmp_path: Path) -> None:
    day = date(2024, 1, 1)
    manifest = write_scenario_input(
        tmp_path,
        historical_start=day,
        historical_end=day,
        target_grid_rows=(
            '1,2027-01-01,0,2027-01-01T00:00:00+01:00',
            '1,2027-01-01,1,2027-01-01T12:00:00+01:00',
            '1,2027-01-01,2,2027-01-02T00:00:00+01:00',
        ),
        observation_rows=['arc-1,2024-01-01T00:00:00+01:00,2.0'],
    )
    imputer = _Imputer()

    result = generate_scenario_bank(
        manifest,
        tmp_path / 'bank',
        options=ScenarioGenerationOptions(joint_imputer=imputer),
    )

    assert imputer.fit_calls == 1
    assert len(imputer.requests) == 1
    assert result.report.imputation_summary.imputed_value_count == 1
    bank = load_scenario_bank(result.output_directory / 'scenario-bank.toml')
    assert bank.travel_times[('scenario-000001', 'arc-1', 1, 1)] == 7.0


def test_joint_imputer_must_return_every_missing_coordinate(tmp_path: Path) -> None:
    day = date(2024, 1, 1)
    manifest = write_scenario_input(
        tmp_path,
        historical_start=day,
        historical_end=day,
        target_grid_rows=(
            '1,2027-01-01,0,2027-01-01T00:00:00+01:00',
            '1,2027-01-01,1,2027-01-01T12:00:00+01:00',
            '1,2027-01-01,2,2027-01-02T00:00:00+01:00',
        ),
        observation_rows=['arc-1,2024-01-01T00:00:00+01:00,2.0'],
    )

    class PartialImputer(_Imputer):
        def impute_missing(
            self, request: ImputationRequest, random_source: random.Random
        ) -> dict[ProfileCoordinate, float]:
            return {}

    with pytest.raises(ScenarioGenerationError, match='missing'):
        generate_scenario_bank(
            manifest,
            tmp_path / 'bank',
            options=ScenarioGenerationOptions(joint_imputer=PartialImputer()),
        )


def test_joint_imputer_callback_exception_keeps_identity_and_cause(
    tmp_path: Path,
) -> None:
    day = date(2024, 1, 1)
    manifest = write_scenario_input(
        tmp_path,
        historical_start=day,
        historical_end=day,
        target_grid_rows=(
            '1,2027-01-01,0,2027-01-01T00:00:00+01:00',
            '1,2027-01-01,1,2027-01-01T12:00:00+01:00',
            '1,2027-01-01,2,2027-01-02T00:00:00+01:00',
        ),
        observation_rows=['arc-1,2024-01-01T00:00:00+01:00,2.0'],
    )

    class RaisingImputer(_Imputer):
        def fit(
            self, aligned_history: AlignedHistoricalData, random_source: random.Random
        ) -> None:
            raise RuntimeError('fit broke')

    with pytest.raises(ScenarioGenerationError, match='fit failed') as caught:
        generate_scenario_bank(
            manifest,
            tmp_path / 'bank',
            options=ScenarioGenerationOptions(joint_imputer=RaisingImputer()),
        )

    assert caught.value.extension_id == 'test-imputer'
    assert caught.value.method == 'fit'
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_fifo_projection_corrects_non_fifo_profiles(tmp_path: Path) -> None:
    day = date(2024, 1, 1)
    manifest = write_scenario_input(
        tmp_path,
        historical_start=day,
        historical_end=day,
        target_grid_rows=(
            '1,2027-01-01,0,2027-01-01T00:00:00+01:00',
            '1,2027-01-01,1,2027-01-01T12:00:00+01:00',
            '1,2027-01-01,2,2027-01-02T00:00:00+01:00',
        ),
        observation_rows=[
            'arc-1,2024-01-01T00:00:00+01:00,20.0',
            'arc-1,2024-01-01T12:00:00+01:00,1.0',
        ],
    )

    result = generate_scenario_bank(manifest, tmp_path / 'bank')

    assert result.report.fifo_adjustments.adjustments
    bank = load_scenario_bank(result.output_directory / 'scenario-bank.toml')
    travel = [bank.travel_times[('scenario-000001', 'arc-1', 1, i)] for i in range(3)]
    arrivals = [time + value for time, value in zip((0.0, 12.0, 24.0), travel)]
    assert arrivals == sorted(arrivals)


def test_protected_groups_reject_infeasible_representative_count(
    tmp_path: Path,
) -> None:
    dates = [date(2024, 1, day) for day in (1, 2)]
    manifest = _write_two_point_input(
        tmp_path,
        values_by_date={dates[0]: (1.0, 1.0), dates[1]: (2.0, 2.0)},
        scenario_count=1,
    )
    groups = (
        ProtectedTrafficGroup('morning', frozenset({'2024-01-01'}), 1),
        ProtectedTrafficGroup('evening', frozenset({'2024-01-02'}), 1),
    )

    with pytest.raises(ScenarioReductionError, match='protected'):
        generate_scenario_bank(
            manifest,
            tmp_path / 'bank',
            options=ScenarioGenerationOptions(protected_traffic_groups=groups),
        )


def test_user_weights_determine_empirical_block_probabilities(tmp_path: Path) -> None:
    first = date(2024, 1, 1)
    second = date(2024, 1, 2)
    manifest = write_scenario_input(
        tmp_path,
        historical_start=first,
        historical_end=second,
        target_grid_rows=(
            '1,2027-01-01,0,2027-01-01T00:00:00+01:00',
            '1,2027-01-01,1,2027-01-01T12:00:00+01:00',
            '1,2027-01-01,2,2027-01-02T00:00:00+01:00',
        ),
        observation_rows=[
            *_rows_for_values((2.0, 2.0), day=first),
            *_rows_for_values((4.0, 4.0), day=second),
        ],
        scenario_count=2,
        block_weights={first: 2.0, second: 1.0},
    )

    result = generate_scenario_bank(manifest, tmp_path / 'bank')

    assert [item.probability for item in result.report.scenario_probabilities] == [
        pytest.approx(2 / 3),
        pytest.approx(1 / 3),
    ]


def test_repeated_local_time_uses_the_target_offset_to_select_source_occurrence(
    tmp_path: Path,
) -> None:
    day = date(2024, 10, 27)
    manifest = write_scenario_input(
        tmp_path,
        historical_start=day,
        historical_end=day,
        target_grid_rows=(
            '1,2027-10-31,0,2027-10-31T00:00:00+02:00',
            '1,2027-10-31,1,2027-10-31T02:30:00+02:00',
            '1,2027-10-31,2,2027-10-31T02:30:00+01:00',
            '1,2027-10-31,3,2027-11-01T00:00:00+01:00',
        ),
        observation_rows=[
            'arc-1,2024-10-27T00:00:00+02:00,1.0',
            'arc-1,2024-10-27T02:30:00+02:00,1.0',
            'arc-1,2024-10-27T02:30:00+01:00,2.0',
        ],
    )

    result = generate_scenario_bank(manifest, tmp_path / 'bank')
    bank = load_scenario_bank(result.output_directory / 'scenario-bank.toml')

    assert bank.travel_times[('scenario-000001', 'arc-1', 1, 1)] == 1.0
    assert bank.travel_times[('scenario-000001', 'arc-1', 1, 2)] == 2.0


def test_spring_forward_missing_clock_value_is_not_interpolated(tmp_path: Path) -> None:
    spring_day = date(2024, 3, 31)
    next_day = date(2024, 4, 1)
    manifest = write_scenario_input(
        tmp_path,
        historical_start=spring_day,
        historical_end=next_day,
        target_grid_rows=(
            '1,2027-01-01,0,2027-01-01T00:00:00+01:00',
            '1,2027-01-01,1,2027-01-01T02:30:00+01:00',
            '1,2027-01-01,2,2027-01-01T04:00:00+01:00',
            '1,2027-01-01,3,2027-01-02T00:00:00+01:00',
        ),
        observation_rows=[
            'arc-1,2024-03-31T00:00:00+01:00,2.0',
            'arc-1,2024-03-31T04:00:00+02:00,2.0',
            'arc-1,2024-04-01T00:00:00+02:00,3.0',
            'arc-1,2024-04-01T02:30:00+02:00,3.0',
            'arc-1,2024-04-01T04:00:00+02:00,3.0',
        ],
    )

    result = generate_scenario_bank(manifest, tmp_path / 'bank')

    assert result.report.source_data_coverage.unresolved_missing_value_count == 1
    assert result.report.excluded_blocks[0].block_start_date == '2024-03-31'
    assert result.report.excluded_blocks[0].reason == 'unresolved_missing_values'
    assert result.report.eligible_block_start_dates == ('2024-04-01',)


def test_custom_post_horizon_rule_is_checked_at_the_final_boundary(
    tmp_path: Path,
) -> None:
    day = date(2024, 1, 1)
    manifest = _write_two_point_input(
        tmp_path,
        values_by_date={day: (2.0, 3.0)},
        extra_manifest=(
            "post_horizon_rule_id = 'custom'\n"
            "post_horizon_rule_configuration_id = 'config-1'"
        ),
    )

    class CustomRule:
        rule_id = 'custom'
        configuration_id = 'config-1'

        def travel_time_at(
            self,
            arc_id: str,
            departure_time: datetime,
            final_boundary_time: datetime,
            final_boundary_profile: Mapping[str, float],
            time_unit: str,
        ) -> float:
            return final_boundary_profile[arc_id]

    result = generate_scenario_bank(
        manifest,
        tmp_path / 'bank',
        options=ScenarioGenerationOptions(
            post_horizon_rules={('custom', 'config-1'): CustomRule()}
        ),
    )

    assert result.report.post_horizon_rule_id == 'custom'
    assert result.report.post_horizon_rule_configuration_id == 'config-1'


def test_custom_post_horizon_rule_must_match_final_boundary(tmp_path: Path) -> None:
    day = date(2024, 1, 1)
    manifest = _write_two_point_input(
        tmp_path,
        values_by_date={day: (2.0, 3.0)},
        extra_manifest=(
            "post_horizon_rule_id = 'custom'\n"
            "post_horizon_rule_configuration_id = 'config-1'"
        ),
    )

    class IncorrectRule:
        rule_id = 'custom'
        configuration_id = 'config-1'

        def travel_time_at(
            self,
            arc_id: str,
            departure_time: datetime,
            final_boundary_time: datetime,
            final_boundary_profile: Mapping[str, float],
            time_unit: str,
        ) -> float:
            return final_boundary_profile[arc_id] + 10.0

    with pytest.raises(ScenarioGenerationError, match='does not match'):
        generate_scenario_bank(
            manifest,
            tmp_path / 'bank',
            options=ScenarioGenerationOptions(
                post_horizon_rules={('custom', 'config-1'): IncorrectRule()}
            ),
        )


def test_empty_destination_is_replaced_by_completed_bank(tmp_path: Path) -> None:
    day = date(2024, 1, 1)
    manifest = _write_two_point_input(
        tmp_path,
        values_by_date={day: (2.0, 3.0)},
    )
    destination = tmp_path / 'bank'
    destination.mkdir()

    result = generate_scenario_bank(manifest, destination)

    assert result.output_directory == destination
    assert (destination / 'scenario-bank.toml').is_file()


def test_nonempty_destination_is_preserved(tmp_path: Path) -> None:
    day = date(2024, 1, 1)
    manifest = _write_two_point_input(
        tmp_path,
        values_by_date={day: (2.0, 3.0)},
    )
    destination = tmp_path / 'bank'
    destination.mkdir()
    existing = destination / 'keep.txt'
    existing.write_text('keep', encoding='utf-8')

    with pytest.raises(ScenarioInputError, match='empty'):
        generate_scenario_bank(manifest, destination)

    assert existing.read_text(encoding='utf-8') == 'keep'


def test_malformed_observation_is_reported_as_input_error(tmp_path: Path) -> None:
    day = date(2024, 1, 1)
    manifest = _write_two_point_input(
        tmp_path,
        values_by_date={day: (2.0, 3.0)},
        observations=['unknown-arc,2024-01-01T00:00:00+01:00,2.0'],
    )

    with pytest.raises(ScenarioInputError, match='arc'):
        generate_scenario_bank(manifest, tmp_path / 'bank')


def test_scenario_generation_options_snapshot_mappings() -> None:
    source = {('arc-1', 1, 0): 2}
    options = ScenarioGenerationOptions(scenario_distance_weights=source)
    source[('arc-1', 1, 0)] = 4

    assert options.scenario_distance_weights[('arc-1', 1, 0)] == 2.0
    with pytest.raises(TypeError):
        cast(
            MutableMapping[ProfileCoordinate, int | float],
            options.scenario_distance_weights,
        )[('arc-1', 1, 0)] = 3.0


def test_protected_group_and_options_validate_python_integer_types() -> None:
    with pytest.raises(ValueError, match='minimum_representatives'):
        ProtectedTrafficGroup('group', frozenset({'2024-01-01'}), True)
    with pytest.raises(ValueError, match='weight'):
        ScenarioGenerationOptions(scenario_distance_weights={('arc-1', 1, 0): True})


def test_result_records_and_extension_protocol_are_public() -> None:
    assert JointImputer is not None
    assert GenerationResult is not None
    assert package_generate_scenario_bank is generate_scenario_bank


def test_serialized_scenario_csv_has_stable_ids_and_order(tmp_path: Path) -> None:
    day = date(2024, 1, 1)
    manifest = _write_two_point_input(
        tmp_path,
        values_by_date={day: (2.0, 3.0)},
    )

    result = generate_scenario_bank(manifest, tmp_path / 'bank')

    with (result.output_directory / 'scenarios.csv').open(
        'r', encoding='utf-8', newline=''
    ) as file:
        rows = tuple(csv.DictReader(file))
    assert rows == ({'scenario_id': 'scenario-000001', 'probability': '1.0'},)
