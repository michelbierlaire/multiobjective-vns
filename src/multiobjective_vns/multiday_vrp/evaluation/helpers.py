"""Shared numeric and ordering helpers for VRP plan evaluation.

Created: 2026-11-03T20:25:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import fsum

__all__ = [
    '_custody_event_rank',
    '_greater',
    '_lower_violation',
    '_outside_availability',
    '_positive_violation',
    '_tolerance',
    '_upper_violation',
]

_TOL_ABSOLUTE = 1e-9
_TOL_RELATIVE = 1e-12


def _lower_violation(value: float, bound: float) -> float:
    if value >= bound - _tolerance(value, bound):
        return 0.0
    return bound - value


def _upper_violation(value: float, bound: float) -> float:
    if value <= bound + _tolerance(value, bound):
        return 0.0
    return value - bound


def _positive_violation(value: float) -> float:
    return 0.0 if value <= _tolerance(value, 0.0) else value


def _greater(value: float, bound: float) -> bool:
    return value > bound + _tolerance(value, bound)


def _tolerance(left: float, right: float) -> float:
    return _TOL_ABSOLUTE + _TOL_RELATIVE * max(abs(left), abs(right))


def _custody_event_rank(
    action: str, step_number: int | None, max_transfers: int
) -> int:
    if action == 'pickup':
        return 0
    if action == 'transfer_out' and step_number is not None:
        return 2 * step_number - 1
    if action == 'transfer_in' and step_number is not None:
        return 2 * step_number
    if action == 'delivery':
        return 2 * max_transfers + 1
    raise ValueError(f'unsupported package custody action {action!r}')


def _outside_availability(
    start: float, end: float, intervals: tuple[tuple[float, float], ...]
) -> float:
    if end <= start:
        return 0.0
    merged: list[tuple[float, float]] = []
    for left, right in sorted(intervals):
        if not merged or left > merged[-1][1]:
            merged.append((left, right))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
    covered = fsum(
        max(0.0, min(end, right) - max(start, left)) for left, right in merged
    )
    return _positive_violation(max(0.0, end - start - covered))
