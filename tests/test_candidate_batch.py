"""Tests for bounded multi-candidate Operator adaptation.

Created: 2026-11-08T12:00:00+01:00
Author: Michel Bierlaire, EPFL
"""

from typing import cast

from multiobjective_vns.candidate_batch import CandidateBatchAdapter
from multiobjective_vns.random_source import RandomSource as RandomSourceProtocol
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)


class SequenceOperator:
    marker = 'wrapped-marker'

    def __init__(self) -> None:
        self.results = [
            OperatorResult(candidates=(CandidateProposal(Candidate('first'), 1),)),
            OperatorResult(candidates=(CandidateProposal(Candidate('first'), 1),)),
            OperatorResult(candidates=(CandidateProposal(Candidate('second'), 1),)),
        ]
        self.limits: list[int] = []

    def call(self, context: CandidateContext) -> OperatorResult:
        self.limits.append(context.max_candidates)
        return self.results.pop(0)


class RandomSource:
    def random(self) -> float:
        return 0.5


def test_adapter_collects_distinct_candidates_in_sample_order() -> None:
    delegate = SequenceOperator()
    adapter = CandidateBatchAdapter(delegate)
    assert adapter.delegate is delegate
    assert adapter.marker == 'wrapped-marker'
    context = CandidateContext(
        base='base',
        k=2,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSourceProtocol, RandomSource()),
        max_candidates=3,
    )

    result = adapter.call(context)

    assert tuple(item.candidate.solution for item in result.candidates) == (
        'first',
        'second',
    )
    assert tuple(item.m for item in result.candidates) == (1, 1)
    assert delegate.limits == [3, 2, 2]


def test_adapter_respects_a_child_that_already_returns_multiple_candidates() -> None:
    class MultiProposalOperator:
        def call(self, context: CandidateContext) -> OperatorResult:
            return OperatorResult(
                candidates=tuple(
                    CandidateProposal(Candidate(index), 1)
                    for index in range(context.max_candidates)
                )
            )

    context = CandidateContext(
        base='base',
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSourceProtocol, RandomSource()),
        max_candidates=2,
    )

    result = CandidateBatchAdapter(MultiProposalOperator()).call(context)

    assert tuple(item.candidate.solution for item in result.candidates) == (0, 1)


def test_adapter_uses_application_equivalence_for_distinctness() -> None:
    class CaseVariantOperator:
        def __init__(self) -> None:
            self.values = iter(('plan-a', 'PLAN-A', 'plan-b'))

        def call(self, context: CandidateContext) -> OperatorResult:
            del context
            value = next(self.values)
            return OperatorResult(candidates=(CandidateProposal(Candidate(value), 1),))

    context = CandidateContext(
        base='base',
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSourceProtocol, RandomSource()),
        max_candidates=3,
    )

    result = CandidateBatchAdapter(
        CaseVariantOperator(),
        equivalent=lambda left, right: str(left).lower() == str(right).lower(),
    ).call(context)

    assert tuple(item.candidate.solution for item in result.candidates) == (
        'plan-a',
        'plan-b',
    )


def test_adapter_skips_candidates_equivalent_to_the_base() -> None:
    class BaseSymmetryOperator:
        def __init__(self) -> None:
            self.values = iter(('BASE', 'distinct'))

        def call(self, context: CandidateContext) -> OperatorResult:
            del context
            return OperatorResult(
                candidates=(CandidateProposal(Candidate(next(self.values)), 1),)
            )

    context = CandidateContext(
        base='base',
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSourceProtocol, RandomSource()),
        max_candidates=2,
    )

    result = CandidateBatchAdapter(
        BaseSymmetryOperator(),
        equivalent=lambda left, right: str(left).lower() == str(right).lower(),
    ).call(context)

    assert tuple(item.candidate.solution for item in result.candidates) == ('distinct',)


def test_adapter_bounds_empty_candidate_sampling_by_the_output_limit() -> None:
    class NoCandidateOperator:
        calls = 0

        def call(self, context: CandidateContext) -> OperatorResult:
            del context
            self.calls += 1
            return OperatorResult(candidates=())

    delegate = NoCandidateOperator()
    context = CandidateContext(
        base='base',
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSourceProtocol, RandomSource()),
        max_candidates=4,
    )

    result = CandidateBatchAdapter(delegate).call(context)

    assert result.candidates == ()
    assert delegate.calls == 4
