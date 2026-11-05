"""Hos feasibility methods for multi-day VRP evaluation.

Created: 2026-11-03T18:47:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import isfinite

from multiobjective_vns.multiday_vrp.evaluation.helpers import (
    _positive_violation,
)
from multiobjective_vns.multiday_vrp.evaluation.rules import (
    RuleMeasure,
)
from multiobjective_vns.multiday_vrp.evaluation.runtime import (
    _EvaluatorRuntime,
)
from multiobjective_vns.multiday_vrp.extensions import (
    HosRuleFinding,
    HosScenarioRequest,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.solution import (
    VrpSolution,
)


class _HosFeasibilityMethods(_EvaluatorRuntime):
    """Provide hos feasibility methods checks."""

    __slots__ = ()

    def _hos_rule_measures(
        self, solution: VrpSolution, trace: VrpScenarioEvaluation
    ) -> tuple[RuleMeasure, ...]:

        settings = self._instance.settings

        if settings.hos_regime_id == 'none':
            return ()

        registration = next(
            (
                item
                for item in self._extension_registry.hos_regimes
                if item.extension_id == settings.hos_regime_id
                and item.version == settings.hos_regime_version
            ),
            None,
        )

        if registration is None:
            raise self._extension_error(
                'hos_regime',
                settings.hos_regime_id,
                'evaluate_scenario',
                None,
                trace.scenario_id,
                'no matching regime and version are registered',
            )

        if self._instance.hos_configuration is None:
            raise self._extension_error(
                'hos_regime',
                settings.hos_regime_id,
                'evaluate_scenario',
                None,
                trace.scenario_id,
                'the selected regime has no configuration',
            )

        request = HosScenarioRequest(
            instance=self._instance,
            solution=solution,
            scenario=trace,
            hos_configuration=self._instance.hos_configuration,
            application_data=self._instance.application_data,
        )

        raw_result = self._call_extension(
            registration,
            'hos_regime',
            'evaluate_scenario',
            None,
            trace.scenario_id,
            registration.evaluate_scenario,
            request,
        )

        if type(raw_result) is not tuple or any(
            type(item) is not HosRuleFinding for item in raw_result
        ):
            raise self._extension_error(
                'hos_regime',
                settings.hos_regime_id,
                'evaluate_scenario',
                None,
                trace.scenario_id,
                'result must be a tuple of HosRuleFinding records',
            )

        expected_pairs = tuple(
            (rule.rule_id, person_id)
            for rule in self._instance.hos_rule_definitions
            for person_id in rule.personnel_ids
        )

        values_by_pair: dict[tuple[str, str], float] = {}

        for finding in raw_result:
            pair = finding.rule_id, finding.personnel_id

            measure = finding.violation_measure

            if pair in values_by_pair:
                raise self._extension_error(
                    'hos_regime',
                    settings.hos_regime_id,
                    'evaluate_scenario',
                    None,
                    trace.scenario_id,
                    f'duplicate HOS finding for {pair!r}',
                )

            if type(measure) is not float or not isfinite(measure) or measure < 0.0:
                raise self._extension_error(
                    'hos_regime',
                    settings.hos_regime_id,
                    'evaluate_scenario',
                    None,
                    trace.scenario_id,
                    f'HOS finding {pair!r} must have a finite nonnegative float measure',
                )

            values_by_pair[pair] = _positive_violation(measure)

        if set(values_by_pair) != set(expected_pairs):
            raise self._extension_error(
                'hos_regime',
                settings.hos_regime_id,
                'evaluate_scenario',
                None,
                trace.scenario_id,
                'result must contain exactly one finding for each configured rule/person pair',
            )

        return tuple(
            RuleMeasure(
                'hos',
                'hos_rule',
                rule_id,
                f'{person_id}:{rule_id}',
                None,
                None,
                values_by_pair[(rule_id, person_id)],
            )
            for rule_id, person_id in expected_pairs
        )
