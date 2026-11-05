"""Tests for the shared scalar-value contracts.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.values import finite_real, require_integer


def test_integer_validation_excludes_boolean_values() -> None:
    assert require_integer(2, name='count') == 2
    with pytest.raises(TypeError):
        require_integer(True, name='count')


def test_integer_validation_applies_a_minimum() -> None:
    with pytest.raises(ValueError):
        require_integer(0, name='count', minimum=1)


def test_finite_real_converts_supported_values() -> None:
    assert finite_real(3, name='value') == 3.0
    assert finite_real(1.5, name='value') == 1.5


@pytest.mark.parametrize('value', [False, float('nan'), float('-inf'), '2'])
def test_finite_real_rejects_invalid_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        finite_real(value, name='value')  # type: ignore[arg-type]
