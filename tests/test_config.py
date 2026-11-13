"""Tests for immutable search configuration contracts.

Created: 2026-10-29T13:18:36+00:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.config import (
    BudgetLimits,
    MultiobjectiveVNSConfig,
)
from multiobjective_vns.values import finite_real


def test_configuration_defaults_match_the_design() -> None:
    limits = BudgetLimits()
    config = MultiobjectiveVNSConfig(max_candidates_per_attempt=1)

    assert limits.max_wall_time_seconds == 300.0
    assert limits.max_candidate_attempts == 10000
    assert limits.max_evaluation_requests == 10000
    assert config.feasibility_mode == 'strict'
    assert config.composition_selection_probability == 0.5
    assert config.feedback_selection_mode == 'performance_matching'
    assert config.feedback_epsilon == 1e-6
    assert config.feedback_mu == 1.0
    assert config.max_candidates_per_attempt == 1
    assert config.random_seed == 0


def test_candidate_limit_must_be_supplied() -> None:
    with pytest.raises(TypeError):
        MultiobjectiveVNSConfig()  # type: ignore[call-arg]


def test_finite_real_normalizes_integer_and_float_inputs() -> None:
    assert finite_real(3) == 3.0
    assert finite_real(2.5) == 2.5
    assert BudgetLimits(max_wall_time_seconds=15).max_wall_time_seconds == 15.0


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), '1.0'])
def test_finite_real_rejects_nonfinite_or_unsupported_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        finite_real(value)  # type: ignore[arg-type]


@pytest.mark.parametrize('value', [0, -1, True, 1.5])
def test_config_requires_a_positive_integer_candidate_limit(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        MultiobjectiveVNSConfig(
            max_candidates_per_attempt=value  # type: ignore[arg-type]
        )


def test_config_enforces_cross_field_feasibility_rules() -> None:
    with pytest.raises(ValueError):
        MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            feasibility_mode='relaxed',
        )

    config = MultiobjectiveVNSConfig(
        max_candidates_per_attempt=1,
        feasibility_mode='relaxed',
        max_infeasible_steps=4,
    )
    assert config.max_infeasible_steps == 4


@pytest.mark.parametrize('epsilon', [0.0, -0.1, 1.0, 1.1, True, float('nan')])
def test_config_requires_a_positive_probability_floor(epsilon: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            feedback_epsilon=epsilon,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize('epsilon', [1e-9, 0.01, 0.49])
def test_config_accepts_a_positive_probability_floor_below_one(
    epsilon: float,
) -> None:
    config = MultiobjectiveVNSConfig(
        max_candidates_per_attempt=1,
        feedback_epsilon=epsilon,
    )

    assert config.feedback_epsilon == epsilon


@pytest.mark.parametrize('mu', [-1.0, True, float('nan'), float('inf'), '1.0'])
def test_config_rejects_invalid_feedback_intensity(mu: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            feedback_mu=mu,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize('mu', [0.0, 0.25, 1.0])
def test_config_accepts_nonnegative_feedback_intensity(mu: float) -> None:
    config = MultiobjectiveVNSConfig(
        max_candidates_per_attempt=1,
        feedback_mu=mu,
    )

    assert config.feedback_mu == mu


@pytest.mark.parametrize('probability', [-0.1, 1.1, True, float('nan')])
def test_config_validates_composition_selection_probability(
    probability: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            composition_selection_probability=probability,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize('probability', [0, 0.2, 1])
def test_config_accepts_composition_selection_probability(probability: float) -> None:
    assert (
        MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1, composition_selection_probability=probability
        ).composition_selection_probability
        == probability
    )


def test_configuration_is_immutable() -> None:
    config = MultiobjectiveVNSConfig(max_candidates_per_attempt=1)
    with pytest.raises((AttributeError, TypeError)):
        config.random_seed = 17  # type: ignore[misc]
