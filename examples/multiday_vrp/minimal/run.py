"""Run the minimal multi-day VRP example with the generic VNS engine.

Created: 2026-11-04T00:03:09+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path

from multiobjective_vns import BudgetLimits, MultiobjectiveVNS, MultiobjectiveVNSConfig
from multiobjective_vns.multiday_vrp import (
    DepotOnlyStop,
    RouteLeg,
    VehicleDayRoute,
    VrpOperatorId,
    VrpSolution,
    load_vrp_application,
)
from multiobjective_vns.records.results import RunResult

EXAMPLE_DIRECTORY = Path(__file__).resolve().parent
INSTANCE_MANIFEST = EXAMPLE_DIRECTORY / 'instance' / 'instance.toml'


def run_example(*, log_directory: Path | None = None) -> RunResult:
    """Load the example, create a search, and run a small reproducible search.

    :param log_directory: Optional destination for event and summary files.
    :returns: Search result, including its Pareto solutions and run reports.
    """
    application = load_vrp_application(INSTANCE_MANIFEST)
    two_opt_id = VrpOperatorId('vrp.generic.two_opt', None)
    two_opt = next(
        registration
        for registration in application.operators
        if registration.operator_id == two_opt_id
    )
    search = MultiobjectiveVNS(
        solution_codec=application.solution_codec,
        solution_identity=application.solution_identity,
        objective_declaration=application.objective_declaration,
        operators=(two_opt,),
        application_evaluator=application.application_evaluator,
        problem_instance_id=application.problem_instance_id,
        operator_groups=application.operator_groups,
        config=MultiobjectiveVNSConfig(
            budget_limits=BudgetLimits(
                max_wall_time_seconds=30.0,
                max_candidate_attempts=1,
                max_evaluation_requests=1,
            ),
            max_neighborhood_size=1,
            random_seed=2026,
        ),
    )
    return search.run(
        initial_solutions=(
            VrpSolution(
                routes=(
                    VehicleDayRoute(
                        vehicle_id='vehicle-1',
                        day_index=1,
                        overnight_depot_id='depot-1',
                        start_time=0.0,
                        driver_id='driver-1',
                        stops=(
                            DepotOnlyStop('node-b', 0.0, 0.0, 0.0, False),
                            DepotOnlyStop('node-c', 0.0, 0.0, 0.0, False),
                        ),
                        legs=(
                            RouteLeg(('arc-ab',)),
                            RouteLeg(('arc-bc',)),
                            RouteLeg(('arc-ca',)),
                        ),
                    ),
                ),
                transfer_steps=(),
            ),
        ),
        log_directory=(
            Path('./multiobjective-vns-logs')
            if log_directory is None
            else log_directory
        ),
    )


def main() -> None:
    """Run the example and print the location of its reports."""
    result = run_example()
    print(f'Termination: {result.termination_reason.value}')
    print(f'Pareto solutions: {len(result.search_state.pareto_solutions())}')
    print(f'Event log: {result.event_log_path}')
    print(f'Executive summary: {result.summary_path}')


if __name__ == '__main__':
    main()
