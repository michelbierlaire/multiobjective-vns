"""Tests for VRP objective identity, comparison, and declaration building.

Created: 2026-11-03T17:25:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import inf, nan
from pathlib import Path

import pytest

from multiobjective_vns.multiday_vrp import load_vrp_instance
from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    VrpObjectiveFunction,
    build_vrp_objective_declaration,
)
from multiobjective_vns.objectives import ObjectiveComparison

from .input_fixtures import write_minimal_vrp_bundle


def test_objective_declaration_follows_instance_order(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    instance = load_vrp_instance(manifest)

    declaration = build_vrp_objective_declaration(instance)

    assert len(declaration.objectives) == 1
    objective = declaration.objectives[0]
    assert objective.objective_id == 'vehicles-used'
    assert str(objective) == 'vehicles-used'
    assert objective.component_id == (
        'multiobjective-vns:multiday-vrp:objective-function:vehicles-used'
    )


def test_vrp_objective_compares_float_values_exactly() -> None:
    objective = VrpObjectiveFunction(
        objective_id='travel',
        component_id='vrp:objective:travel',
        configuration_id='vrp-float-objective-v1:travel',
    )

    assert objective.compare(1.0, 2.0) is ObjectiveComparison.LESS
    assert objective.compare(2.0, 2.0) is ObjectiveComparison.EQUAL
    assert objective.compare(3.0, 2.0) is ObjectiveComparison.GREATER
    assert objective.compare(1.0, 1.0 + 1e-13) is ObjectiveComparison.LESS


def test_hard_infeasible_value_orders_after_every_finite_value() -> None:
    objective = VrpObjectiveFunction(
        objective_id='travel',
        component_id='vrp:objective:travel',
        configuration_id='vrp-float-objective-v1:travel',
    )

    assert objective.compare(1e300, inf) is ObjectiveComparison.LESS
    assert objective.compare(inf, inf) is ObjectiveComparison.EQUAL
    assert 'infeasible' in objective.format(inf)


@pytest.mark.parametrize('invalid', [nan, -inf, 1, True, '1.0'])
def test_vrp_objective_rejects_values_outside_its_float_domain(invalid: object) -> None:
    objective = VrpObjectiveFunction(
        objective_id='travel',
        component_id='vrp:objective:travel',
        configuration_id='vrp-float-objective-v1:travel',
    )

    with pytest.raises((TypeError, ValueError)):
        objective.compare(invalid, 1.0)


def test_vrp_objective_format_preserves_finite_float_representation() -> None:
    objective = VrpObjectiveFunction(
        objective_id='travel',
        component_id='vrp:objective:travel',
        configuration_id='vrp-float-objective-v1:travel',
    )

    assert objective.format(1.2345678901234567) == '1.2345678901234567'
