"""Window penalty methods for multi-day VRP evaluation.

Created: 2026-11-03T18:47:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from math import fsum

from multiobjective_vns.multiday_vrp.evaluation.aggregation import (
    validate_finite_result,
)
from multiobjective_vns.multiday_vrp.evaluation.helpers import (
    _lower_violation,
    _outside_availability,
    _positive_violation,
    _upper_violation,
)
from multiobjective_vns.multiday_vrp.evaluation.objective_aggregation import (
    _ObjectiveAggregationMethods,
)
from multiobjective_vns.multiday_vrp.evaluation.records import (
    ResourceFinding,
)
from multiobjective_vns.multiday_vrp.evaluation.rules import RuleMeasure
from multiobjective_vns.multiday_vrp.extensions import (
    ViolationCategory,
    VrpScenarioEvaluation,
    WindowScenarioAssessment,
)
from multiobjective_vns.multiday_vrp.input_records import (
    ObjectiveSpec,
    ScenarioRecord,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    VrpSolution,
)

_CATEGORY_ORDER: tuple[ViolationCategory, ...] = (
    'early_window',
    'late_window',
    'vehicle_availability',
    'personnel_availability',
    'duty_overlap',
    'simple_rule',
    'hos_rule',
)


class _WindowPenaltyMethods(_ObjectiveAggregationMethods):
    """Provide window penalty methods."""

    __slots__ = ()

    def _active_categories(self) -> tuple[ViolationCategory, ...]:

        instance = self._instance

        active: set[ViolationCategory] = set()

        if instance.package_data.packages or instance.intervention_data.interventions:
            active.update({'early_window', 'late_window'})

        if instance.network_data.depots:
            active.add('vehicle_availability')

        if instance.personnel_data.personnel:
            active.update({'personnel_availability', 'duty_overlap'})

        if instance.settings.rule_treatment == 'penalty':
            if instance.settings.active_simple_rules:
                active.add('simple_rule')

            if instance.settings.hos_regime_id != 'none':
                active.add('hos_rule')

        return tuple(category for category in _CATEGORY_ORDER if category in active)

    def _objective_categories(self, objective_id: str) -> tuple[ViolationCategory, ...]:
        assigned = {
            item.violation_category
            for item in self._instance.objective_data.penalty_functions
            if item.objective_id == objective_id and item.rule_id == ''
        }
        return tuple(
            category for category in self._active_categories() if category in assigned
        )

    def _window_assessments(
        self,
        objective: ObjectiveSpec,
        scenario: ScenarioRecord,
        trace: VrpScenarioEvaluation,
        category_values: dict[ViolationCategory, list[tuple[str | None, float]]],
    ) -> dict[tuple[int, int], tuple[WindowScenarioAssessment, ...]]:

        result: dict[tuple[int, int], tuple[WindowScenarioAssessment, ...]] = {}

        for route_index, vehicle_day in enumerate(trace.vehicle_days):
            for stop in vehicle_day.stops:
                if stop.activity_ref is None:
                    continue

                windows = self._activity_windows(
                    stop.activity_ref, vehicle_day.day_index
                )

                if not windows:
                    continue

                alternatives: list[
                    tuple[float, float, float, int, float, float, float, float]
                ] = []

                for window in windows:
                    assert stop.service_start is not None

                    assert stop.service_finish is not None

                    early = _lower_violation(stop.service_start, window[0])

                    late = _upper_violation(stop.service_finish, window[1])

                    early_penalty = self._penalty_value(
                        objective, 'early_window', None, early, scenario.scenario_id
                    )

                    late_penalty = self._penalty_value(
                        objective, 'late_window', None, late, scenario.scenario_id
                    )

                    scale_early = float(
                        objective.penalty_scales.get('early_window', 0.0)
                    )

                    scale_late = float(objective.penalty_scales.get('late_window', 0.0))

                    guidance = fsum(
                        (scale_early * early_penalty, scale_late * late_penalty)
                    )

                    alternatives.append(
                        (
                            guidance,
                            window[0],
                            window[1],
                            window[2],
                            early,
                            late,
                            early_penalty,
                            late_penalty,
                        )
                    )

                chosen = min(alternatives, key=lambda item: item[:4])

                category_values['early_window'].append((None, chosen[6]))

                category_values['late_window'].append((None, chosen[7]))

                result[(route_index, stop.stop_index)] = (
                    WindowScenarioAssessment(
                        objective.objective_id,
                        chosen[3],
                        chosen[4],
                        chosen[5],
                        validate_finite_result(chosen[0], 'window guidance value'),
                    ),
                )

        return result

    def _activity_windows(
        self, reference: ActivityRef, day_index: int
    ) -> tuple[tuple[float, float, int], ...]:

        if reference.kind in {'pickup', 'delivery'}:
            return tuple(
                (item.start_time, item.end_time, item.window_index)
                for item in self._instance.package_data.package_time_windows
                if item.package_id == reference.activity_id
                and item.activity == reference.kind
            )

        return tuple(
            (item.start_time, item.end_time, item.window_index)
            for item in self._instance.intervention_data.intervention_time_windows
            if item.intervention_id == reference.activity_id
            and item.day_index == day_index
        )

    def _attach_window_assessments(
        self,
        traces: list[VrpScenarioEvaluation],
        window_guidance: dict[
            tuple[int, int, int], tuple[WindowScenarioAssessment, ...]
        ],
    ) -> tuple[VrpScenarioEvaluation, ...]:

        completed: list[VrpScenarioEvaluation] = []

        for scenario_index, trace in enumerate(traces):
            vehicle_days = []

            for route_index, day in enumerate(trace.vehicle_days):
                stops = tuple(
                    stop._replace(
                        window_assessments_by_objective=window_guidance.get(
                            (scenario_index, route_index, stop.stop_index), ()
                        )
                    )
                    for stop in day.stops
                )

                vehicle_days.append(day._replace(stops=stops))

            completed.append(trace._replace(vehicle_days=tuple(vehicle_days)))

        return tuple(completed)

    def _append_soft_resource_findings(
        self,
        objective: ObjectiveSpec,
        scenario: ScenarioRecord,
        trace: VrpScenarioEvaluation,
        category_values: dict[ViolationCategory, list[tuple[str | None, float]]],
        solution: VrpSolution,
        simple_rule_measures: tuple[RuleMeasure, ...],
        resource_findings: list[ResourceFinding],
        assigned_categories: frozenset[ViolationCategory],
    ) -> None:

        route_by_key = {
            (route.vehicle_id, route.day_index): route for route in solution.routes
        }

        person_duties: dict[str, list[tuple[float, float, str, int]]] = defaultdict(
            list
        )

        for day in trace.vehicle_days:
            route = route_by_key[(day.vehicle_id, day.day_index)]

            crew = {route.driver_id}

            crew.update(
                person_id
                for stop in route.stops
                if type(stop) is ActivityStop
                for person_id in stop.team_member_ids
            )

            for person_id in crew:
                person_duties[person_id].append(
                    (
                        day.duty_start_time,
                        day.duty_end_time,
                        day.vehicle_id,
                        day.day_index,
                    )
                )

        if 'vehicle_availability' in assigned_categories:
            for vehicle in self._instance.fleet_data.vehicles:
                vehicle_id = vehicle.vehicle_id

                vehicle_intervals = tuple(
                    item
                    for item in self._instance.fleet_data.vehicle_availability
                    if item.vehicle_id == vehicle_id
                )

                for day in trace.vehicle_days:
                    if day.vehicle_id == vehicle_id:
                        amount = _outside_availability(
                            day.duty_start_time,
                            day.duty_end_time,
                            tuple(
                                (item.start_time, item.end_time)
                                for item in vehicle_intervals
                            ),
                        )

                        value = self._penalty_value(
                            objective,
                            'vehicle_availability',
                            None,
                            amount,
                            scenario.scenario_id,
                        )

                        category_values['vehicle_availability'].append((None, value))

                        scaled_value = (
                            float(
                                objective.penalty_scales.get(
                                    'vehicle_availability', 0.0
                                )
                            )
                            * value
                        )

                        resource_findings.append(
                            ResourceFinding(
                                'vehicle_availability',
                                vehicle_id,
                                scenario.scenario_id,
                                vehicle_id,
                                day.day_index,
                                None,
                                amount,
                                ((objective.objective_id, scaled_value),),
                            )
                        )

        for person in self._instance.personnel_data.personnel:
            person_id = person.person_id

            if person_id not in person_duties:
                continue

            person_intervals = tuple(
                person_availability
                for person_availability in self._instance.personnel_data.personnel_availability
                if person_availability.person_id == person_id
            )

            amount = fsum(
                _outside_availability(
                    start,
                    end,
                    tuple(
                        (item.start_time, item.end_time) for item in person_intervals
                    ),
                )
                for start, end, _, _ in person_duties[person_id]
            )

            if 'personnel_availability' in assigned_categories:
                value = self._penalty_value(
                    objective,
                    'personnel_availability',
                    None,
                    amount,
                    scenario.scenario_id,
                )

                category_values['personnel_availability'].append((None, value))

                scaled_value = (
                    float(objective.penalty_scales['personnel_availability']) * value
                )

                resource_findings.append(
                    ResourceFinding(
                        'personnel_availability',
                        person_id,
                        scenario.scenario_id,
                        None,
                        None,
                        None,
                        amount,
                        ((objective.objective_id, scaled_value),),
                    )
                )

            if 'duty_overlap' in assigned_categories:
                duties = sorted(person_duties[person_id])

                for left, right in combinations(duties, 2):
                    overlap = max(0.0, min(left[1], right[1]) - max(left[0], right[0]))

                    measure = _positive_violation(overlap)

                    value = self._penalty_value(
                        objective, 'duty_overlap', None, measure, scenario.scenario_id
                    )

                    category_values['duty_overlap'].append((None, value))

                    scaled_value = (
                        float(objective.penalty_scales['duty_overlap']) * value
                    )

                    resource_id = (
                        f'{person_id}:{left[2]}:{left[3]}:{right[2]}:{right[3]}'
                    )

                    resource_findings.append(
                        ResourceFinding(
                            'duty_overlap',
                            resource_id,
                            scenario.scenario_id,
                            None,
                            None,
                            None,
                            measure,
                            ((objective.objective_id, scaled_value),),
                        )
                    )

        if self._instance.settings.rule_treatment == 'penalty':
            for rule_measure in simple_rule_measures:
                if rule_measure.penalty_category not in assigned_categories:
                    continue
                penalty = self._penalty_value(
                    objective,
                    rule_measure.penalty_category,
                    rule_measure.rule_id,
                    rule_measure.measure,
                    scenario.scenario_id,
                )

                category_values[rule_measure.penalty_category].append(
                    (rule_measure.rule_id, penalty)
                )

                scale = float(
                    objective.penalty_scales.get(rule_measure.penalty_category, 0.0)
                )

                resource_findings.append(
                    ResourceFinding(
                        rule_measure.kind,
                        rule_measure.resource_id,
                        scenario.scenario_id,
                        rule_measure.vehicle_id,
                        rule_measure.day_index,
                        None,
                        rule_measure.measure,
                        ((objective.objective_id, scale * penalty),),
                    )
                )
