"""Tests for immutable solution identity resolution and registry snapshots.

Created: 2026-10-29T13:52:20+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import replace
from typing import cast

import pytest

from multiobjective_vns.exceptions import ComponentSetupError
from multiobjective_vns.identifiers import SolutionId
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
    SolutionRecord,
)
from multiobjective_vns.records.failures import FailureKind
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    ArchiveStatus,
    ProcessingStatus,
)
from multiobjective_vns.registry import SolutionRegistry


class EqualityIdentity:
    component_id = 'identity'
    configuration_id = 'identity-v1'

    def equivalent(self, left: object, right: object) -> bool:
        return left == right


class KeyedIdentity(EqualityIdentity):
    def equivalent(self, left: object, right: object) -> bool:
        return (
            cast(tuple[object, object], left)[1]
            == cast(tuple[object, object], right)[1]
        )

    def lookup_key(self, solution: object) -> str:
        return cast(tuple[str, int], solution)[0]


class RaisingIdentity(EqualityIdentity):
    def equivalent(self, left: object, right: object) -> bool:
        raise RuntimeError('identity callback failed')


class RaisingKeyIdentity(KeyedIdentity):
    def lookup_key(self, solution: object) -> str:
        raise RuntimeError('lookup key failed')


def valid_record(solution_id: SolutionId, solution: object) -> SolutionRecord:
    evaluation = ApplicationEvaluation(True, True, (1, 2))
    return SolutionRecord(
        solution_id,
        solution,
        EvaluationRecord(
            ProcessingStatus.COMPLETED,
            evaluation,
            ApplicationAssessment.VALID,
        ),
    )


def test_registry_returns_immutable_snapshots_and_reuses_equal_solutions() -> None:
    registry = SolutionRegistry(EqualityIdentity())
    first = valid_record(SolutionId(0), ('a',))
    updated = registry.with_record(first)

    assert registry.records == ()
    assert updated.records == (first,)
    resolution = updated.resolve(('a',))
    assert resolution.solution_id == first.solution_id
    assert resolution.failure is None


def test_keyed_registry_keeps_distinct_solutions_with_a_shared_key() -> None:
    registry = SolutionRegistry(KeyedIdentity())
    first = valid_record(SolutionId(0), ('shared', 1))
    registry = registry.with_record(first, lookup_key='shared')

    resolution = registry.resolve(('shared', 2))

    assert resolution.solution_id is None
    assert resolution.failure is None
    second = valid_record(SolutionId(1), ('shared', 2))
    registry = registry.with_record(second, lookup_key='shared')
    assert registry.records == (first, second)
    assert registry.resolve(('shared', 2)).solution_id == second.solution_id


def test_record_update_preserves_lookup_index_and_prior_snapshot() -> None:
    first = valid_record(SolutionId(0), ('shared', 1))
    registry = SolutionRegistry(KeyedIdentity()).with_record(
        first,
        lookup_key='shared',
    )

    updated = registry.with_updated_record(
        replace(first, archive_status=ArchiveStatus.PARETO_OPTIMAL)
    )

    assert registry.get(first.solution_id).archive_status is None
    assert updated.get(first.solution_id).archive_status is ArchiveStatus.PARETO_OPTIMAL
    assert updated.resolve(('shared', 1)).solution_id == first.solution_id


def test_exhaustive_key_validation_resolves_mismatched_key_as_duplicate() -> None:
    registry = SolutionRegistry(KeyedIdentity(), exhaustive_identity_validation=True)
    first = valid_record(SolutionId(0), ('old-key', 1))
    registry = registry.with_record(first, lookup_key='old-key')

    resolution = registry.resolve(('new-key', 1))

    assert resolution.solution_id == first.solution_id
    assert resolution.failure is not None
    assert resolution.failure.kind is FailureKind.CONTRACT_VIOLATION
    assert resolution.failure.solution_id == first.solution_id
    assert resolution.failure.phase == 'identity_resolution'


def test_identity_callback_failure_leaves_the_submission_unresolved() -> None:
    registry = SolutionRegistry(RaisingIdentity())
    registry = registry.with_record(valid_record(SolutionId(0), ('known',)))

    resolution = registry.resolve(('new',))

    assert resolution.solution_id is None
    assert resolution.failure is not None
    assert resolution.failure.kind is FailureKind.CALLBACK_EXCEPTION
    assert resolution.failure.source == 'identity'
    assert registry.records[0].solution_id == SolutionId(0)


def test_registry_rejects_duplicate_solution_ids() -> None:
    record = valid_record(SolutionId(0), ('known',))
    with pytest.raises(ValueError, match='unique'):
        SolutionRegistry(EqualityIdentity(), records=(record, record))


def test_rebuilding_key_index_raises_setup_error_with_restore_phase() -> None:
    record = valid_record(SolutionId(4), ('key', 1))

    with pytest.raises(ComponentSetupError) as error:
        SolutionRegistry(RaisingKeyIdentity(), records=(record,))

    assert error.value.failure.phase == 'identity_index_rebuild'
    assert error.value.failure.solution_id == record.solution_id
