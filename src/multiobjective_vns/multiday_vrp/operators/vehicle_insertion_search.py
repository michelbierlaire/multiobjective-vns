"""Bounded best and regret insertion search for vehicle assignment moves.

Created: 2026-11-05T09:50:25+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cmp_to_key
from itertools import islice
from math import inf
from typing import Generic, Literal, TypeVar, cast

from multiobjective_vns.multiday_vrp.operators.common import (
    choose_uniformly,
    randomized_sample,
)
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
    ObjectiveFunction,
)
from multiobjective_vns.records.candidates import CandidateContext

__all__ = []

_Plan = TypeVar('_Plan')
_Unit = TypeVar('_Unit')

type InsertionStrategy = Literal['best', 'regret']
type _ScoredPlan[_Plan] = tuple[_Plan, tuple[object, ...]]
type _ProposalGenerator[_Plan, _Unit] = Callable[
    [_Plan, _Unit, CandidateContext], Iterable[_Plan]
]
type _PlanAssessor[_Plan] = Callable[[_Plan], tuple[object, ...] | None]


@dataclass(frozen=True, slots=True)
class _BeamState(Generic[_Plan, _Unit]):
    plan: _Plan
    pending_units: tuple[_Unit, ...]
    objective_values: tuple[object, ...]


def build_insertion_beam(
    base: _Plan,
    units: tuple[_Unit, ...],
    propose: _ProposalGenerator[_Plan, _Unit],
    assess: _PlanAssessor[_Plan],
    objectives: ObjectiveDeclaration,
    *,
    objective_position: int | None,
    strategy: InsertionStrategy,
    context: CandidateContext,
    candidate_limit: int,
) -> tuple[_ScoredPlan[_Plan], ...]:
    """Build a bounded set of complete plans by best or regret insertion.

    :param base: Complete starting plan containing all units.
    :param units: Stable ordered identifiers for units that need reassignment.
    :param propose: Generate at most ``candidate_limit`` trial placements.
    :param assess: Return objective values for an eligible complete plan.
    :param objectives: Objective comparators used for ranking and regret.
    :param objective_position: Objective rank position, or ``None`` for Pareto.
    :param strategy: Best placement expansion or regret unit selection.
    :param context: Candidate context providing the shared random source.
    :param candidate_limit: Per-unit proposal limit and maximum beam width.
    :returns: Scored plans with every unit placed.
    """
    if not units:
        return ()
    if strategy not in {'best', 'regret'}:
        raise ValueError("strategy must be 'best' or 'regret'")
    if type(candidate_limit) is not int or candidate_limit < 1:
        raise ValueError('candidate_limit must be a positive integer')
    if objective_position is not None and not (
        0 <= objective_position < len(objectives.objectives)
    ):
        raise ValueError('objective_position must identify a declared objective')

    beam: tuple[_BeamState[_Plan, _Unit], ...] = (_BeamState(base, units, ()),)
    for _ in units:
        expanded: list[_BeamState[_Plan, _Unit]] = []
        for state in beam:
            alternatives = _assess_unit_proposals(
                state,
                propose,
                assess,
                context,
                candidate_limit,
            )
            if not alternatives:
                continue
            if strategy == 'best':
                selected = alternatives
            else:
                chosen = _choose_regret_unit(
                    alternatives,
                    objectives,
                    objective_position,
                    context,
                )
                selected = (chosen,)
            for unit, plans in selected:
                pending = tuple(item for item in state.pending_units if item != unit)
                expanded.extend(
                    _BeamState(plan, pending, values) for plan, values in plans
                )
        if not expanded:
            return ()
        beam = _select_beam(
            _unique_states(expanded),
            objectives,
            objective_position,
            context,
            candidate_limit,
        )

    return tuple(
        (state.plan, state.objective_values)
        for state in beam
        if not state.pending_units
    )


def _assess_unit_proposals(
    state: _BeamState[_Plan, _Unit],
    propose: _ProposalGenerator[_Plan, _Unit],
    assess: _PlanAssessor[_Plan],
    context: CandidateContext,
    limit: int,
) -> tuple[tuple[_Unit, tuple[_ScoredPlan[_Plan], ...]], ...]:
    result: list[tuple[_Unit, tuple[_ScoredPlan[_Plan], ...]]] = []
    for unit in state.pending_units:
        plans: list[_ScoredPlan[_Plan]] = []
        for plan in islice(propose(state.plan, unit, context), limit):
            if plan == state.plan or any(existing == plan for existing, _ in plans):
                continue
            values = assess(plan)
            if values is not None:
                plans.append((plan, values))
        if plans:
            result.append((unit, tuple(plans)))
    return tuple(result)


def _choose_regret_unit(
    alternatives: tuple[tuple[_Unit, tuple[_ScoredPlan[_Plan], ...]], ...],
    objectives: ObjectiveDeclaration,
    objective_position: int | None,
    context: CandidateContext,
) -> tuple[_Unit, tuple[_ScoredPlan[_Plan], ...]]:
    single_alternative = tuple(item for item in alternatives if len(item[1]) == 1)
    if single_alternative:
        return choose_uniformly(single_alternative, context.random_source)
    if objective_position is None:
        regrets = tuple(
            (
                item,
                _regret_vector(item[1], objectives),
            )
            for item in alternatives
        )
        maximal = tuple(
            item
            for item, regret in regrets
            if not any(
                _regret_dominates(other_regret, regret)
                for other, other_regret in regrets
                if other != item
            )
        )
        return choose_uniformly(maximal, context.random_source)

    objective = objectives.objectives[objective_position]
    scored = tuple(
        (
            item,
            _scalar_regret(item[1], objective_position, objective),
            _secondary_regret_vector(
                item[1], objectives, excluded_position=objective_position
            ),
        )
        for item in alternatives
    )
    maximum_target_regret = max(target_regret for _item, target_regret, _ in scored)
    target_regret_ties = tuple(
        entry for entry in scored if entry[1] == maximum_target_regret
    )
    maximal = tuple(
        entry[0]
        for entry in target_regret_ties
        if not any(
            _regret_dominates(other[2], entry[2])
            for other in target_regret_ties
            if other is not entry
        )
    )
    return choose_uniformly(maximal, context.random_source)


def _scalar_regret(
    plans: tuple[_ScoredPlan[_Plan], ...],
    position: int,
    objective: ObjectiveFunction,
) -> float:
    def compare_scored(left: _ScoredPlan[_Plan], right: _ScoredPlan[_Plan]) -> int:
        return _compare_values(objective, left[1][position], right[1][position])

    ordered: list[_ScoredPlan[_Plan]] = sorted(
        plans,
        key=cmp_to_key(compare_scored),
    )
    return _difference(ordered[0][1][position], ordered[1][1][position])


def _regret_vector(
    plans: tuple[_ScoredPlan[_Plan], ...],
    objectives: ObjectiveDeclaration,
) -> tuple[float, ...]:
    return tuple(
        _scalar_regret(plans, position, objective)
        for position, objective in enumerate(objectives.objectives)
    )


def _secondary_regret_vector(
    plans: tuple[_ScoredPlan[_Plan], ...],
    objectives: ObjectiveDeclaration,
    *,
    excluded_position: int,
) -> tuple[float, ...]:
    objective = objectives.objectives[excluded_position]
    best_value = plans[0][1][excluded_position]
    for plan, values in plans[1:]:
        if objective.compare(values[excluded_position], best_value) is (
            ObjectiveComparison.LESS
        ):
            best_value = values[excluded_position]
    target_best = tuple(
        scored
        for scored in plans
        if objective.compare(scored[1][excluded_position], best_value)
        is ObjectiveComparison.EQUAL
    )
    return tuple(
        0.0
        if len(target_best) < 2
        else _scalar_regret(target_best, position, other_objective)
        for position, other_objective in enumerate(objectives.objectives)
        if position != excluded_position
    )


def _difference(best: object, second: object) -> float:
    best_value = cast(float, best)
    second_value = cast(float, second)
    if best_value == inf and second_value == inf:
        return 0.0
    return max(0.0, second_value - best_value)


def _regret_dominates(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return all(a >= b for a, b in zip(left, right, strict=True)) and any(
        a > b for a, b in zip(left, right, strict=True)
    )


def _compare_values(
    objective: ObjectiveFunction,
    left: object,
    right: object,
) -> int:
    comparison = objective.compare(left, right)
    if comparison is ObjectiveComparison.LESS:
        return -1
    if comparison is ObjectiveComparison.GREATER:
        return 1
    return 0


def _unique_states(
    states: list[_BeamState[_Plan, _Unit]],
) -> tuple[_BeamState[_Plan, _Unit], ...]:
    unique: list[_BeamState[_Plan, _Unit]] = []
    for state in states:
        if not any(
            previous.plan == state.plan
            and previous.pending_units == state.pending_units
            for previous in unique
        ):
            unique.append(state)
    return tuple(unique)


def nondominated_scored_plans(
    candidates: Iterable[_ScoredPlan[_Plan]],
    objectives: ObjectiveDeclaration,
) -> tuple[_ScoredPlan[_Plan], ...]:
    """Return the Pareto-nondominated plans from an assessed candidate set.

    :param candidates: Plan and complete objective-vector pairs.
    :param objectives: Ordered minimization comparators.
    :returns: All nondominated pairs in their input order.
    """
    plans = tuple(candidates)
    return tuple(
        candidate
        for candidate in plans
        if not any(
            _objective_dominates(other[1], candidate[1], objectives)
            for other in plans
            if other is not candidate
        )
    )


def target_first_nondominated_scored_plans(
    candidates: Iterable[_ScoredPlan[_Plan]],
    objectives: ObjectiveDeclaration,
    *,
    objective_position: int,
) -> tuple[_ScoredPlan[_Plan], ...]:
    """Rank plans by a target objective, then Pareto-filter the remaining ones.

    :param candidates: Plan and complete objective-vector pairs.
    :param objectives: Ordered minimization comparators.
    :param objective_position: Target objective's zero-based position.
    :returns: Nondominated plans among those tied at the best target value.
    :raises ValueError: If the target position is outside the declaration.
    """
    plans = tuple(candidates)
    if not 0 <= objective_position < len(objectives.objectives):
        raise ValueError('objective_position must identify a declared objective')
    frontier_indices = _target_first_frontier_indices(
        tuple(values for _plan, values in plans),
        objectives,
        objective_position,
    )
    return tuple(plans[index] for index in frontier_indices)


def _select_beam(
    states: tuple[_BeamState[_Plan, _Unit], ...],
    objectives: ObjectiveDeclaration,
    objective_position: int | None,
    context: CandidateContext,
    width: int,
) -> tuple[_BeamState[_Plan, _Unit], ...]:
    if objective_position is not None:
        frontier_indices = _target_first_frontier_indices(
            tuple(state.objective_values for state in states),
            objectives,
            objective_position,
        )
        frontier = tuple(states[index] for index in frontier_indices)
        if len(frontier) <= width:
            return frontier
        return randomized_sample(frontier, width, context.random_source)

    nondominated = tuple(
        state
        for state in states
        if not any(
            _objective_dominates(
                other.objective_values, state.objective_values, objectives
            )
            for other in states
            if other is not state
        )
    )
    if len(nondominated) <= width:
        return nondominated
    return randomized_sample(nondominated, width, context.random_source)


def _objective_dominates(
    left: tuple[object, ...],
    right: tuple[object, ...],
    objectives: ObjectiveDeclaration,
    *,
    excluded_position: int | None = None,
) -> bool:
    comparisons = tuple(
        objective.compare(left_value, right_value)
        for position, (objective, left_value, right_value) in enumerate(
            zip(
                objectives.objectives,
                left,
                right,
                strict=True,
            )
        )
        if position != excluded_position
    )
    if not comparisons:
        return False
    return all(item is not ObjectiveComparison.GREATER for item in comparisons) and any(
        item is ObjectiveComparison.LESS for item in comparisons
    )


def _target_first_frontier_indices(
    objective_vectors: tuple[tuple[object, ...], ...],
    objectives: ObjectiveDeclaration,
    objective_position: int,
) -> tuple[int, ...]:
    if not objective_vectors:
        return ()
    target = objectives.objectives[objective_position]
    best_value = objective_vectors[0][objective_position]
    for values in objective_vectors[1:]:
        if target.compare(values[objective_position], best_value) is (
            ObjectiveComparison.LESS
        ):
            best_value = values[objective_position]
    target_best_indices = tuple(
        index
        for index, values in enumerate(objective_vectors)
        if target.compare(values[objective_position], best_value)
        is ObjectiveComparison.EQUAL
    )
    return tuple(
        index
        for index in target_best_indices
        if not any(
            _objective_dominates(
                objective_vectors[other_index],
                objective_vectors[index],
                objectives,
                excluded_position=objective_position,
            )
            for other_index in target_best_indices
            if other_index != index
        )
    )
