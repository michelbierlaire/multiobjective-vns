"""Scalar validation shared by public records and configuration.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from math import isfinite
from typing import TypeAlias

Integer: TypeAlias = int
FiniteReal: TypeAlias = int | float


def require_integer(
    value: object,
    *,
    name: str,
    minimum: int | None = None,
) -> int:
    """Validate an integer field, excluding Boolean values.

    :param value: Value supplied to a public integer field.
    :param name: Field name used in a validation error.
    :param minimum: Optional inclusive lower bound.
    :returns: The validated integer value.
    :raises TypeError: If ``value`` is not an integer or is a Boolean.
    :raises ValueError: If ``value`` is below ``minimum``.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f'{name} must be an integer excluding bool')
    if minimum is not None and value < minimum:
        raise ValueError(f'{name} must be at least {minimum}')
    return value


def finite_real(value: FiniteReal, *, name: str = 'value') -> float:
    """Normalize a supported numeric input to a finite Python float.

    :param value: Python integer or float, excluding Boolean values.
    :param name: Field name used in a validation error.
    :returns: The normalized finite float.
    :raises TypeError: If ``value`` is not an integer or float, or is Boolean.
    :raises ValueError: If conversion overflows or yields a non-finite value.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f'{name} must be an int or float excluding bool')
    try:
        normalized = float(value)
    except OverflowError as error:
        raise ValueError(f'{name} must be finite') from error
    if not isfinite(normalized):
        raise ValueError(f'{name} must be finite')
    return normalized
