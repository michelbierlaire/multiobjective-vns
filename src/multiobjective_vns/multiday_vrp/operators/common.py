"""Shared random, size, result, and feasibility helpers for VRP Operators.

Created: 2026-11-03T18:58:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite
from typing import Protocol, TypeVar

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = [
    'VrpPlanAssessor',
    'candidate_is_eligible',
    'choose_uniformly',
    'operator_result',
    'randomized_order',
    'randomized_sample',
    'require_vrp_base',
    'validate_neighborhood_size',
]

_Item = TypeVar('_Item')


class VrpPlanAssessor(Protocol):
    """Callable contract for assessing a complete VRP plan.

    The assessment applies the loaded instance's representation, feasibility,
    and objective rules. Operators use the same callback when screening move
    alternatives internally.
    """

    def __call__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        """Assess one complete plan under its fixed instance and objectives."""


def randomized_sample(
    values: Sequence[_Item], count: int, random_source: RandomSource
) -> tuple[_Item, ...]:
    """Sample up to ``count`` sequence positions uniformly without replacement.

    The input sequence supplies the stable baseline order. Callers sort
    identifier-based targets before sampling, as required by the Operator
    contract. Invalid random draws raise ``TypeError`` or ``ValueError`` at
    this boundary.

    :param values: Ordered alternatives to sample.
    :param count: Maximum number of alternatives to return; zero is allowed.
    :param random_source: The search's shared random stream.
    :returns: Sampled alternatives in draw order.
    :raises TypeError: If ``count`` or a random draw has an invalid type.
    :raises ValueError: If ``count`` is negative or a random draw is outside
        ``[0, 1)`` or non-finite.
    """
    sample_count = require_integer(count, name='count', minimum=0)
    pool = list(values)
    selected_count = min(sample_count, len(pool))
    for position in range(selected_count):
        remaining = len(pool) - position
        if remaining == 1:
            break
        draw = random_source.random()
        if type(draw) is not float:
            raise TypeError('RandomSource.random() must return a Python float')
        if not isfinite(draw) or not 0.0 <= draw < 1.0:
            raise ValueError('RandomSource.random() must return a float in [0, 1)')
        offset = min(int(draw * remaining), remaining - 1)
        selected_position = position + offset
        pool[position], pool[selected_position] = (
            pool[selected_position],
            pool[position],
        )
    return tuple(pool[:selected_count])


def randomized_order(
    values: Sequence[_Item], random_source: RandomSource
) -> tuple[_Item, ...]:
    """Return all sequence positions in a uniformly randomized order."""
    return randomized_sample(values, len(values), random_source)


def require_vrp_base(context: CandidateContext) -> VrpSolution:
    """Return an Operator base after checking its VRP application type.

    :param context: Inputs supplied to one Operator call.
    :returns: The base as an immutable ``VrpSolution``.
    :raises TypeError: If the base value is not a ``VrpSolution``.
    """
    if not isinstance(context.base, VrpSolution):
        raise TypeError('VRP Operator base must be a VrpSolution')
    return context.base


def choose_uniformly(values: Sequence[_Item], random_source: RandomSource) -> _Item:
    """Choose one alternative uniformly from a nonempty ordered sequence.

    :raises ValueError: If ``values`` is empty.
    """
    if not values:
        raise ValueError('uniform choice requires a nonempty sequence')
    return randomized_sample(values, 1, random_source)[0]


def validate_neighborhood_size(k: int, maximum: int) -> int:
    """Validate a requested size against an Operator's registered maximum.

    :param k: Positive requested neighborhood size.
    :param maximum: Positive maximum supported by the registration.
    :returns: The validated requested size.
    :raises TypeError: If either value is not a built-in integer excluding bool.
    :raises ValueError: If either value is nonpositive or ``k`` exceeds maximum.
    """
    requested = require_integer(k, name='k', minimum=1)
    limit = require_integer(maximum, name='maximum neighborhood size', minimum=1)
    if requested > limit:
        raise ValueError('k exceeds the Operator maximum neighborhood size')
    return requested


def operator_result(
    solution: VrpSolution | None,
    *,
    modifications: int,
    requested_size: int,
    metadata: object | None = None,
) -> OperatorResult:
    """Build an Operator result with a candidate exactly when ``m`` is positive.

    :param solution: Complete candidate plan, or ``None`` when no move was made.
    :param modifications: Operator-defined effective move count ``m``.
    :param requested_size: Requested neighborhood size ``k``.
    :param metadata: Optional application metadata for the candidate.
    :returns: A validated generic ``OperatorResult``.
    :raises TypeError: If integer fields or a non-null solution have invalid types.
    :raises ValueError: If ``m`` falls outside ``0..k`` or disagrees with solution.
    """
    count = require_integer(modifications, name='m', minimum=0)
    size = require_integer(requested_size, name='k', minimum=1)
    if count > size:
        raise ValueError('m cannot exceed requested k')
    if count == 0:
        if solution is not None:
            raise ValueError('a zero modification count requires no candidate')
        return OperatorResult(candidates=())
    if solution is None:
        raise ValueError('a positive modification count requires a candidate')
    if not isinstance(solution, VrpSolution):
        raise TypeError('VRP Operator candidates must be VrpSolution values')
    return OperatorResult(
        candidates=(
            CandidateProposal(Candidate(solution=solution, metadata=metadata), count),
        )
    )


def candidate_is_eligible(
    instance: LoadedVrpInstance,
    objective_declaration: ObjectiveDeclaration,
    solution: VrpSolution,
    *,
    ensure_feasible: bool,
    assess_plan: VrpPlanAssessor,
) -> bool:
    """Check representation validity and the requested feasibility guarantee.

    Each complete move trial is assessed by the same application callback used
    by search. A representation-valid infeasible plan remains eligible when
    ``ensure_feasible`` is false. Callback exceptions propagate to the generic
    Operator failure boundary.

    :param instance: Validated VRP instance captured by the Operator.
    :param objective_declaration: Ordered objectives used for plan assessment.
    :param solution: Complete alternative plan being screened.
    :param ensure_feasible: Whether the alternative must satisfy every hard
        requirement in every scenario.
    :param assess_plan: Shared pure plan-assessment callback.
    :returns: Whether the assessed plan meets the request.
    :raises TypeError: If the feasibility flag or callback result is malformed.
    :raises ValueError: If a representation-valid assessment has no objective
        vector or its vector length differs from the declared objectives.
    """
    if type(ensure_feasible) is not bool:
        raise TypeError('ensure_feasible must be bool')
    assessment = assess_plan(instance, objective_declaration, solution)
    if not isinstance(assessment, ApplicationEvaluation):
        raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
    if not assessment.representation_valid:
        return False
    objective_values = assessment.objective_values
    if objective_values is None:
        raise ValueError('valid VRP assessment requires an objective vector')
    if not isinstance(objective_values, tuple):
        raise TypeError('valid VRP assessment objective_values must be a tuple')
    if len(objective_values) != len(objective_declaration.objectives):
        raise ValueError(
            'valid VRP assessment objective vector length must match the declaration'
        )
    return not ensure_feasible or assessment.feasible is True
