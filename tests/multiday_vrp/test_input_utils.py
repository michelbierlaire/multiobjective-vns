"""Tests for shared strict parsing helpers used by VRP input loaders.

Created: 2026-11-03T16:30:00+01:00
Author: Michel Bierlaire, EPFL
"""

from pathlib import Path

import pytest

from multiobjective_vns.multiday_vrp.input_errors import VrpInputError
from multiobjective_vns.multiday_vrp.input_utils import (
    parse_finite_float,
    parse_integer,
    parse_offset_datetime,
    read_csv_table,
    read_toml_table,
)


def test_csv_reader_requires_exact_header_and_preserves_row_number(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'values.csv'
    path.write_text('id,value\na,1\n', encoding='utf-8')

    records = read_csv_table(path, ('id', 'value'))

    assert records == ({'id': 'a', 'value': '1'},)

    path.write_text('id,value,value\na,1,2\n', encoding='utf-8')
    with pytest.raises(VrpInputError) as error:
        read_csv_table(path, ('id', 'value'))
    assert error.value.row == 1
    assert error.value.column == 'value'


@pytest.mark.parametrize('text', ['true', 'NaN', 'inf', '1e9999', ''])
def test_finite_float_parser_rejects_non_numeric_or_nonfinite_values(
    text: str,
) -> None:
    with pytest.raises(VrpInputError):
        parse_finite_float(text, file='x.csv', row=2, column='value')


def test_numeric_parsers_reject_bool_spellings_for_integer_fields() -> None:
    with pytest.raises(VrpInputError):
        parse_integer('true', file='x.csv', row=2, column='count')
    assert parse_finite_float('2', file='x.csv', row=2, column='value') == 2.0


def test_toml_parser_rejects_nonfinite_numbers(tmp_path: Path) -> None:
    path = tmp_path / 'settings.toml'
    path.write_text('coefficient = nan\n', encoding='utf-8')

    with pytest.raises(VrpInputError, match='finite'):
        read_toml_table(path)


def test_timestamp_parser_requires_an_explicit_numeric_offset() -> None:
    parsed = parse_offset_datetime(
        '2025-01-02T00:00:00+01:00', file='grid.csv', row=2, column='time'
    )
    assert parsed.utcoffset() is not None

    with pytest.raises(VrpInputError, match='numeric UTC offset'):
        parse_offset_datetime(
            '2025-01-02T00:00:00Z', file='grid.csv', row=2, column='time'
        )
