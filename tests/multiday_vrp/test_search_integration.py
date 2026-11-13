"""Integration tests for VRP catalog assembly and representative searches.

Created: 2026-11-03T23:47:20+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from multiobjective_vns import (
    BudgetLimits,
    MultiobjectiveVNS,
    MultiobjectiveVNSConfig,
    TerminationReason,
)
from multiobjective_vns.callbacks import SolutionIdentity
from multiobjective_vns.candidate_batch import CandidateBatchAdapter
from multiobjective_vns.identifiers import (
    ComponentConfigurationId,
    ComponentId,
    ProblemInstanceId,
)
from multiobjective_vns.multiday_vrp import (
    VrpApplicationEvaluator,
    VrpOperatorId,
    VrpSolutionCodec,
    build_default_vrp_operator_catalog,
    build_vrp_objective_declaration,
    load_vrp_instance,
)
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators import (
    MergeToursOperator,
    SplitTourOperator,
)
from multiobjective_vns.multiday_vrp.operators.common import VrpPlanAssessor
from multiobjective_vns.multiday_vrp.operators.route_order import TwoOptOperator
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.records.policies import InvocationCounters
from multiobjective_vns.records.status import ApplicationAssessment, SearchDisposition
from multiobjective_vns.registrations import (
    OperatorCompositionRegistration,
    OperatorRegistration,
)

from .input_fixtures import write_minimal_vrp_bundle
from .test_universal_route_order import _test_plan


class _StructuralIdentity:
    component_id: ComponentId = 'test:vrp-structural-identity'
    configuration_id: ComponentConfigurationId = 'test:vrp-structural-identity-v1'

    def equivalent(self, left: object, right: object) -> bool:
        return (
            type(left) is VrpSolution and type(right) is VrpSolution and left == right
        )


class _ZeroRandomSource:
    component_id: ComponentId = 'test:vrp-random-source'
    configuration_id: ComponentConfigurationId = 'test:vrp-random-source-v1'

    def random(self) -> float:
        return 0.0

    def capture_state(self) -> object:
        return None

    def restore_state(self, payload: object) -> None:
        if payload is not None:
            raise TypeError('the stateless random source accepts only None')


def _assessor(evaluator: VrpApplicationEvaluator) -> VrpPlanAssessor:
    def assess(
        _instance: LoadedVrpInstance,
        objectives: ObjectiveDeclaration,
        solution: VrpSolution,
    ) -> ApplicationEvaluation:
        return evaluator.evaluate(solution, objectives)

    return cast(VrpPlanAssessor, assess)


def _operator_implementation(operator: object) -> object:
    if isinstance(operator, CandidateBatchAdapter):
        return operator.delegate
    return operator


def _search(
    instance: LoadedVrpInstance,
    registrations: tuple[OperatorRegistration, ...],
    *,
    config: MultiobjectiveVNSConfig,
    pre_registered_compositions: tuple[OperatorCompositionRegistration, ...] = (),
) -> MultiobjectiveVNS:
    return MultiobjectiveVNS(
        solution_codec=VrpSolutionCodec(),
        solution_identity=cast(SolutionIdentity, _StructuralIdentity()),
        objective_declaration=build_vrp_objective_declaration(instance),
        operators=registrations,
        pre_registered_compositions=pre_registered_compositions,
        application_evaluator=VrpApplicationEvaluator(instance),
        problem_instance_id=cast(ProblemInstanceId, instance.instance_id),
        config=config,
        random_source=cast(RandomSource, _ZeroRandomSource()),
        promoted_composition_catalog_path=None,
    )


def _one_attempt_config() -> MultiobjectiveVNSConfig:
    return MultiobjectiveVNSConfig(
        max_candidates_per_attempt=1,
        max_neighborhood_size=1,
        feedback_selection_mode='uniform',
        random_seed=0,
        budget_limits=BudgetLimits(
            max_wall_time_seconds=10.0,
            max_candidate_attempts=1,
            max_evaluation_requests=1,
        ),
    )


def _operator_spec(registration: OperatorRegistration) -> str:
    return cast(VrpOperatorId, registration.operator_id).operator_spec_id


def test_simple_instance_factory_and_search_use_only_universal_choices(
    tmp_path: Path,
) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))
    objectives = build_vrp_objective_declaration(instance)
    evaluator = VrpApplicationEvaluator(instance)
    catalog = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id=cast(ProblemInstanceId, instance.instance_id),
        objective_declaration=objectives,
        assess_plan=_assessor(evaluator),
    )
    registrations = catalog.operators

    specs = {_operator_spec(item) for item in registrations}
    assert 'vrp.generic.two_opt' in specs
    assert 'vrp.generic.route_leg_reroute' in specs
    assert not any('.package_' in spec for spec in specs)
    assert not any('.service_windows.' in spec for spec in specs)

    search = _search(
        instance,
        registrations,
        config=_one_attempt_config(),
        pre_registered_compositions=catalog.pre_registered_compositions,
    )
    result = search.run(
        initial_solutions=(VrpSolution((), ()),),
        log_directory=tmp_path / 'simple-logs',
    )

    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert result.counters == InvocationCounters(
        candidate_attempts=1, evaluation_requests=0
    )
    assert len(result.candidate_outcomes) == 1
    assert result.candidate_outcomes[0].assessment is ApplicationAssessment.VALID
    assert result.diagnostics == ()


def test_richer_instance_enables_feature_choices_and_runs_a_catalog_operator(
    tmp_path: Path,
) -> None:
    instance, _objectives, initial = _test_plan(tmp_path)
    vehicles_path = tmp_path / 'instance' / 'vehicles.csv'
    vehicle_text = vehicles_path.read_text(encoding='utf-8')
    vehicles_path.write_text(
        vehicle_text.replace(',depot-a,1\n', ',depot-a,2\n'), encoding='utf-8'
    )
    instance = load_vrp_instance(tmp_path / 'instance' / 'instance.toml')
    assert 'multiple_tours_per_day' in instance.feature_profile.active_features

    objectives = build_vrp_objective_declaration(instance)
    evaluator = VrpApplicationEvaluator(instance)
    catalog = build_default_vrp_operator_catalog(
        instance,
        problem_instance_id=cast(ProblemInstanceId, instance.instance_id),
        objective_declaration=objectives,
        assess_plan=_assessor(evaluator),
    )
    registrations = catalog.operators
    specs = {_operator_spec(item) for item in registrations}
    assert 'vrp.multiple_tours.split_tour' in specs
    assert 'vrp.multiple_tours.merge_tours' in specs
    assert 'vrp.multiple_tours.tour_boundary_change' not in specs
    split_registrations = tuple(
        item
        for item in registrations
        if _operator_spec(item) == 'vrp.multiple_tours.split_tour'
    )
    merge_registrations = tuple(
        item
        for item in registrations
        if _operator_spec(item) == 'vrp.multiple_tours.merge_tours'
    )
    assert split_registrations
    assert merge_registrations
    assert all(
        isinstance(_operator_implementation(item.operator), SplitTourOperator)
        for item in split_registrations
    )
    assert all(
        isinstance(_operator_implementation(item.operator), MergeToursOperator)
        for item in merge_registrations
    )
    assert 'vrp.depot_visits.depot_visit_insertion' in specs
    assert 'vrp.depot_visits.depot_visit_removal' in specs

    two_opt_registration = next(
        item for item in registrations if _operator_spec(item) == 'vrp.generic.two_opt'
    )
    assert isinstance(
        _operator_implementation(two_opt_registration.operator), TwoOptOperator
    )
    search = _search(
        instance,
        (two_opt_registration,),
        config=_one_attempt_config(),
    )
    result = search.run(
        initial_solutions=(initial,),
        log_directory=tmp_path / 'rich-logs',
    )

    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert result.counters.candidate_attempts == 1
    assert len(result.candidate_outcomes) == 2
    assert all(
        outcome.assessment is ApplicationAssessment.VALID
        for outcome in result.candidate_outcomes
    )
    assert all(outcome.failures == () for outcome in result.candidate_outcomes)
    assert result.diagnostics == ()
    assert result.candidate_outcomes[1].disposition is SearchDisposition.ARCHIVED
    assert len(result.search_state.visited_solutions()) == 2
    assert len(result.search_state.pareto_solutions()) == 2
