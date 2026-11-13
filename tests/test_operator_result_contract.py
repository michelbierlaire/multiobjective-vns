"""Tests for ordered multi-candidate Operator results."""

import pytest

from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateProposal,
    OperatorResult,
)


def test_operator_result_preserves_ordered_proposals() -> None:
    first = CandidateProposal(Candidate(('first',)), 1)
    second = CandidateProposal(Candidate(('second',)), 2)

    result = OperatorResult(candidates=(first, second))

    assert result.candidates == (first, second)
    assert not hasattr(result, 'candidate')
    assert not hasattr(result, 'm')


def test_empty_operator_result_represents_no_candidate() -> None:
    assert OperatorResult(candidates=()).candidates == ()


def test_operator_result_rejects_non_proposals() -> None:
    with pytest.raises(TypeError, match='CandidateProposal'):
        OperatorResult(candidates=(Candidate(('invalid',)),))  # type: ignore[arg-type]


def test_candidate_proposal_requires_positive_modification_count() -> None:
    with pytest.raises(ValueError, match='m'):
        CandidateProposal(Candidate(('candidate',)), 0)


def test_operator_result_preserves_repeated_proposal_occurrences() -> None:
    proposal = CandidateProposal(Candidate(('same',)), 1)
    assert OperatorResult(candidates=(proposal, proposal)).candidates == (
        proposal,
        proposal,
    )


def test_operator_result_requires_the_candidate_list_contract() -> None:
    with pytest.raises(TypeError):
        OperatorResult(1, Candidate(('legacy',)))  # type: ignore[arg-type, call-arg]
    with pytest.raises(TypeError):
        OperatorResult(m=1, candidate=Candidate(('legacy',)))  # type: ignore[call-arg]
