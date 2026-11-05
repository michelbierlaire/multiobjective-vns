"""Tests for precise VRP input diagnostics.

Created: 2026-11-03T16:30:00+01:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.multiday_vrp.input_errors import VrpInputError


def test_input_error_retains_file_row_column_and_reason() -> None:
    error = VrpInputError(
        file='vehicles.csv',
        reason='must be a positive integer',
        row=4,
        column='seat_capacity',
    )

    assert error.file == 'vehicles.csv'
    assert error.row == 4
    assert error.column == 'seat_capacity'
    assert error.reason == 'must be a positive integer'
    assert 'vehicles.csv:4:seat_capacity' in str(error)
