"""Joint representation, capacity, and objective assessment for knapsack vectors.

Created: 2026-10-29T17:44:53+00:00
Author: Michel Bierlaire, EPFL
"""

from math import fsum, isfinite
from typing import cast

from multiobjective_vns.callbacks import Solution
from multiobjective_vns.identifiers import ComponentConfigurationId, ComponentId
from multiobjective_vns.knapsack.instance import KnapsackInstance
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.evaluation import ApplicationEvaluation


def knapsack_is_feasible(instance: KnapsackInstance, solution: tuple[int, ...]) -> bool:
    """Return whether an integer vector satisfies every capacity constraint.

    :param instance: Validated knapsack problem data.
    :param solution: Representation-valid binary decision tuple.
    :returns: ``True`` when all resource use is within capacity.
    """
    for resource in instance.resources:
        try:
            used = fsum(
                amount * selected
                for amount, selected in zip(resource.consumption, solution, strict=True)
            )
        except OverflowError:
            return False
        if not isfinite(used) or used > resource.capacity:
            return False
    return True


class KnapsackApplicationEvaluator:
    """Evaluate binary item tuples against one immutable problem instance.

    :param instance: Problem data used for all evaluation calls.
    """

    component_id: ComponentId = 'multiobjective-vns:knapsack:evaluator'
    configuration_id: ComponentConfigurationId = 'knapsack-evaluator-v1'

    __slots__ = ('_instance', '_objective_ids')

    def __init__(self, instance: KnapsackInstance) -> None:
        self._instance = instance
        self._objective_ids = tuple(
            objective.objective_id for objective in instance.objectives
        )

    def evaluate(
        self, solution: Solution, objectives: ObjectiveDeclaration
    ) -> ApplicationEvaluation:
        """Assess vector representation, capacity feasibility, and objectives.

        :param solution: Immutable item-decision tuple.
        :param objectives: Ordered objective contract supplied by the engine.
        :returns: Separate representation, feasibility, and objective findings.
        :raises ValueError: If the supplied objective order differs from the instance.
        """
        if (
            tuple(objective.objective_id for objective in objectives.objectives)
            != self._objective_ids
        ):
            raise ValueError('ObjectiveDeclaration objective order must match instance')

        if not self._is_valid_solution(solution):
            return ApplicationEvaluation(False, None, None)

        values = cast(tuple[int, ...], solution)
        feasible = knapsack_is_feasible(self._instance, values)
        objective_values = self._objective_values(values)
        return ApplicationEvaluation(True, feasible, objective_values)

    def _is_valid_solution(self, solution: object) -> bool:
        return (
            isinstance(solution, tuple)
            and len(solution) == self._instance.n_items
            and all(type(value) is int and value in (0, 1) for value in solution)
        )

    def _objective_values(self, solution: tuple[int, ...]) -> tuple[float, ...] | None:
        values: list[float] = []
        for objective in self._instance.objectives:
            try:
                total = fsum(
                    contribution * selected
                    for contribution, selected in zip(
                        objective.contribution, solution, strict=True
                    )
                )
            except OverflowError:
                return None
            if not isfinite(total):
                return None
            values.append(-total)
        return tuple(values)
