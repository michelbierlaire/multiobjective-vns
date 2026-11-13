"""Tests for shared multi-day VRP Operator helpers.

Created: 2026-11-03T18:52:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from math import nan
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.evaluation.objectives import (
    build_vrp_objective_declaration,
)
from multiobjective_vns.multiday_vrp.input_loader import load_vrp_instance
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators import (
    VrpPlanAssessor,
    candidate_is_eligible,
    choose_uniformly,
    operator_result,
    randomized_order,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation

from .input_fixtures import write_minimal_vrp_bundle


class _SequenceRandomSource:
    def __init__(self, values: tuple[object, ...]) -> None:
        self._values: Iterator[object] = iter(values)
        self.calls = 0

    def random(self) -> float:
        self.calls += 1
        return cast(float, next(self._values))


def _random_source(*values: object) -> RandomSource:
    return cast(RandomSource, _SequenceRandomSource(values))


def _instance_and_objectives(
    tmp_path: Path,
) -> tuple[LoadedVrpInstance, ObjectiveDeclaration]:
    manifest = write_minimal_vrp_bundle(tmp_path)
    instance = load_vrp_instance(manifest)
    return instance, build_vrp_objective_declaration(instance)


def test_randomized_sample_uses_shared_draws_without_replacement() -> None:
    source = _SequenceRandomSource((0.5, 0.0))

    selected = randomized_sample(('a', 'b', 'c', 'd'), 2, cast(RandomSource, source))

    assert selected == ('c', 'b')
    assert source.calls == 2


def test_randomized_sample_clamps_count_and_randomizes_full_order() -> None:
    source = _SequenceRandomSource((0.75, 0.0))

    selected = randomized_sample(('a', 'b', 'c'), 10, cast(RandomSource, source))

    assert selected == ('c', 'b', 'a')
    assert source.calls == 2


def test_randomized_order_and_uniform_choice_use_the_shared_source() -> None:
    order_source = _SequenceRandomSource((0.5, 0.0))
    choice_source = _SequenceRandomSource((0.75,))

    assert randomized_order(('a', 'b', 'c'), cast(RandomSource, order_source)) == (
        'b',
        'a',
        'c',
    )
    assert (
        choose_uniformly(('a', 'b', 'c', 'd'), cast(RandomSource, choice_source)) == 'd'
    )


def test_randomized_sample_of_empty_or_zero_count_uses_no_draws() -> None:
    source = _SequenceRandomSource(())

    assert randomized_sample((), 4, cast(RandomSource, source)) == ()
    assert randomized_sample(('a', 'b'), 0, cast(RandomSource, source)) == ()
    assert source.calls == 0


@pytest.mark.parametrize('value', [0, True, nan, -0.1, 1.0])
def test_randomized_sample_rejects_invalid_random_draws(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        randomized_sample(('a', 'b'), 1, _random_source(value))


def test_uniform_choice_rejects_an_empty_population() -> None:
    with pytest.raises(ValueError, match='nonempty'):
        choose_uniformly((), _random_source())


def test_neighborhood_size_must_be_within_the_registration_limit() -> None:
    assert validate_neighborhood_size(3, 5) == 3
    with pytest.raises(ValueError, match='maximum'):
        validate_neighborhood_size(6, 5)
    with pytest.raises(TypeError):
        validate_neighborhood_size(True, 5)
    with pytest.raises(ValueError):
        validate_neighborhood_size(1, 0)


def test_require_vrp_base_checks_the_application_solution_type() -> None:
    solution = VrpSolution((), ())
    context = CandidateContext(solution, 1, False, None, _random_source())

    assert require_vrp_base(context) is solution
    with pytest.raises(TypeError, match='VrpSolution'):
        require_vrp_base(CandidateContext(object(), 1, False, None, _random_source()))


def test_operator_result_matches_candidate_and_modification_count() -> None:
    solution = VrpSolution((), ())

    empty = operator_result(None, modifications=0, requested_size=4)
    result = operator_result(
        solution, modifications=2, requested_size=4, metadata={'source': 'test'}
    )

    assert empty.candidates == ()
    assert result.candidates == (
        CandidateProposal(Candidate(solution, {'source': 'test'}), 2),
    )


@pytest.mark.parametrize(
    ('solution', 'modifications', 'requested_size'),
    [
        (VrpSolution((), ()), 0, 4),
        (None, 1, 4),
        (VrpSolution((), ()), 5, 4),
    ],
)
def test_operator_result_rejects_inconsistent_counts(
    solution: VrpSolution | None,
    modifications: int,
    requested_size: int,
) -> None:
    with pytest.raises(ValueError):
        operator_result(
            solution, modifications=modifications, requested_size=requested_size
        )


def test_candidate_feasibility_requirement_uses_joint_application_assessment(
    tmp_path: Path,
) -> None:
    instance, objectives = _instance_and_objectives(tmp_path)
    solution = VrpSolution((), ())
    assessments = iter(
        (
            ApplicationEvaluation(True, False, (1.0,)),
            ApplicationEvaluation(True, False, (1.0,)),
            ApplicationEvaluation(True, True, (1.0,)),
        )
    )
    calls: list[VrpSolution] = []

    def assess_plan(
        received_instance: object,
        received_objectives: object,
        candidate: VrpSolution,
    ) -> ApplicationEvaluation:
        assert received_instance is instance
        assert received_objectives is objectives
        calls.append(candidate)
        return next(assessments)

    assessor = cast(VrpPlanAssessor, assess_plan)

    assert candidate_is_eligible(
        instance, objectives, solution, ensure_feasible=False, assess_plan=assessor
    )
    assert not candidate_is_eligible(
        instance, objectives, solution, ensure_feasible=True, assess_plan=assessor
    )
    assert candidate_is_eligible(
        instance, objectives, solution, ensure_feasible=True, assess_plan=assessor
    )
    assert calls == [solution, solution, solution]


def test_candidate_feasibility_rejects_invalid_representation_and_bad_assessor(
    tmp_path: Path,
) -> None:
    instance, objectives = _instance_and_objectives(tmp_path)
    solution = VrpSolution((), ())

    def invalid_assessor(
        _instance: object, _objectives: object, _solution: VrpSolution
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(False, None, None)

    def malformed_assessor(
        _instance: object, _objectives: object, _solution: VrpSolution
    ) -> object:
        return object()

    assert not candidate_is_eligible(
        instance,
        objectives,
        solution,
        ensure_feasible=False,
        assess_plan=cast(VrpPlanAssessor, invalid_assessor),
    )
    with pytest.raises(TypeError, match='ApplicationEvaluation'):
        candidate_is_eligible(
            instance,
            objectives,
            solution,
            ensure_feasible=False,
            assess_plan=cast(VrpPlanAssessor, malformed_assessor),
        )


@pytest.mark.parametrize(
    'objective_values',
    [None, (1.0, 2.0)],
)
def test_candidate_screening_rejects_incomplete_valid_assessments(
    tmp_path: Path,
    objective_values: tuple[float, ...] | None,
) -> None:
    instance, objectives = _instance_and_objectives(tmp_path)
    solution = VrpSolution((), ())

    def incomplete_assessor(
        _instance: object, _objectives: object, _solution: VrpSolution
    ) -> ApplicationEvaluation:
        return ApplicationEvaluation(True, True, objective_values)

    with pytest.raises(ValueError, match='objective'):
        candidate_is_eligible(
            instance,
            objectives,
            solution,
            ensure_feasible=True,
            assess_plan=cast(VrpPlanAssessor, incomplete_assessor),
        )
