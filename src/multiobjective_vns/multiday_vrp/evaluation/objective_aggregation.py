"""Objective aggregation methods for multi-day VRP evaluation.

Created: 2026-11-03T18:47:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import fsum
from typing import cast

from multiobjective_vns.multiday_vrp.evaluation.aggregation import (
    aggregate_penalties,
    aggregate_scenarios,
    validate_finite_result,
)
from multiobjective_vns.multiday_vrp.evaluation.runtime import (
    _EvaluatorRuntime,
)
from multiobjective_vns.multiday_vrp.extensions import (
    PenaltyAggregationRegistration,
    PenaltyAggregationRequest,
    PenaltyCategoryInput,
    PenaltyFunctionRegistration,
    PenaltyFunctionRequest,
    ScenarioAggregationRegistration,
    ScenarioAggregationRequest,
    ScenarioObjectiveInput,
    ViolationCategory,
)
from multiobjective_vns.multiday_vrp.input_records import (
    ObjectiveSpec,
    PenaltyFunctionSpec,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    VrpSolution,
)


class _ObjectiveAggregationMethods(_EvaluatorRuntime):
    """Provide objective aggregation methods."""

    __slots__ = ()

    def _penalty_value(
        self,
        objective: ObjectiveSpec,
        category: ViolationCategory,
        rule_id: str | None,
        measure: float,
        scenario_id: str,
    ) -> float:

        binding = self._penalty_binding(objective.objective_id, category, rule_id)

        if binding.function_id == 'linear':
            rate = binding.parameters['rate']

            result = cast(float, rate) * measure

            return validate_finite_result(result, 'linear penalty result')

        registration = self._registration('penalty_function', binding.function_id)

        if registration is None:
            raise self._extension_error(
                'penalty_function',
                binding.function_id,
                'evaluate',
                objective.objective_id,
                scenario_id,
                'no callback is registered',
                rule_id=rule_id,
            )

        assert isinstance(registration, PenaltyFunctionRegistration)

        request = PenaltyFunctionRequest(
            objective_id=objective.objective_id,
            violation_category=category,
            rule_id=rule_id,
            violation_measure=measure,
            parameters=binding.parameters,
            application_data=self._instance.application_data,
        )

        value = self._call_extension(
            registration,
            'penalty_function',
            'evaluate',
            objective.objective_id,
            scenario_id,
            registration.evaluate,
            request,
            rule_id=rule_id,
        )

        result = validate_finite_result(value, 'penalty function result')

        if (measure == 0.0 and result != 0.0) or (measure > 0.0 and result <= 0.0):
            raise self._extension_error(
                'penalty_function',
                binding.function_id,
                'evaluate',
                objective.objective_id,
                scenario_id,
                'penalty output must be zero exactly for zero violation and positive otherwise',
                rule_id=rule_id,
            )

        return result

    def _penalty_binding(
        self, objective_id: str, category: str, rule_id: str | None
    ) -> PenaltyFunctionSpec:

        bindings = self._instance.objective_data.penalty_functions

        if rule_id is not None:
            specific = next(
                (
                    item
                    for item in bindings
                    if item.objective_id == objective_id
                    and item.violation_category == category
                    and item.rule_id == rule_id
                ),
                None,
            )

            if specific is not None:
                return specific

        return next(
            item
            for item in bindings
            if item.objective_id == objective_id
            and item.violation_category == category
            and item.rule_id == ''
        )

    def _penalty_aggregate(
        self,
        objective: ObjectiveSpec,
        categories: tuple[PenaltyCategoryInput, ...],
        scenario_id: str,
    ) -> float:

        specification = objective.penalty_aggregation

        if specification.id == 'sum':
            return aggregate_penalties(categories)

        registration = self._registration('penalty_aggregation', specification.id)

        if registration is None:
            raise self._extension_error(
                'penalty_aggregation',
                specification.id,
                'aggregate',
                objective.objective_id,
                scenario_id,
                'no callback is registered',
            )

        assert isinstance(registration, PenaltyAggregationRegistration)

        request = PenaltyAggregationRequest(
            objective_id=objective.objective_id,
            category_values=categories,
            parameters=specification.parameters,
            application_data=self._instance.application_data,
        )

        value = self._call_extension(
            registration,
            'penalty_aggregation',
            'aggregate',
            objective.objective_id,
            scenario_id,
            registration.aggregate,
            request,
        )

        result = validate_finite_result(value, 'penalty aggregation result')

        if result < 0.0:
            raise self._extension_error(
                'penalty_aggregation',
                specification.id,
                'aggregate',
                objective.objective_id,
                scenario_id,
                'penalty aggregation must return a nonnegative value',
            )

        if (
            not any(
                any(item != 0.0 for item in category.values) for category in categories
            )
            and result != 0.0
        ):
            raise self._extension_error(
                'penalty_aggregation',
                specification.id,
                'aggregate',
                objective.objective_id,
                scenario_id,
                'penalty aggregation must return zero when every input is zero',
            )

        return result

    def _scenario_aggregate(
        self,
        objective: ObjectiveSpec,
        values: tuple[ScenarioObjectiveInput, ...],
    ) -> float:

        specification = objective.aggregation

        if specification.id in {
            'weighted_mean',
            'weighted_quantile',
            'weighted_standard_deviation',
            'maximum',
        }:
            return aggregate_scenarios(specification, values)

        registration = self._registration('scenario_aggregation', specification.id)

        if registration is None:
            raise self._extension_error(
                'scenario_aggregation',
                specification.id,
                'aggregate',
                objective.objective_id,
                None,
                'no callback is registered',
            )

        assert isinstance(registration, ScenarioAggregationRegistration)

        request = ScenarioAggregationRequest(
            objective_id=objective.objective_id,
            scenario_values=values,
            parameters=specification.parameters,
            application_data=self._instance.application_data,
        )

        value = self._call_extension(
            registration,
            'scenario_aggregation',
            'aggregate',
            objective.objective_id,
            None,
            registration.aggregate,
            request,
        )

        return validate_finite_result(value, 'scenario aggregation result')

    def _omission_cost(self, objective_id: str, solution: VrpSolution) -> float:

        if self._instance.settings.service_coverage_mode != 'penalized_omission':
            return 0.0

        occurrences = {
            (item.kind, item.activity_id)
            for route in solution.routes
            for stop in route.stops
            if type(stop) is ActivityStop
            for item in (stop.activity_ref,)
        }

        return fsum(
            item.amount
            for item in self._instance.objective_data.objective_omission_penalties
            if item.objective_id == objective_id
            and (item.activity_kind, item.activity_id) not in occurrences
        )
