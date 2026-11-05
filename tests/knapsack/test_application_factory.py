"""Tests for assembling a usable knapsack application from TOML.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

from pathlib import Path

from multiobjective_vns import (
    ApplicationAssessment,
    BudgetLimits,
    MultiobjectiveVNS,
    MultiobjectiveVNSConfig,
)
from multiobjective_vns.knapsack import load_knapsack_application

INSTANCE_TOML = """\
format_version = 1
problem_instance_id = 'tiny-v1'
items = ['a', 'b', 'c']

[[resources]]
id = 'weight'
capacity = 3
consumption = [1, 2, 3]

[[objectives]]
id = 'profit-1'
name = 'Profit 1'
contribution = [2, 5, 7]

[[objectives]]
id = 'profit-2'
name = 'Profit 2'
contribution = [4, 3, 8]
"""


def test_loaded_application_constructs_a_core_search_and_solves(tmp_path: Path) -> None:
    path = tmp_path / 'instance.toml'
    path.write_text(INSTANCE_TOML, encoding='utf-8')
    application = load_knapsack_application(path)
    config = MultiobjectiveVNSConfig(
        budget_limits=BudgetLimits(
            max_wall_time_seconds=5,
            max_candidate_attempts=1,
            max_evaluation_requests=1,
        ),
        random_seed=3,
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

    result = search.run(
        initial_solutions=((0, 0, 0),),
        log_directory=tmp_path / 'logs',
    )

    assert result.search_state.pareto_solutions()
    assert result.candidate_outcomes[0].assessment is ApplicationAssessment.VALID
    assert result.event_log_path is not None and result.event_log_path.exists()
    assert result.summary_path is not None and result.summary_path.exists()


def test_p410_example_runs_through_the_application_factory(tmp_path: Path) -> None:
    path = (
        Path(__file__).parents[2] / 'examples' / 'knapsack' / 'p410' / 'instance.toml'
    )
    application = load_knapsack_application(path)
    config = MultiobjectiveVNSConfig(
        budget_limits=BudgetLimits(
            max_wall_time_seconds=5,
            max_candidate_attempts=1,
            max_evaluation_requests=1,
        ),
        random_seed=3,
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

    result = search.run(
        initial_solutions=((0,) * application.instance.n_items,),
        log_directory=tmp_path / 'logs',
    )

    assert result.search_state.pareto_solutions()
    assert result.event_log_path is not None and result.event_log_path.exists()
