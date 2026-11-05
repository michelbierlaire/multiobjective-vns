"""Scenario-specific simulation of stored VRP routes.

Created: 2026-11-03T18:34:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Literal, cast
from zoneinfo import ZoneInfo

from multiobjective_vns.multiday_vrp.extensions import (
    ArcTraversalEvaluation,
    LegScenarioEvaluation,
    StopScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import (
    ArcRecord,
    InterventionRecord,
    LoadedScenarioBank,
    LoadedVrpInstance,
    PackageRecord,
    ScenarioRecord,
    VehicleRecord,
)
from multiobjective_vns.multiday_vrp.scenario_generation.records import PostHorizonRule
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
    RouteStop,
    TransferOperationStop,
    TransferStep,
    VehicleDayRoute,
    VrpSolution,
)

__all__ = ['simulate_scenario']

_SECONDS_PER_UNIT = {'seconds': 1.0, 'minutes': 60.0, 'hours': 3600.0}
_BUILTIN_POST_HORIZON = ('constant_last_boundary', 'builtin-v1')


def simulate_scenario(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    scenario: ScenarioRecord,
    *,
    post_horizon_rule: PostHorizonRule | None = None,
) -> VrpScenarioEvaluation:
    """Project every route and arc into one fixed traffic scenario.

    :param instance: Validated application data and scenario travel functions.
    :param solution: Structurally valid immutable route plan.
    :param scenario: One scenario from ``instance.scenario_bank``.
    :param post_horizon_rule: Matching application rule for post-horizon travel;
        ``None`` selects the package's constant-last-boundary rule.
    :returns: Complete ordered route, stop, and arc trace for the scenario.
    :raises ValueError: If a scenario or post-horizon rule is inconsistent.
    :raises RuntimeError: If a travel calculation is non-finite or invalid.
    """
    scenario_ids = {item.scenario_id for item in instance.scenario_bank.scenarios}
    if scenario.scenario_id not in scenario_ids:
        raise ValueError('scenario does not belong to the loaded ScenarioBank')
    _validate_post_horizon_rule(instance.scenario_bank, post_horizon_rule)

    arcs = {item.arc_id: item for item in instance.network_data.arcs}
    depots = {item.depot_id: item.node_id for item in instance.network_data.depots}
    vehicles = {item.vehicle_id: item for item in instance.fleet_data.vehicles}
    packages = {item.package_id: item for item in instance.package_data.packages}
    interventions = {
        item.intervention_id: item for item in instance.intervention_data.interventions
    }
    transfer_steps = {
        (item.package_id, item.step_number): item for item in solution.transfer_steps
    }
    routes_by_vehicle_day = {
        (item.vehicle_id, item.day_index): item for item in solution.routes
    }

    vehicle_days: list[VehicleDayScenarioEvaluation] = []
    for route in solution.routes:
        current_depot_id = _starting_depot_id(route, routes_by_vehicle_day, vehicles)
        current_node = depots[current_depot_id]
        current_time = route.start_time
        leg_results: list[LegScenarioEvaluation] = []
        stop_results: list[StopScenarioEvaluation] = []

        for leg_index, leg in enumerate(route.legs):
            current_time, traversals = _simulate_leg(
                instance,
                scenario.scenario_id,
                leg,
                current_node,
                current_time,
                arcs,
                post_horizon_rule,
            )
            leg_results.append(LegScenarioEvaluation(leg_index, traversals))
            if leg_index == len(route.stops):
                continue

            stop = route.stops[leg_index]
            node_id = _stop_node_id(stop, transfer_steps, packages, interventions)
            current_time, stop_result = _simulate_stop(
                stop,
                leg_index,
                node_id,
                current_time,
                packages,
                interventions,
                transfer_steps,
            )
            stop_results.append(stop_result)
            current_node = node_id

        vehicle_days.append(
            VehicleDayScenarioEvaluation(
                vehicle_id=route.vehicle_id,
                day_index=route.day_index,
                duty_start_time=route.start_time,
                duty_end_time=current_time,
                stops=tuple(stop_results),
                legs=tuple(leg_results),
            )
        )

    return VrpScenarioEvaluation(
        scenario_id=scenario.scenario_id,
        probability=scenario.probability,
        vehicle_days=tuple(vehicle_days),
    )


def _starting_depot_id(
    route: VehicleDayRoute,
    routes_by_vehicle_day: dict[tuple[str, int], VehicleDayRoute],
    vehicles: Mapping[str, VehicleRecord],
) -> str:
    previous = [
        routes_by_vehicle_day[(route.vehicle_id, day)]
        for day in range(1, route.day_index)
        if (route.vehicle_id, day) in routes_by_vehicle_day
    ]
    if previous:
        return max(previous, key=lambda item: item.day_index).overnight_depot_id
    return vehicles[route.vehicle_id].initial_depot_id


def _simulate_leg(
    instance: LoadedVrpInstance,
    scenario_id: str,
    leg: RouteLeg,
    origin: str,
    departure_time: float,
    arcs: Mapping[str, ArcRecord],
    post_horizon_rule: PostHorizonRule | None,
) -> tuple[float, tuple[ArcTraversalEvaluation, ...]]:
    current_node = origin
    current_time = departure_time
    traversals: list[ArcTraversalEvaluation] = []
    for arc_id in leg.arc_ids:
        arc = arcs[arc_id]
        if arc.tail_node_id != current_node:
            raise ValueError(f'arc {arc_id!r} does not continue the route path')
        travel_time = _travel_time_at(
            instance.scenario_bank,
            scenario_id,
            arc_id,
            current_time,
            post_horizon_rule,
        )
        arrival_time = current_time + travel_time
        if not isfinite(arrival_time):
            raise RuntimeError(
                f'non-finite arrival time for scenario {scenario_id!r}, '
                f'arc {arc_id!r}, entry {current_time!r}'
            )
        traversals.append(
            ArcTraversalEvaluation(arc_id, current_time, arrival_time, travel_time)
        )
        current_node = arc.head_node_id
        current_time = arrival_time
    return current_time, tuple(traversals)


def _travel_time_at(
    bank: LoadedScenarioBank,
    scenario_id: str,
    arc_id: str,
    departure_time: float,
    post_horizon_rule: PostHorizonRule | None,
) -> float:
    final_boundary = bank.day_boundaries[-1][-1]
    if departure_time >= final_boundary:
        value: object
        if post_horizon_rule is None:
            value = bank.travel_times[
                (
                    scenario_id,
                    arc_id,
                    len(bank.planning_dates),
                    len(bank.day_boundaries[-1]) - 1,
                )
            ]
        else:
            value = _custom_post_horizon_time(
                bank, scenario_id, arc_id, departure_time, post_horizon_rule
            )
        return _finite_travel_time(value, scenario_id, arc_id, departure_time)

    # The actual calendar day determines the profile used for this traversal.
    day_index = _calendar_day_index(bank, departure_time)
    boundaries = bank.day_boundaries[day_index - 1]
    segment = bisect_right(boundaries, departure_time) - 1
    segment = min(max(segment, 0), len(boundaries) - 2)
    left_time = boundaries[segment]
    right_time = boundaries[segment + 1]
    left_value = bank.travel_times[(scenario_id, arc_id, day_index, segment)]
    right_value = bank.travel_times[(scenario_id, arc_id, day_index, segment + 1)]
    fraction = (departure_time - left_time) / (right_time - left_time)
    value = left_value + fraction * (right_value - left_value)
    return _finite_travel_time(value, scenario_id, arc_id, departure_time)


def _calendar_day_index(bank: LoadedScenarioBank, time_value: float) -> int:
    starts = tuple(day[0] for day in bank.day_boundaries)
    day_index = bisect_right(starts, time_value)
    if day_index < 1 or day_index > len(starts):
        raise RuntimeError(f'no ScenarioBank day covers time {time_value!r}')
    return day_index


def _custom_post_horizon_time(
    bank: LoadedScenarioBank,
    scenario_id: str,
    arc_id: str,
    departure_time: float,
    rule: PostHorizonRule,
) -> object:
    zone = ZoneInfo(bank.time_zone)
    seconds_per_unit = _SECONDS_PER_UNIT[bank.time_unit]
    origin = datetime.combine(bank.planning_dates[0], datetime.min.time(), tzinfo=zone)
    final_boundary = datetime.combine(
        bank.planning_dates[-1] + timedelta(days=1),
        datetime.min.time(),
        tzinfo=zone,
    )
    departure = origin.astimezone(UTC) + timedelta(
        seconds=departure_time * seconds_per_unit
    )
    final_profile = {
        item_id: bank.travel_times[
            (
                scenario_id,
                item_id,
                len(bank.planning_dates),
                len(bank.day_boundaries[-1]) - 1,
            )
        ]
        for item_id in bank.arc_ids
    }
    try:
        return rule.travel_time_at(
            arc_id,
            departure.astimezone(zone),
            final_boundary,
            final_profile,
            bank.time_unit,
        )
    except Exception as error:
        raise RuntimeError(
            f'post-horizon rule {rule.rule_id!r} failed for scenario '
            f'{scenario_id!r}, arc {arc_id!r}, time {departure_time!r}: {error}'
        ) from error


def _stop_node_id(
    stop: RouteStop,
    transfer_steps: Mapping[tuple[str, int], TransferStep],
    packages: Mapping[str, PackageRecord],
    interventions: Mapping[str, InterventionRecord],
) -> str:
    if type(stop) is ActivityStop:
        reference = stop.activity_ref
        if reference.kind == 'pickup':
            return packages[reference.activity_id].pickup_node_id
        if reference.kind == 'delivery':
            return packages[reference.activity_id].delivery_node_id
        return interventions[reference.activity_id].node_id
    if type(stop) is TransferOperationStop:
        step = transfer_steps[
            (stop.transfer_step_ref.package_id, stop.transfer_step_ref.step_number)
        ]
        return step.facility_id
    if type(stop) is DepotOnlyStop:
        return stop.node_id
    raise TypeError('unsupported route stop record')


def _simulate_stop(
    stop: RouteStop,
    stop_index: int,
    node_id: str,
    arrival_time: float,
    packages: Mapping[str, PackageRecord],
    interventions: Mapping[str, InterventionRecord],
    transfer_steps: Mapping[tuple[str, int], TransferStep],
) -> tuple[float, StopScenarioEvaluation]:
    if type(stop) is ActivityStop:
        activity_reference = stop.activity_ref
        if activity_reference.kind == 'pickup':
            duration = packages[activity_reference.activity_id].pickup_service_duration
        elif activity_reference.kind == 'delivery':
            duration = packages[
                activity_reference.activity_id
            ].delivery_service_duration
        else:
            duration = interventions[activity_reference.activity_id].service_duration
        stop_kind: Literal['activity', 'transfer_operation', 'depot_only'] = 'activity'
        activity_ref = activity_reference
        transfer_step_ref = None
    elif type(stop) is TransferOperationStop:
        transfer_reference = stop.transfer_step_ref
        package = packages[transfer_reference.package_id]
        duration = (
            package.transfer_out_service_duration
            if stop.role == 'out'
            else package.transfer_in_service_duration
        )
        stop_kind = 'transfer_operation'
        activity_ref = None
        transfer_step_ref = transfer_reference
    elif type(stop) is DepotOnlyStop:
        duration = 0.0
        stop_kind = 'depot_only'
        activity_ref = None
        transfer_step_ref = None
    else:
        raise TypeError('unsupported route stop record')

    service_start = arrival_time + stop.pre_service_wait
    service_finish = service_start + duration
    departure_time = service_finish + stop.wait
    if not all(
        isfinite(value) for value in (service_start, service_finish, departure_time)
    ):
        raise RuntimeError(f'non-finite stop time at stop {stop_index}')
    if type(stop) is DepotOnlyStop:
        public_service_start = None
        public_service_finish = None
        break_origin = arrival_time
    else:
        public_service_start = service_start
        public_service_finish = service_finish
        break_origin = service_finish
    if stop.break_duration > 0.0:
        break_start = break_origin
        break_finish = break_start + stop.break_duration
    else:
        break_start = None
        break_finish = None
    record = StopScenarioEvaluation(
        stop_index=stop_index,
        stop_kind=stop_kind,
        node_id=node_id,
        arrival_time=arrival_time,
        service_start=public_service_start,
        service_finish=public_service_finish,
        departure_time=departure_time,
        break_start=break_start,
        break_finish=break_finish,
        activity_ref=activity_ref,
        transfer_step_ref=transfer_step_ref,
        window_assessments_by_objective=(),
    )
    return departure_time, record


def _validate_post_horizon_rule(
    bank: LoadedScenarioBank, rule: PostHorizonRule | None
) -> None:
    selected = bank.post_horizon_rule_id, bank.post_horizon_rule_configuration_id
    if selected == _BUILTIN_POST_HORIZON:
        if rule is not None:
            raise ValueError('the built-in post-horizon rule is selected')
        return
    if rule is None or (rule.rule_id, rule.configuration_id) != selected:
        raise ValueError(
            'the selected post-horizon rule must be registered and supplied'
        )


def _finite_travel_time(
    value: object, scenario_id: str, arc_id: str, departure_time: float
) -> float:
    if type(value) not in (int, float):
        raise RuntimeError(
            f'travel time must be numeric for scenario {scenario_id!r}, '
            f'arc {arc_id!r}, time {departure_time!r}'
        )
    result = float(cast(int | float, value))
    if not isfinite(result) or result < 0.0:
        raise RuntimeError(
            f'travel time must be finite and nonnegative for scenario '
            f'{scenario_id!r}, arc {arc_id!r}, time {departure_time!r}'
        )
    return result
