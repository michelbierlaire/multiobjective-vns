"""Tests for built-in VRP scenario and penalty aggregation rules.

Created: 2026-11-03T18:45:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import pytest

from multiobjective_vns.multiday_vrp.evaluation.aggregation import (
    aggregate_penalties,
    aggregate_scenarios,
)
from multiobjective_vns.multiday_vrp.extensions import (
    PenaltyCategoryInput,
    ScenarioObjectiveInput,
)
from multiobjective_vns.multiday_vrp.input_records import (
    AggregationSpec,
    FrozenTomlTable,
)


def _values() -> tuple[ScenarioObjectiveInput, ...]:
    return (
        ScenarioObjectiveInput('s1', 0.25, 2.0),
        ScenarioObjectiveInput('s2', 0.75, 6.0),
    )


def test_weighted_mean_and_maximum_use_scenario_probabilities() -> None:
    empty = FrozenTomlTable({})
    assert (
        aggregate_scenarios(AggregationSpec('weighted_mean', None, empty), _values())
        == 5.0
    )
    assert (
        aggregate_scenarios(AggregationSpec('maximum', None, empty), _values()) == 6.0
    )


def test_weighted_quantile_uses_smallest_value_reaching_probability_mass() -> None:
    values = (
        ScenarioObjectiveInput('s1', 0.25, 2.0),
        ScenarioObjectiveInput('s2', 0.75, 6.0),
    )

    empty = FrozenTomlTable({})
    assert (
        aggregate_scenarios(AggregationSpec('weighted_quantile', 0.25, empty), values)
        == 2.0
    )
    assert (
        aggregate_scenarios(AggregationSpec('weighted_quantile', 0.5, empty), values)
        == 6.0
    )


def test_weighted_standard_deviation_uses_population_definition() -> None:
    assert (
        aggregate_scenarios(
            AggregationSpec('weighted_standard_deviation', None, FrozenTomlTable({})),
            _values(),
        )
        == 1.7320508075688772
    )


def test_penalty_sum_applies_category_scales_once() -> None:
    categories = (
        PenaltyCategoryInput('early_window', 2.0, (1.5, 0.0)),
        PenaltyCategoryInput('late_window', 0.5, (4.0,)),
    )

    assert aggregate_penalties(categories) == 5.0


@pytest.mark.parametrize('value', [float('nan'), float('inf'), 1, True])
def test_scenario_aggregation_rejects_nonfinite_or_nonfloat_results(
    value: object,
) -> None:
    from multiobjective_vns.multiday_vrp.evaluation.aggregation import (
        validate_finite_result,
    )

    with pytest.raises((TypeError, ValueError)):
        validate_finite_result(value, 'test result')
