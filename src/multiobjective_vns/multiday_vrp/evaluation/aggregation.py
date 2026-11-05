"""Built-in scenario and objective-penalty aggregation rules.

Created: 2026-11-03T18:47:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import fsum, isfinite, sqrt

from multiobjective_vns.multiday_vrp.extensions import (
    PenaltyCategoryInput,
    ScenarioObjectiveInput,
)
from multiobjective_vns.multiday_vrp.input_records import AggregationSpec

__all__ = ['aggregate_penalties', 'aggregate_scenarios', 'validate_finite_result']


def aggregate_scenarios(
    specification: AggregationSpec,
    values: tuple[ScenarioObjectiveInput, ...],
) -> float:
    """Apply a package-provided probability-weighted scenario statistic.

    :param specification: The objective's selected aggregation declaration.
    :param values: Complete scenario costs in ScenarioBank order.
    :returns: One finite Python float.
    :raises ValueError: If values or the aggregation declaration are invalid.
    """
    if not values:
        raise ValueError('scenario aggregation requires at least one value')
    weighted_values: list[tuple[float, float]] = []
    for item in values:
        probability = validate_finite_result(item.probability, 'scenario probability')
        value = validate_finite_result(item.value, 'scenario objective value')
        if probability <= 0.0:
            raise ValueError('scenario probabilities must be positive')
        weighted_values.append((value, probability))

    if specification.id == 'weighted_mean':
        result = fsum(value * probability for value, probability in weighted_values)
    elif specification.id == 'maximum':
        result = max(value for value, _ in weighted_values)
    elif specification.id == 'weighted_quantile':
        quantile = specification.q
        if type(quantile) is not float or not 0.0 < quantile <= 1.0:
            raise ValueError('weighted quantile requires q in (0, 1]')
        cumulative = 0.0
        result = max(value for value, _ in weighted_values)
        for value, probability in sorted(weighted_values, key=lambda item: item[0]):
            cumulative = fsum((cumulative, probability))
            if cumulative >= quantile:
                result = value
                break
    elif specification.id == 'weighted_standard_deviation':
        mean = fsum(value * probability for value, probability in weighted_values)
        result = sqrt(
            fsum(
                probability * (value - mean) ** 2
                for value, probability in weighted_values
            )
        )
    else:
        raise ValueError(f'unknown built-in scenario aggregation {specification.id!r}')
    return validate_finite_result(result, 'scenario aggregation result')


def aggregate_penalties(categories: tuple[PenaltyCategoryInput, ...]) -> float:
    """Apply the built-in ``sum`` aggregation to scaled penalty categories.

    :param categories: Categories in the declared order, before scaling.
    :returns: The finite sum of category scales and finding penalties.
    """
    values: list[float] = []
    for category in categories:
        scale = validate_finite_result(category.scale, 'penalty scale')
        if scale < 0.0:
            raise ValueError('penalty scales must be nonnegative')
        for penalty in category.values:
            amount = validate_finite_result(penalty, 'penalty value')
            if amount < 0.0:
                raise ValueError('penalty values must be nonnegative')
            values.append(scale * amount)
    return validate_finite_result(fsum(values), 'penalty aggregation result')


def validate_finite_result(value: object, label: str) -> float:
    """Require an exact, finite Python float calculation result.

    :param value: Callback or arithmetic result.
    :param label: Name used in the validation error.
    :returns: The validated value.
    :raises TypeError: If the value is not an exact Python float.
    :raises ValueError: If the value is non-finite.
    """
    if type(value) is not float:
        raise TypeError(f'{label} must be a Python float')
    if not isfinite(value):
        raise ValueError(f'{label} must be finite')
    return value
