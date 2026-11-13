"""Public coordinator for VRP representation, feasibility, and objective evaluation.

Created: 2026-11-03T18:40:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Mapping
from math import fsum, inf

from multiobjective_vns.identifiers import ComponentId
from multiobjective_vns.multiday_vrp.evaluation.aggregation import (
    validate_finite_result,
)
from multiobjective_vns.multiday_vrp.evaluation.hos import _HosFeasibilityMethods
from multiobjective_vns.multiday_vrp.evaluation.objective_measures import (
    _ObjectiveMeasureMethods,
)
from multiobjective_vns.multiday_vrp.evaluation.plan_feasibility import (
    _PlanFeasibilityMethods,
)
from multiobjective_vns.multiday_vrp.evaluation.records import (
    HardFeasibilityFinding,
    ResourceFinding,
    ScenarioObjectiveCost,
    VrpOperatorAnalysis,
)
from multiobjective_vns.multiday_vrp.evaluation.rules import (
    RuleMeasure,
    assess_simple_rules,
)
from multiobjective_vns.multiday_vrp.evaluation.scenario_feasibility import (
    _ScenarioFeasibilityMethods,
)
from multiobjective_vns.multiday_vrp.evaluation.simulation import simulate_scenario
from multiobjective_vns.multiday_vrp.evaluation.validation import representation_error
from multiobjective_vns.multiday_vrp.evaluation.window_penalties import (
    _WindowPenaltyMethods,
)
from multiobjective_vns.multiday_vrp.extensions import (
    PenaltyCategoryInput,
    ScenarioObjectiveInput,
    ViolationCategory,
    VrpExtensionRegistry,
    VrpScenarioEvaluation,
    WindowScenarioAssessment,
)
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.scenario_generation.records import PostHorizonRule
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.evaluation import ApplicationEvaluation

__all__ = ['VrpApplicationEvaluator']


class VrpApplicationEvaluator(
    _PlanFeasibilityMethods,
    _ScenarioFeasibilityMethods,
    _HosFeasibilityMethods,
    _WindowPenaltyMethods,
    _ObjectiveMeasureMethods,
):
    """Evaluate a ``VrpSolution`` against one immutable VRP instance.

    :param instance: Validated instance data and fixed ScenarioBank.
    :param extension_registry: The registry used to load custom declarations.
    :param post_horizon_rules: Custom ScenarioBank travel extensions, indexed
        by ``(rule_id, configuration_id)``.
    """

    __slots__ = ()

    component_id: ComponentId = 'multiobjective-vns:multiday-vrp:evaluator'

    def __init__(
        self,
        instance: LoadedVrpInstance,
        *,
        extension_registry: VrpExtensionRegistry | None = None,
        post_horizon_rules: Mapping[tuple[str, str], PostHorizonRule] | None = None,
    ) -> None:
        from multiobjective_vns.multiday_vrp.extensions import (
            empty_vrp_extension_registry,
        )

        registry = extension_registry or empty_vrp_extension_registry()
        rules = {} if post_horizon_rules is None else dict(post_horizon_rules)
        selected_key = (
            instance.scenario_bank.post_horizon_rule_id,
            instance.scenario_bank.post_horizon_rule_configuration_id,
        )
        if selected_key == ('constant_last_boundary', 'builtin-v1'):
            selected_rule = None
        else:
            selected_rule = rules.get(selected_key)
            if selected_rule is None:
                raise ValueError(
                    f'no post-horizon rule registered for {selected_key!r}'
                )
            if (selected_rule.rule_id, selected_rule.configuration_id) != selected_key:
                raise ValueError(
                    'post-horizon rule identity does not match ScenarioBank'
                )
        self._instance = instance
        self._extension_registry = registry
        self._post_horizon_rule = selected_rule
        self.configuration_id = (
            f'multiday-vrp-evaluator-v1:{instance.instance_id}:'
            f'{registry.registry_id}:{registry.configuration_id}:'
            f'{selected_key[0]}:{selected_key[1]}'
        )

    def evaluate(
        self, solution: object, objectives: ObjectiveDeclaration
    ) -> ApplicationEvaluation:
        """Assess representation, hard feasibility, penalties, and objectives.

        :param solution: Application candidate supplied by the search engine.
        :param objectives: Ordered package objective declaration.
        :returns: Separate representation, feasibility, objective, and analysis
            findings. Positive infinity is reserved for hard infeasibility.
        :raises ValueError: If the objective declaration order differs from the
            loaded instance.
        :raises VrpExtensionEvaluationError: If a registered extension fails.
        """
        objective_ids = tuple(item.objective_id for item in objectives.objectives)
        expected_ids = tuple(
            item.objective_id for item in self._instance.objective_data.objectives
        )
        if objective_ids != expected_ids:
            raise ValueError('ObjectiveDeclaration objective order must match instance')
        reason = representation_error(self._instance, solution)
        if reason is not None:
            return ApplicationEvaluation(False, None, None)
        assert isinstance(solution, VrpSolution)

        hard_reason = self._static_hard_failure(solution)
        if hard_reason is not None:
            return self._infeasible(
                objectives,
                (),
                (),
                HardFeasibilityFinding('static_constraint', hard_reason, None),
            )

        traces: list[VrpScenarioEvaluation] = []
        rule_measures_by_scenario: list[tuple[RuleMeasure, ...]] = []
        storage_hard_failure: HardFeasibilityFinding | None = None
        resource_findings_list: list[ResourceFinding] = []
        for scenario in self._instance.scenario_bank.scenarios:
            trace = simulate_scenario(
                self._instance,
                solution,
                scenario,
                post_horizon_rule=self._post_horizon_rule,
            )
            traces.append(trace)
            hard_reason = self._scenario_hard_failure(trace, solution)
            if hard_reason is not None:
                if hard_reason.startswith('transfer facility ') and (
                    'exceeds storage capacity' in hard_reason
                ):
                    resource_findings_list.extend(
                        self._transfer_storage_resource_findings(solution, trace)
                    )
                    if storage_hard_failure is None:
                        storage_hard_failure = HardFeasibilityFinding(
                            'scenario_constraint', hard_reason, trace.scenario_id
                        )
                    continue
                if storage_hard_failure is not None:
                    continue
                return self._infeasible(
                    objectives,
                    tuple(traces),
                    (),
                    HardFeasibilityFinding(
                        'scenario_constraint', hard_reason, trace.scenario_id
                    ),
                )
            if storage_hard_failure is not None:
                continue
            measures = (
                *assess_simple_rules(self._instance, solution, trace),
                *self._hos_rule_measures(solution, trace),
            )
            rule_measures_by_scenario.append(measures)
            if self._instance.settings.rule_treatment == 'compliance':
                failing_measure = next(
                    (item for item in measures if item.measure > 0.0), None
                )
                if failing_measure is not None:
                    return self._infeasible(
                        objectives,
                        tuple(traces),
                        (
                            ResourceFinding(
                                kind=failing_measure.kind,
                                resource_id=failing_measure.resource_id,
                                scenario_id=trace.scenario_id,
                                vehicle_id=failing_measure.vehicle_id,
                                day_index=failing_measure.day_index,
                                activity_ref=None,
                                measure=failing_measure.measure,
                                penalty_by_objective=(),
                            ),
                        ),
                        HardFeasibilityFinding(
                            'compliance_rule',
                            (
                                f'{failing_measure.rule_id} violation for '
                                f'{failing_measure.resource_id}: '
                                f'{failing_measure.measure}'
                            ),
                            trace.scenario_id,
                        ),
                    )

        if storage_hard_failure is not None:
            return self._infeasible(
                objectives,
                tuple(traces),
                tuple(resource_findings_list),
                storage_hard_failure,
            )

        window_guidance: dict[
            tuple[int, int, int], tuple[WindowScenarioAssessment, ...]
        ] = {}
        raw_findings: dict[
            tuple[int, int], dict[ViolationCategory, list[tuple[str | None, float]]]
        ] = {}
        for objective_index, objective in enumerate(
            self._instance.objective_data.objectives
        ):
            for scenario_index, (scenario, trace) in enumerate(
                zip(self._instance.scenario_bank.scenarios, traces, strict=True)
            ):
                assigned_categories = self._objective_categories(objective.objective_id)
                category_values: dict[
                    ViolationCategory, list[tuple[str | None, float]]
                ] = {category: [] for category in assigned_categories}
                if (
                    objective.objective_id
                    == self._instance.time_window_violation_objective_id
                ):
                    assessments = self._window_assessments(
                        objective, scenario, trace, category_values
                    )
                else:
                    assessments = {}
                for (route_index, stop_index), values in assessments.items():
                    key = (scenario_index, route_index, stop_index)
                    window_guidance[key] = window_guidance.get(key, ()) + values
                self._append_soft_resource_findings(
                    objective,
                    scenario,
                    trace,
                    category_values,
                    solution,
                    rule_measures_by_scenario[scenario_index],
                    resource_findings_list,
                    frozenset(assigned_categories),
                )
                raw_findings[(objective_index, scenario_index)] = category_values

        completed_traces = self._attach_window_assessments(traces, window_guidance)
        scenario_costs: list[ScenarioObjectiveCost] = []
        objective_values: list[float] = []
        resource_findings = tuple(resource_findings_list)
        for objective_index, objective in enumerate(
            self._instance.objective_data.objectives
        ):
            scenario_inputs: list[ScenarioObjectiveInput] = []
            for scenario_index, (scenario, trace) in enumerate(
                zip(
                    self._instance.scenario_bank.scenarios,
                    completed_traces,
                    strict=True,
                )
            ):
                base_value = self._base_measure(objective, solution, trace)
                category_inputs = tuple(
                    PenaltyCategoryInput(
                        category_id=category,
                        scale=float(objective.penalty_scales.get(category, 0.0)),
                        values=tuple(
                            value
                            for _, value in raw_findings[
                                (objective_index, scenario_index)
                            ].get(category, [])
                        ),
                    )
                    for category in self._objective_categories(objective.objective_id)
                )
                penalty = self._penalty_aggregate(
                    objective, category_inputs, scenario.scenario_id
                )
                omission = self._omission_cost(objective.objective_id, solution)
                scenario_cost = validate_finite_result(
                    fsum((base_value, penalty, omission)),
                    'complete scenario objective cost',
                )
                scenario_inputs.append(
                    ScenarioObjectiveInput(
                        scenario_id=scenario.scenario_id,
                        probability=scenario.probability,
                        value=scenario_cost,
                    )
                )
                scenario_costs.append(
                    ScenarioObjectiveCost(
                        objective_id=objective.objective_id,
                        scenario_id=scenario.scenario_id,
                        value=scenario_cost,
                    )
                )
            objective_values.append(
                self._scenario_aggregate(objective, tuple(scenario_inputs))
            )

        analysis = VrpOperatorAnalysis(
            is_complete=True,
            scenario_evaluations=completed_traces,
            resource_findings=resource_findings,
            scenario_objective_costs=tuple(scenario_costs),
        )
        return ApplicationEvaluation(True, True, tuple(objective_values), analysis)

    def _infeasible(
        self,
        objectives: ObjectiveDeclaration,
        traces: tuple[VrpScenarioEvaluation, ...],
        findings: tuple[ResourceFinding, ...],
        hard_failure: HardFeasibilityFinding,
    ) -> ApplicationEvaluation:
        analysis = VrpOperatorAnalysis(False, traces, findings, (), hard_failure)
        return ApplicationEvaluation(
            True,
            False,
            tuple(inf for _ in objectives.objectives),
            analysis,
        )
