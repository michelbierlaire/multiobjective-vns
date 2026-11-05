"""Objective measure methods for multi-day VRP evaluation.

Created: 2026-11-03T18:47:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict
from math import fsum

from multiobjective_vns.multiday_vrp.evaluation.aggregation import (
    validate_finite_result,
)
from multiobjective_vns.multiday_vrp.evaluation.runtime import (
    _EvaluatorRuntime,
)
from multiobjective_vns.multiday_vrp.extensions import (
    BaseMeasureRegistration,
    BaseMeasureRequest,
    ViolationCategory,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import (
    ObjectiveSpec,
)
from multiobjective_vns.multiday_vrp.solution import (
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


class _ObjectiveMeasureMethods(_EvaluatorRuntime):
    """Provide objective measure methods."""

    __slots__ = ()

    def _base_measure(
        self,
        objective: ObjectiveSpec,
        solution: VrpSolution,
        trace: VrpScenarioEvaluation,
    ) -> float:

        measure_id = objective.base_measure_id

        if measure_id in {
            'total_travel_time',
            'event_count',
            'maximum_event_exposure',
            'total_route_duration',
            'maximum_route_duration',
            'total_personnel_work_time',
            'maximum_personnel_work_time',
            'total_driver_work_time',
            'maximum_driver_work_time',
            'vehicles_used',
            'vehicle_days_used',
            'tours_used',
        }:
            return self._builtin_measure(
                measure_id, solution, trace, objective.objective_id
            )

        registration = self._registration('base_measure', measure_id)

        if registration is None:
            raise self._extension_error(
                'base_measure',
                measure_id,
                'evaluate',
                objective.objective_id,
                trace.scenario_id,
                'no callback is registered',
            )

        assert isinstance(registration, BaseMeasureRegistration)

        request = BaseMeasureRequest(
            instance=self._instance,
            solution=solution,
            objective_id=objective.objective_id,
            scenario=trace,
            parameters=objective.base_measure_parameters,
            application_data=self._instance.application_data,
        )

        value = self._call_extension(
            registration,
            'base_measure',
            'evaluate',
            objective.objective_id,
            trace.scenario_id,
            registration.evaluate,
            request,
        )

        return validate_finite_result(value, 'base-measure result')

    def _builtin_measure(
        self,
        measure_id: str,
        solution: VrpSolution,
        trace: VrpScenarioEvaluation,
        objective_id: str,
    ) -> float:

        if measure_id == 'total_travel_time':
            return validate_finite_result(
                fsum(
                    traversal.travel_time
                    for day in trace.vehicle_days
                    for leg in day.legs
                    for traversal in leg.arc_traversals
                ),
                'total travel time',
            )

        if measure_id in {'event_count', 'maximum_event_exposure'}:
            counts = self._event_counts(trace, objective_id, solution)

            if measure_id == 'event_count':
                return float(sum(counts))

            return float(max(counts, default=0))

        if measure_id in {'total_route_duration', 'maximum_route_duration'}:
            durations = tuple(
                day.duty_end_time - day.duty_start_time for day in trace.vehicle_days
            )

            if measure_id == 'total_route_duration':
                return validate_finite_result(fsum(durations), 'total route duration')

            return float(max(durations, default=0.0))

        if measure_id in {
            'total_personnel_work_time',
            'maximum_personnel_work_time',
            'total_driver_work_time',
            'maximum_driver_work_time',
        }:
            values = self._person_work_times(trace, solution, measure_id)

            if measure_id.startswith('total_'):
                return validate_finite_result(
                    fsum(values.values()), 'total personnel work time'
                )

            return float(max(values.values(), default=0.0))

        if measure_id == 'vehicles_used':
            return float(len({route.vehicle_id for route in solution.routes}))

        if measure_id == 'vehicle_days_used':
            return float(len(solution.routes))

        if measure_id == 'tours_used':
            return float(
                sum(
                    1 + sum(stop.tour_end for stop in route.stops)
                    for route in solution.routes
                )
            )

        raise ValueError(f'unsupported built-in base measure {measure_id!r}')

    def _event_counts(
        self, trace: VrpScenarioEvaluation, objective_id: str, solution: VrpSolution
    ) -> tuple[int, ...]:

        selected_types = {
            item.event_type_id
            for item in self._instance.event_data.objective_event_types
            if item.objective_id == objective_id
        }

        events = self._instance.event_data.events

        event_types = {
            item.event_type_id: item.location_domain
            for item in self._instance.event_data.event_types
        }

        route_counts: list[int] = []

        routes = {(item.vehicle_id, item.day_index): item for item in solution.routes}

        for day in trace.vehicle_days:
            route = routes[(day.vehicle_id, day.day_index)]

            vehicle = next(
                item
                for item in self._instance.fleet_data.vehicles
                if item.vehicle_id == route.vehicle_id
            )

            previous_route = [
                item
                for item in solution.routes
                if item.vehicle_id == route.vehicle_id
                and item.day_index < route.day_index
            ]

            depot_id = (
                max(previous_route, key=lambda item: item.day_index).overnight_depot_id
                if previous_route
                else vehicle.initial_depot_id
            )

            current_node = next(
                item.node_id
                for item in self._instance.network_data.depots
                if item.depot_id == depot_id
            )

            count = sum(
                1
                for event in events
                if event.event_type_id in selected_types
                and event.location_id == current_node
                and event_types[event.event_type_id] == 'node'
            )

            for leg in day.legs:
                for traversal in leg.arc_traversals:
                    arc = next(
                        item
                        for item in self._instance.network_data.arcs
                        if item.arc_id == traversal.arc_id
                    )

                    count += sum(
                        1
                        for event in events
                        if event.event_type_id in selected_types
                        and event_types[event.event_type_id] == 'arc'
                        and event.location_id == traversal.arc_id
                    )

                    current_node = arc.head_node_id

                    count += sum(
                        1
                        for event in events
                        if event.event_type_id in selected_types
                        and event_types[event.event_type_id] == 'node'
                        and event.location_id == current_node
                    )

            route_counts.append(count)

        return tuple(route_counts)

    def _person_work_times(
        self, trace: VrpScenarioEvaluation, solution: VrpSolution, measure_id: str
    ) -> dict[tuple[str, int], float]:

        drivers_only = measure_id in {
            'total_driver_work_time',
            'maximum_driver_work_time',
        }

        routes = {(item.vehicle_id, item.day_index): item for item in solution.routes}

        minimum_break = {
            item.person_id: item.qualifying_break_minimum
            for item in self._instance.work_rule_data.person_rule_limits
        }

        totals: dict[tuple[str, int], float] = defaultdict(float)

        for day in trace.vehicle_days:
            route = routes[(day.vehicle_id, day.day_index)]

            people = {route.driver_id}

            if not drivers_only:
                people.update(
                    person_id
                    for stop in route.stops
                    if type(stop) is ActivityStop
                    for person_id in stop.team_member_ids
                )

            for person_id in people:
                qualifying = minimum_break.get(person_id)

                breaks = fsum(
                    route_stop.break_duration
                    for route_stop in route.stops
                    if qualifying is not None
                    and route_stop.break_duration >= qualifying
                )

                totals[(person_id, day.day_index)] += max(
                    0.0, day.duty_end_time - day.duty_start_time - breaks
                )

        return dict(totals)
