"""Tests for bounded best and regret insertion construction.

Created: 2026-11-05T09:42:54+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.objectives import VrpObjectiveFunction
from multiobjective_vns.multiday_vrp.operators.vehicle_insertion_search import (
    build_insertion_beam,
    nondominated_scored_plans,
    target_first_nondominated_scored_plans,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext


class _ZeroRandomSource:
    def __init__(self) -> None:
        self._draws: Iterator[float] = iter((0.0,) * 1000)

    def random(self) -> float:
        return next(self._draws)


def _objectives(count: int = 1) -> ObjectiveDeclaration:
    return ObjectiveDeclaration(
        tuple(
            VrpObjectiveFunction(
                f'objective-{index}',
                f'component-{index}',
                f'configuration-{index}',
            )
            for index in range(count)
        )
    )


def _context() -> CandidateContext:
    return CandidateContext(
        base=(),
        k=1,
        ensure_feasible=False,
        operator_state=None,
        random_source=cast(RandomSource, _ZeroRandomSource()),
    )


def test_best_and_regret_insertion_choose_different_unit_orders() -> None:
    objectives = _objectives()
    base: tuple[str, ...] = ()
    values: dict[tuple[str, ...], tuple[float, ...]] = {
        ('a-best',): (0.0,),
        ('a-second',): (1.0,),
        ('b-best',): (2.0,),
        ('b-second',): (100.0,),
        ('a-best', 'finish-b'): (20.0,),
        ('a-second', 'finish-b'): (30.0,),
        ('b-best', 'finish-a'): (10.0,),
        ('b-second', 'finish-a'): (15.0,),
    }

    def proposals(
        plan: tuple[str, ...], unit: str, _context: CandidateContext
    ) -> tuple[tuple[str, ...], ...]:
        if not plan:
            if unit == 'a':
                return (('a-best',), ('a-second',))
            return (('b-best',), ('b-second',))
        return (plan + (f'finish-{unit}',),)

    def assess(plan: tuple[str, ...]) -> tuple[object, ...] | None:
        return values.get(plan)

    best = build_insertion_beam(
        base,
        ('a', 'b'),
        proposals,
        assess,
        objectives,
        objective_position=0,
        strategy='best',
        context=_context(),
        candidate_limit=2,
    )
    regret = build_insertion_beam(
        base,
        ('a', 'b'),
        proposals,
        assess,
        objectives,
        objective_position=0,
        strategy='regret',
        context=_context(),
        candidate_limit=2,
    )

    assert best
    assert regret
    assert {plan[0] for plan, _scores in best} == {'a-best'}
    assert {plan[0] for plan, _scores in regret} == {'b-best'}


def test_unranked_regret_uses_pareto_maximal_regret_vectors() -> None:
    objectives = _objectives(count=2)
    base: tuple[str, ...] = ()
    initial_values: dict[tuple[str, ...], tuple[object, ...]] = {
        ('a-best',): (0.0, 0.0),
        ('a-second',): (1.0, 10.0),
        ('b-best',): (0.0, 0.0),
        ('b-second',): (10.0, 1.0),
        ('c-best',): (0.0, 0.0),
        ('c-second',): (0.5, 0.5),
    }

    def proposals(
        plan: tuple[str, ...], unit: str, _context: CandidateContext
    ) -> tuple[tuple[str, ...], ...]:
        if not plan:
            return ((f'{unit}-best',), (f'{unit}-second',))
        return (plan + (f'finish-{unit}',),)

    def assess(plan: tuple[str, ...]) -> tuple[object, ...] | None:
        return initial_values.get(plan, (100.0, 100.0))

    result = build_insertion_beam(
        base,
        ('a', 'b', 'c'),
        proposals,
        assess,
        objectives,
        objective_position=None,
        strategy='regret',
        context=_context(),
        candidate_limit=2,
    )

    assert result
    assert {plan[0][0] for plan, _scores in result} <= {'a', 'b'}


def test_targeted_best_insertion_uses_other_objectives_for_target_ties() -> None:
    objectives = _objectives(count=2)
    base: tuple[str, ...] = ()
    values: dict[tuple[str, ...], tuple[float, float]] = {
        ('target-best-long',): (0.0, 100.0),
        ('target-worse-short',): (1.0, 0.0),
        ('target-best-short',): (0.0, 50.0),
    }

    def proposals(
        _plan: tuple[str, ...], _unit: str, _context: CandidateContext
    ) -> tuple[tuple[str, ...], ...]:
        return tuple(
            (name,)
            for name in (
                'target-best-long',
                'target-worse-short',
                'target-best-short',
            )
        )

    result = build_insertion_beam(
        base,
        ('unit',),
        proposals,
        lambda plan: values[plan],
        objectives,
        objective_position=0,
        strategy='best',
        context=_context(),
        candidate_limit=3,
    )

    assert len(result) == 1
    assert result[0][0] == ('target-best-short',)
    assert result[0][1] == (0.0, 50.0)


def test_targeted_regret_uses_secondary_regret_among_target_best_placements() -> None:
    objectives = _objectives(count=2)
    base: tuple[str, ...] = ()
    values = {
        ('a-best-short',): (0.0, 0.0),
        ('a-best-long',): (0.0, 100.0),
        ('a-worse-target',): (1.0, 1.0),
        ('b-best-short',): (0.0, 0.0),
        ('b-best-long',): (0.0, 50.0),
        ('b-worse-target',): (1.0, 1000.0),
        ('a-best-short', 'finish-b'): (0.0, 0.0),
        ('b-best-short', 'finish-a'): (0.0, 0.0),
    }

    def proposals(
        plan: tuple[str, ...], unit: str, _context: CandidateContext
    ) -> tuple[tuple[str, ...], ...]:
        if not plan:
            return tuple(
                (f'{unit}-{name}',)
                for name in ('best-short', 'best-long', 'worse-target')
            )
        return (plan + (f'finish-{unit}',),)

    result = build_insertion_beam(
        base,
        ('b', 'a'),
        proposals,
        lambda plan: values.get(plan),
        objectives,
        objective_position=0,
        strategy='regret',
        context=_context(),
        candidate_limit=3,
    )

    assert result
    assert all(plan[0] == 'a-best-short' for plan, _scores in result)


def test_target_first_filter_preserves_primary_priority_and_secondary_front() -> None:
    selected = target_first_nondominated_scored_plans(
        (
            ('target-best-dominated', (0.0, 100.0)),
            ('target-worse', (1.0, 0.0)),
            ('target-best-frontier', (0.0, 50.0)),
        ),
        _objectives(count=2),
        objective_position=0,
    )

    assert selected == (('target-best-frontier', (0.0, 50.0)),)


def test_nondominated_plan_filter_keeps_incomparable_plans() -> None:
    selected = nondominated_scored_plans(
        (
            ('first', (0.0, 10.0)),
            ('second', (10.0, 0.0)),
            ('dominated', (1.0, 11.0)),
        ),
        _objectives(count=2),
    )

    assert {plan for plan, _scores in selected} == {'first', 'second'}
