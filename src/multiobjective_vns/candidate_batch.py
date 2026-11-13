"""Bounded adapter for operators that generate one candidate per sample.

Created: 2026-11-08T12:00:00+01:00
Author: Michel Bierlaire, EPFL
"""

from collections.abc import Callable
from dataclasses import replace

from multiobjective_vns.callbacks import Operator
from multiobjective_vns.records.candidates import (
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)


class CandidateBatchAdapter:
    """Collect distinct results from bounded independent Operator samples.

    The wrapped Operator receives the same base and requested neighborhood size
    for each sample. Samples share the caller's random source and Operator state.
    The adapter omits solutions equivalent to the base and retains unique
    application solutions in their first-sampled order, stopping after
    ``context.max_candidates`` samples or results. An application identity
    callback may define candidate distinctness.
    """

    __slots__ = ('_delegate', '_equivalent')

    def __init__(
        self,
        delegate: Operator,
        *,
        equivalent: Callable[[object, object], bool] | None = None,
    ) -> None:
        if not callable(getattr(delegate, 'call', None)):
            raise TypeError('delegate must provide call(context)')
        self._delegate = delegate
        if equivalent is not None and not callable(equivalent):
            raise TypeError('equivalent must be callable or None')
        self._equivalent = _same_solution if equivalent is None else equivalent

    @property
    def delegate(self) -> Operator:
        """Return the wrapped application Operator for catalog inspection."""
        return self._delegate

    def __getattr__(self, name: str) -> object:
        """Expose read-only descriptive attributes of the wrapped Operator."""
        return getattr(self._delegate, name)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Return the bounded ordered batch of distinct sampled candidates."""
        proposals: list[CandidateProposal] = []
        seen_solutions = [context.base]
        for _ in range(context.max_candidates):
            remaining = context.max_candidates - len(proposals)
            result = self._delegate.call(replace(context, max_candidates=remaining))
            if not isinstance(result, OperatorResult):
                raise TypeError('wrapped Operator must return OperatorResult')
            if len(result.candidates) > remaining:
                raise ValueError(
                    'wrapped Operator exceeded its CandidateContext.max_candidates'
                )
            for proposal in result.candidates:
                if any(
                    _equivalent_solutions(
                        self._equivalent,
                        proposal.candidate.solution,
                        prior_solution,
                    )
                    for prior_solution in seen_solutions
                ):
                    continue
                proposals.append(proposal)
                seen_solutions.append(proposal.candidate.solution)
                if len(proposals) == context.max_candidates:
                    break
            if len(proposals) == context.max_candidates:
                break
        return OperatorResult(candidates=tuple(proposals))


def _same_solution(left: object, right: object) -> bool:
    """Compare application values used by the built-in catalogs."""
    comparison = left == right
    if type(comparison) is not bool:
        raise TypeError('built-in solution equality must return bool')
    return comparison


def _equivalent_solutions(
    equivalent: Callable[[object, object], bool],
    left: object,
    right: object,
) -> bool:
    comparison = equivalent(left, right)
    if type(comparison) is not bool:
        raise TypeError('solution identity must return bool')
    return comparison


__all__ = ['CandidateBatchAdapter']
