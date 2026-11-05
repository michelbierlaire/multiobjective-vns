"""Tests for shared VRP evaluation comparison and timing helpers.

Created: 2026-11-03T20:25:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import pytest

from multiobjective_vns.multiday_vrp.evaluation.helpers import (
    _custody_event_rank,
    _greater,
    _lower_violation,
    _outside_availability,
    _positive_violation,
    _upper_violation,
)


def test_bound_violations_apply_the_model_tolerance() -> None:
    assert _upper_violation(10.0 + 5e-10, 10.0) == 0.0
    assert _lower_violation(10.0 - 5e-10, 10.0) == 0.0
    assert _positive_violation(5e-10) == 0.0
    assert _upper_violation(10.0 + 2e-9, 10.0) > 0.0
    assert _lower_violation(10.0 - 2e-9, 10.0) > 0.0
    assert _positive_violation(2e-9) > 0.0
    assert _greater(10.0 + 2e-9, 10.0)


def test_outside_availability_uses_the_union_of_intervals() -> None:
    assert _outside_availability(0.0, 5.0, ((0.0, 2.0), (2.0, 4.0), (3.0, 4.5))) == 0.5
    assert _outside_availability(1.0, 3.0, ()) == 2.0


def test_custody_event_rank_preserves_pickup_transfer_delivery_order() -> None:
    assert _custody_event_rank('pickup', None, 2) == 0
    assert _custody_event_rank('transfer_out', 1, 2) == 1
    assert _custody_event_rank('transfer_in', 1, 2) == 2
    assert _custody_event_rank('transfer_out', 2, 2) == 3
    assert _custody_event_rank('transfer_in', 2, 2) == 4
    assert _custody_event_rank('delivery', None, 2) == 5
    with pytest.raises(ValueError, match='unsupported package custody action'):
        _custody_event_rank('unknown', None, 0)
