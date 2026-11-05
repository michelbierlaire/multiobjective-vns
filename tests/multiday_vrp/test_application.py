"""Tests for assembling a complete VRP application for generic VNS.

Created: 2026-11-03T23:54:30+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path

import pytest

from multiobjective_vns import (
    BudgetLimits,
    MultiobjectiveVNS,
    MultiobjectiveVNSConfig,
    TerminationReason,
)
from multiobjective_vns.multiday_vrp import (
    VrpApplication,
    VrpApplicationEvaluator,
    VrpExtensionRegistry,
    VrpOperatorCatalogSettings,
    VrpOperatorId,
    VrpSolution,
    VrpSolutionCodec,
    VrpSolutionIdentity,
    build_vrp_application,
    load_vrp_application,
    load_vrp_instance,
)
from multiobjective_vns.records.status import ApplicationAssessment
from multiobjective_vns.registrations import OperatorGroup

from .input_fixtures import write_minimal_vrp_bundle


def _search(application: VrpApplication) -> MultiobjectiveVNS:
    return MultiobjectiveVNS(
        solution_codec=application.solution_codec,
        solution_identity=application.solution_identity,
        objective_declaration=application.objective_declaration,
        operators=application.operators,
        application_evaluator=application.application_evaluator,
        problem_instance_id=application.problem_instance_id,
        config=MultiobjectiveVNSConfig(
            budget_limits=BudgetLimits(
                max_wall_time_seconds=10.0,
                max_candidate_attempts=1,
                max_evaluation_requests=1,
            ),
            random_seed=11,
        ),
        operator_groups=application.operator_groups,
    )


def test_application_loading_assembles_a_searchable_vrp(
    tmp_path: Path,
) -> None:
    application = load_vrp_application(write_minimal_vrp_bundle(tmp_path))

    assert isinstance(application, VrpApplication)
    assert isinstance(application.solution_codec, VrpSolutionCodec)
    assert isinstance(application.solution_identity, VrpSolutionIdentity)
    assert isinstance(application.application_evaluator, VrpApplicationEvaluator)
    assert application.problem_instance_id == application.instance.instance_id
    assert application.operators
    assert application.operator_groups == ()

    result = _search(application).run(
        initial_solutions=(VrpSolution((), ()),),
        log_directory=tmp_path / 'logs',
    )

    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert result.candidate_outcomes[0].assessment is ApplicationAssessment.VALID
    assert result.diagnostics == ()


def test_builder_binds_registry_problem_identity_settings_and_groups(
    tmp_path: Path,
) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    registry = VrpExtensionRegistry('example-registry', 'example-registry-v1')
    group = OperatorGroup(
        'general-moves',
        frozenset(
            {
                VrpOperatorId('vrp.generic.two_opt', None),
                VrpOperatorId('vrp.generic.relocate', None),
            }
        ),
    )
    application = load_vrp_application(
        manifest,
        problem_instance_id='bound-minimal-instance',
        extension_registry=registry,
        settings=VrpOperatorCatalogSettings(max_neighborhood_size=2),
        operator_groups=(group,),
    )

    assert application.problem_instance_id == 'bound-minimal-instance'
    assert application.operator_groups == (group,)
    assert 'example-registry:example-registry-v1' in (
        application.application_evaluator.configuration_id
    )
    assert all(item.max_neighborhood_size == 2 for item in application.operators)


def test_build_application_rejects_operator_groups_with_unknown_members(
    tmp_path: Path,
) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))

    with pytest.raises(ValueError, match='registered Operators'):
        build_vrp_application(
            instance,
            operator_groups=(
                OperatorGroup(
                    'unknown-members',
                    frozenset({'not-registered-a', 'not-registered-b'}),
                ),
            ),
        )
