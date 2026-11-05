"""Shared extension dispatch and immutable instance context for VRP evaluation.

Created: 2026-11-03T20:26:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from multiobjective_vns.multiday_vrp.extensions import (
    BaseMeasureRegistration,
    ExtensionFamily,
    ExtensionMethod,
    HosRegimeRegistration,
    PenaltyAggregationRegistration,
    PenaltyFunctionRegistration,
    ScenarioAggregationRegistration,
    VrpExtensionEvaluationError,
    VrpExtensionRegistry,
)
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.scenario_generation.records import PostHorizonRule
from multiobjective_vns.multiday_vrp.solution import ActivityRef

__all__ = ['ExtensionRegistration', '_EvaluatorRuntime']

type ExtensionRegistration = (
    BaseMeasureRegistration
    | ScenarioAggregationRegistration
    | PenaltyFunctionRegistration
    | PenaltyAggregationRegistration
    | HosRegimeRegistration
)


class _EvaluatorRuntime:
    """Provide immutable evaluator inputs and shared extension dispatch."""

    __slots__ = (
        '_instance',
        '_extension_registry',
        '_post_horizon_rule',
        'configuration_id',
    )

    _instance: LoadedVrpInstance
    _extension_registry: VrpExtensionRegistry
    _post_horizon_rule: PostHorizonRule | None
    configuration_id: str

    def _activity_node(self, reference: ActivityRef) -> str:
        if reference.kind == 'pickup':
            return next(
                item.pickup_node_id
                for item in self._instance.package_data.packages
                if item.package_id == reference.activity_id
            )
        if reference.kind == 'delivery':
            return next(
                item.delivery_node_id
                for item in self._instance.package_data.packages
                if item.package_id == reference.activity_id
            )
        return next(
            item.node_id
            for item in self._instance.intervention_data.interventions
            if item.intervention_id == reference.activity_id
        )

    def _call_extension(
        self,
        registration: ExtensionRegistration,
        family: ExtensionFamily,
        method: ExtensionMethod,
        objective_id: str | None,
        scenario_id: str | None,
        callback: object,
        request: object,
        *,
        rule_id: str | None = None,
    ) -> object:
        try:
            typed_callback = cast(Callable[[object], object], callback)
            result = typed_callback(request)
        except Exception as error:
            raise self._extension_error(
                family,
                registration.extension_id,
                method,
                objective_id,
                scenario_id,
                str(error),
                rule_id=rule_id,
                cause=error,
            ) from error
        return result

    def _extension_error(
        self,
        family: ExtensionFamily,
        extension_id: str,
        method: ExtensionMethod,
        objective_id: str | None,
        scenario_id: str | None,
        reason: str,
        *,
        rule_id: str | None = None,
        cause: Exception | None = None,
    ) -> VrpExtensionEvaluationError:
        registration = self._registration(family, extension_id)
        component_id = 'multiobjective-vns:multiday-vrp:missing-extension'
        if registration is not None:
            component_id = registration.component_id
        error = VrpExtensionEvaluationError(
            family=family,
            extension_id=extension_id,
            component_id=component_id,
            method=method,
            objective_id=objective_id,
            rule_id=rule_id,
            scenario_id=scenario_id,
            reason=reason,
        )
        if cause is not None:
            error.__cause__ = cause
        return error

    def _registration(
        self, family: ExtensionFamily, extension_id: str
    ) -> ExtensionRegistration | None:
        collection = {
            'base_measure': self._extension_registry.base_measures,
            'scenario_aggregation': self._extension_registry.scenario_aggregations,
            'penalty_function': self._extension_registry.penalty_functions,
            'penalty_aggregation': self._extension_registry.penalty_aggregations,
            'hos_regime': self._extension_registry.hos_regimes,
        }[family]
        return _find_registration(
            cast(tuple[ExtensionRegistration, ...], collection), extension_id
        )


def _find_registration(
    registrations: tuple[ExtensionRegistration, ...], extension_id: str
) -> ExtensionRegistration | None:
    return next(
        (item for item in registrations if item.extension_id == extension_id),
        None,
    )
