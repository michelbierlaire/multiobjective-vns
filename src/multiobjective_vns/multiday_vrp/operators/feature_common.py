"""Shared assessment and objective-order helpers for feature Operators.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation

__all__ = [
    'assess_complete_candidate',
    'choose_best_candidate',
    'objective_position',
]

_Candidate = TypeVar('_Candidate')


def objective_position(objectives: ObjectiveDeclaration, objective_id: object) -> int:
    """Return the declared position of an objective identifier.

    :param objectives: Ordered objective functions.
    :param objective_id: Identifier to locate.
    :returns: Zero-based objective-vector position.
    :raises ValueError: If the identifier is not declared.
    """
    for position, objective in enumerate(objectives.objectives):
        if objective.objective_id == objective_id:
            return position
    raise ValueError(f'objective {objective_id!r} is not declared')


def assess_complete_candidate(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    solution: VrpSolution,
    assess_plan: VrpPlanAssessor,
    *,
    ensure_feasible: bool,
) -> ApplicationEvaluation | None:
    """Assess a complete candidate and return findings when it is eligible.

    :param instance: Validated application instance.
    :param objectives: Objective declaration for this search.
    :param solution: Complete VRP candidate.
    :param assess_plan: Shared application evaluation callback.
    :param ensure_feasible: Whether all hard constraints must hold.
    :returns: Complete findings, or ``None`` when the candidate is invalid or
        fails the requested feasibility guarantee.
    :raises TypeError: If the callback returns a malformed record.
    :raises ValueError: If findings omit the required objective vector.
    """
    evaluation = assess_plan(instance, objectives, solution)
    if not isinstance(evaluation, ApplicationEvaluation):
        raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
    if not evaluation.representation_valid:
        return None
    if evaluation.objective_values is None:
        raise ValueError('valid VRP assessment requires an objective vector')
    if type(evaluation.objective_values) is not tuple:
        raise TypeError('valid VRP assessment objective_values must be a tuple')
    if len(evaluation.objective_values) != len(objectives.objectives):
        raise ValueError('objective vector length must match the declaration')
    if ensure_feasible and evaluation.feasible is not True:
        return None
    return evaluation


def choose_best_candidate(
    candidates: Sequence[tuple[_Candidate, tuple[object, ...]]],
    objectives: ObjectiveDeclaration,
    position: int,
) -> tuple[_Candidate, tuple[object, ...]]:
    """Return the candidate with the lowest value at one objective position.

    Tied values preserve input order. Operators that require random tie
    breaking can choose uniformly from a separate candidate list after
    comparing with this helper's objective contract.

    :param candidates: Candidate/value-vector pairs.
    :param objectives: Declared objective functions.
    :param position: Zero-based objective position.
    :returns: The best candidate and its objective vector.
    :raises ValueError: If no candidates are supplied or position is invalid.
    """
    if not candidates:
        raise ValueError('at least one candidate is required')
    if type(position) is not int or not 0 <= position < len(objectives.objectives):
        raise ValueError('position must identify a declared objective')
    objective = objectives.objectives[position]
    best = candidates[0]
    for candidate in candidates[1:]:
        if len(candidate[1]) != len(objectives.objectives):
            raise ValueError('candidate objective vector length is inconsistent')
        if (
            objective.compare(candidate[1][position], best[1][position])
            is ObjectiveComparison.LESS
        ):
            best = candidate
    if len(best[1]) != len(objectives.objectives):
        raise ValueError('candidate objective vector length is inconsistent')
    return best
