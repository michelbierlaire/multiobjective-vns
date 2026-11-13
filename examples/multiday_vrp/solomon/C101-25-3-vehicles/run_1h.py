"""Run the fixed three-vehicle C101-25 instance for a reproducible experiment.

Created: 2026-11-08T17:09:30+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import csv
from pathlib import Path

from multiobjective_vns import BudgetLimits, MultiobjectiveVNS, MultiobjectiveVNSConfig
from multiobjective_vns.multiday_vrp import (
    ActivityRef,
    ActivityStop,
    RouteLeg,
    VehicleDayRoute,
    VrpApplication,
    VrpSolution,
    load_vrp_application,
)
from multiobjective_vns.multiday_vrp.input_records import PackageTimeWindowRecord

EXAMPLE_DIRECTORY = Path(__file__).resolve().parent
INSTANCE_MANIFEST = EXAMPLE_DIRECTORY / 'input' / 'instance.toml'
LOG_DIRECTORY = (
    EXAMPLE_DIRECTORY
    / 'run-logs-1h-mu-0-compositions-30-20261109-seed-0'
)


def build_initial_solution(application: VrpApplication) -> VrpSolution:
    """Build a greedy, zero-window-penalty initial plan from the instance data.

    Each vehicle's route is built in turn. At every step, the next delivery is
    the currently feasible package with the earliest latest service start.
    The selected package must fit the vehicle and leave enough time to return
    to its depot before availability ends.
    """
    instance = application.instance
    packages = {item.package_id: item for item in instance.package_data.packages}
    delivery_windows: dict[str, list[PackageTimeWindowRecord]] = {}
    for window in instance.package_data.package_time_windows:
        if window.activity == 'delivery':
            delivery_windows.setdefault(window.package_id, []).append(window)
    arc_by_endpoints = {
        (item.tail_node_id, item.head_node_id): item.arc_id
        for item in instance.network_data.arcs
    }
    scenario = instance.scenario_bank.scenarios[0]
    travel_times = instance.scenario_bank.travel_times

    def travel_time(from_node: str, to_node: str) -> float:
        arc_id = arc_by_endpoints[(from_node, to_node)]
        return travel_times[(scenario.scenario_id, arc_id, 1, 0)]

    remaining_package_ids = set(packages)
    customer_routes: list[tuple[str, ...]] = []
    vehicles = sorted(instance.fleet_data.vehicles, key=lambda item: item.vehicle_id)
    for vehicle in vehicles:
        depot = next(
            item
            for item in instance.network_data.depots
            if item.depot_id == vehicle.initial_depot_id
        )
        availability = next(
            item
            for item in instance.fleet_data.vehicle_availability
            if item.vehicle_id == vehicle.vehicle_id
        )
        route_package_ids: list[str] = []
        current_node = depot.node_id
        current_time = availability.start_time
        current_load = 0.0

        while remaining_package_ids:
            eligible: list[tuple[float, str, float, float]] = []
            for package_id in remaining_package_ids:
                package = packages[package_id]
                if current_load + package.load > vehicle.cargo_capacity + 1e-9:
                    continue
                arrival_time = current_time + travel_time(
                    current_node, package.delivery_node_id
                )
                for window in delivery_windows[package_id]:
                    service_start = max(arrival_time, window.start_time)
                    service_finish = service_start + package.delivery_service_duration
                    return_time = service_finish + travel_time(
                        package.delivery_node_id, depot.node_id
                    )
                    if (
                        service_finish <= window.end_time + 1e-9
                        and return_time <= availability.end_time + 1e-9
                    ):
                        latest_start = (
                            window.end_time - package.delivery_service_duration
                        )
                        eligible.append(
                            (latest_start, package_id, service_start, service_finish)
                        )
                        break

            if not eligible:
                break

            _, package_id, service_start, service_finish = min(eligible)
            package = packages[package_id]
            route_package_ids.append(package_id)
            remaining_package_ids.remove(package_id)
            current_load += package.load
            current_node = package.delivery_node_id
            current_time = service_finish

        if route_package_ids:
            customer_routes.append(tuple(route_package_ids))

    if remaining_package_ids:
        unassigned = ', '.join(sorted(remaining_package_ids))
        raise ValueError(f'greedy construction left packages unassigned: {unassigned}')
    if len(customer_routes) != len(vehicles):
        raise ValueError(
            f'greedy construction used {len(customer_routes)} routes for '
            f'{len(vehicles)} vehicles'
        )

    routes: list[VehicleDayRoute] = []
    drivers = sorted(
        (
            person
            for person in instance.personnel_data.personnel
            if person.authorized_driver
        ),
        key=lambda item: item.person_id,
    )
    for vehicle, driver, package_ids in zip(
        vehicles, drivers, customer_routes, strict=True
    ):
        vehicle_id = vehicle.vehicle_id
        driver_id = driver.person_id
        depot = next(
            item
            for item in instance.network_data.depots
            if item.depot_id == vehicle.initial_depot_id
        )
        availability = next(
            item
            for item in instance.fleet_data.vehicle_availability
            if item.vehicle_id == vehicle_id
        )
        stops: list[ActivityStop] = []
        legs: list[RouteLeg] = []

        for package_id in package_ids:
            stops.append(
                ActivityStop(
                    ActivityRef('pickup', package_id),
                    frozenset(),
                    0.0,
                    0.0,
                    0.0,
                    False,
                )
            )
            legs.append(RouteLeg(()))

        current_node = depot.node_id
        current_time = availability.start_time
        for package_id in package_ids:
            package = packages[package_id]
            destination = package.delivery_node_id
            arc_id = arc_by_endpoints[(current_node, destination)]
            legs.append(RouteLeg((arc_id,)))
            current_time += travel_time(current_node, destination)
            window = next(
                item
                for item in delivery_windows[package_id]
                if max(current_time, item.start_time)
                + package.delivery_service_duration
                <= item.end_time + 1e-9
            )
            service_start = max(current_time, window.start_time)
            service_finish = service_start + package.delivery_service_duration
            stops.append(
                ActivityStop(
                    ActivityRef('delivery', package_id),
                    frozenset(),
                    float(service_start - current_time),
                    0.0,
                    0.0,
                    False,
                )
            )
            current_time = service_finish
            current_node = destination

        return_arc = arc_by_endpoints[(current_node, depot.node_id)]
        legs.append(RouteLeg((return_arc,)))
        routes.append(
            VehicleDayRoute(
                vehicle_id=vehicle_id,
                day_index=1,
                overnight_depot_id=vehicle.initial_depot_id,
                start_time=availability.start_time,
                driver_id=driver_id,
                stops=tuple(stops),
                legs=tuple(legs),
            )
        )

    solution = VrpSolution(tuple(routes), ())
    evaluation = application.application_evaluator.evaluate(
        solution, application.objective_declaration
    )
    if (
        not evaluation.representation_valid
        or evaluation.feasible is not True
        or evaluation.objective_values is None
        or evaluation.objective_values[1] != 0.0
    ):
        raise ValueError(
            'greedy initial plan must be feasible with zero time-window penalty; '
            f'got {evaluation.objective_values!r}'
        )
    return solution


def main() -> None:
    """Run the search and save final Pareto values and route orders."""
    application = load_vrp_application(INSTANCE_MANIFEST)
    initial_solution = build_initial_solution(application)
    search = MultiobjectiveVNS(
        solution_codec=application.solution_codec,
        solution_identity=application.solution_identity,
        objective_declaration=application.objective_declaration,
        operators=application.operators,
        pre_registered_compositions=application.pre_registered_compositions,
        application_evaluator=application.application_evaluator,
        problem_instance_id=application.problem_instance_id,
        operator_groups=application.operator_groups,
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=10,
            composition_selection_probability=0.30,
            feedback_mu=0.0,
            budget_limits=BudgetLimits(
                max_wall_time_seconds=3600.0,
                max_candidate_attempts=1_000_000,
                max_evaluation_requests=10_000_000,
            ),
            random_seed=0,
        ),
    )
    result = search.run(
        initial_solutions=(initial_solution,),
        log_directory=LOG_DIRECTORY,
        progress_report_interval_seconds=60.0,
    )
    pareto_records = result.search_state.pareto_solutions()
    output_path = LOG_DIRECTORY / 'pareto-solutions.csv'
    with output_path.open('w', newline='', encoding='utf-8') as output_file:
        writer = csv.writer(output_file)
        writer.writerow(
            (
                'solution_id',
                'total_distance',
                'time_window_violation',
                'total_waiting_time',
                'routes',
            )
        )
        for record in pareto_records:
            evaluation = record.evaluation_record.application_evaluation
            if evaluation is None or evaluation.objective_values is None:
                continue
            route_descriptions = []
            for route in record.solution.routes:
                deliveries = [
                    stop.activity_ref.activity_id
                    for stop in route.stops
                    if type(stop) is ActivityStop
                    and stop.activity_ref.kind == 'delivery'
                ]
                route_descriptions.append(
                    f'{route.vehicle_id}: ' + ' '.join(deliveries)
                )
            writer.writerow(
                (
                    str(record.solution_id),
                    *evaluation.objective_values,
                    ' | '.join(route_descriptions),
                )
            )

    print(f'Termination: {result.termination_reason.value}')
    print(f'Elapsed seconds: {result.executive_summary.elapsed_seconds:.1f}')
    print(f'Candidate attempts: {result.counters.candidate_attempts}')
    print(f'Evaluation requests: {result.counters.evaluation_requests}')
    print(f'Pareto solutions: {len(pareto_records)}')
    print(f'Event log: {result.event_log_path}')
    print(f'Executive summary: {result.summary_path}')
    print(f'Pareto values and routes: {output_path}')


if __name__ == '__main__':
    main()
