"""Run a bounded multiobjective VNS search on the P410 instance.

Created: 2026-10-29T19:08:07+00:00
Author: Michel Bierlaire, EPFL
"""

from pathlib import Path

from multiobjective_vns import BudgetLimits, MultiobjectiveVNS, MultiobjectiveVNSConfig
from multiobjective_vns.knapsack import load_knapsack_application
from multiobjective_vns.records.results import RunResult


def run_example() -> RunResult:
    """Run P410 from the all-empty feasible initial solution.

    The input file is resolved relative to this script. Logs go into
    ``multiobjective-vns-logs`` under the current working directory.

    :returns: Search outcomes, continuation state, and reporting paths.
    """
    example_directory = Path(__file__).resolve().parent
    application = load_knapsack_application(example_directory / 'instance.toml')
    config = MultiobjectiveVNSConfig(
        budget_limits=BudgetLimits(
            max_wall_time_seconds=30,
            max_candidate_attempts=30,
            max_evaluation_requests=30,
        ),
        random_seed=2026,
    )
    search = MultiobjectiveVNS(
        solution_codec=application.solution_codec,
        solution_identity=application.solution_identity,
        objective_declaration=application.objective_declaration,
        operators=application.operators,
        application_evaluator=application.application_evaluator,
        problem_instance_id=application.problem_instance_id,
        config=config,
        operator_groups=application.operator_groups,
    )
    initial_solution = (0,) * application.instance.n_items
    return search.run(
        initial_solutions=(initial_solution,),
        log_directory=Path.cwd() / 'multiobjective-vns-logs',
    )


def main() -> None:
    """Run P410 and print the generated report locations."""
    result = run_example()
    print(f'Termination reason: {result.termination_reason.value}')
    print(f'Event log: {result.event_log_path}')
    print(f'Executive summary: {result.summary_path}')


if __name__ == '__main__':
    main()
