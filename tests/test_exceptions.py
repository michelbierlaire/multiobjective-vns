"""Tests for public setup, resume, and initialization exceptions.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from pathlib import Path
from uuid import UUID

import pytest

from multiobjective_vns.exceptions import (
    CheckpointRestoreError,
    ComponentRegistrationError,
    ComponentSetupError,
    InitialSolutionsError,
    LogFinalizationError,
    ResumeCompatibilityError,
)
from multiobjective_vns.identifiers import InvocationId
from multiobjective_vns.records.failures import ComponentFailure, FailureKind


def make_failure() -> ComponentFailure:
    return ComponentFailure(
        FailureKind.CALLBACK_EXCEPTION,
        'component',
        'checkpoint_restore',
        'restore callback raised',
    )


def test_registration_and_resume_errors_have_public_types() -> None:
    assert issubclass(ComponentRegistrationError, ValueError)
    assert issubclass(ResumeCompatibilityError, ValueError)
    assert issubclass(InitialSolutionsError, ValueError)


def test_setup_and_checkpoint_errors_preserve_diagnostics() -> None:
    failure = make_failure()
    setup_error = ComponentSetupError(failure)
    restore_error = CheckpointRestoreError('checkpoint-ref', failure)

    assert setup_error.failure == failure
    assert restore_error.checkpoint_reference == 'checkpoint-ref'
    assert restore_error.failure == failure


def test_log_and_initialization_errors_expose_partial_results() -> None:
    failure = make_failure()
    log_error = LogFinalizationError(
        failure=failure,
        event_log_path=Path('events.jsonl'),
        summary_path=None,
        registry_view_path=Path('registry-view.json'),
        pareto_view_path=None,
        executive_summary=None,
        report_diagnostics=(),
    )
    initial_error = InitialSolutionsError(
        invocation_id=InvocationId(UUID(int=1)),
        candidate_outcomes=(),
        diagnostics=(failure,),
        registry_view_path=Path('registry-view.json'),
    )

    assert log_error.event_log_path == Path('events.jsonl')
    assert log_error.registry_view_path == Path('registry-view.json')
    assert initial_error.diagnostics == (failure,)
    assert initial_error.registry_view_path == Path('registry-view.json')
    with pytest.raises(InitialSolutionsError):
        raise initial_error
