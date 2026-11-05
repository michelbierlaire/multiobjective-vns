"""Identity resolution and immutable snapshots of visited solutions.

Created: 2026-10-29T13:53:30+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, replace
from types import MappingProxyType

from multiobjective_vns.callbacks import SolutionIdentity
from multiobjective_vns.exceptions import (
    ComponentRegistrationError,
    ComponentSetupError,
)
from multiobjective_vns.identifiers import ComponentId, SolutionId, identifier_text
from multiobjective_vns.records.evaluation import EvaluationRecord, SolutionRecord
from multiobjective_vns.records.failures import ComponentFailure, FailureKind

_UNSET = object()


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    """Resolved registry identity or its candidate-scoped diagnostic."""

    solution_id: SolutionId | None
    lookup_key: Hashable | None
    has_lookup_key: bool
    failure: ComponentFailure | None = None


class SolutionRegistry:
    """Immutable visited-solution registry with optional keyed lookup.

    :param solution_identity: Application equivalence rule for solutions.
    :param records: Existing records used to restore a registry snapshot.
    :param exhaustive_identity_validation: Compare keyed submissions to all
        records in addition to checking the matching key bucket.
    :raises ComponentRegistrationError: If the identity callback is malformed.
    :raises ComponentSetupError: If rebuilding existing lookup keys fails.
    """

    __slots__ = (
        '_identity',
        '_exhaustive_identity_validation',
        '_has_lookup_key',
        '_records',
        '_records_by_id',
        '_lookup_index',
        '_lookup_keys_by_id',
        '_next_sequence',
    )

    def __init__(
        self,
        solution_identity: SolutionIdentity,
        *,
        records: tuple[SolutionRecord, ...] = (),
        exhaustive_identity_validation: bool = False,
    ) -> None:
        if not isinstance(exhaustive_identity_validation, bool):
            raise TypeError('exhaustive_identity_validation must be bool')
        if not isinstance(records, tuple) or any(
            not isinstance(record, SolutionRecord) for record in records
        ):
            raise TypeError('records must be a tuple of SolutionRecord values')

        try:
            if not callable(getattr(solution_identity, 'equivalent', None)):
                raise TypeError('must provide equivalent(left, right)')
            identifier_text(solution_identity.component_id)
            identifier_text(solution_identity.configuration_id)
            lookup_key_callback = getattr(solution_identity, 'lookup_key', None)
            if lookup_key_callback is not None and not callable(lookup_key_callback):
                raise TypeError('lookup_key must be callable when provided')
        except Exception as error:
            raise ComponentRegistrationError(
                f'SolutionIdentity registration is invalid: {error}'
            ) from error

        records_by_id = {record.solution_id: record for record in records}
        if len(records_by_id) != len(records):
            raise ValueError('SolutionId values must be unique')

        self._identity = solution_identity
        self._exhaustive_identity_validation = exhaustive_identity_validation
        self._has_lookup_key = lookup_key_callback is not None
        self._records = records
        self._records_by_id = MappingProxyType(records_by_id)
        self._next_sequence = (
            max((record.solution_id.sequence for record in records), default=-1) + 1
        )

        lookup_keys_by_id: dict[SolutionId, Hashable] = {}
        lookup_index: dict[Hashable, list[SolutionId]] = {}
        if self._has_lookup_key:
            for record in records:
                key, failure = self._compute_lookup_key(record.solution)
                if failure is not None:
                    raise ComponentSetupError(
                        replace(
                            failure,
                            phase='identity_index_rebuild',
                            solution_id=record.solution_id,
                        )
                    )
                try:
                    lookup_keys_by_id[record.solution_id] = key
                    lookup_index.setdefault(key, []).append(record.solution_id)
                except Exception as error:
                    raise ComponentSetupError(
                        self._failure(
                            FailureKind.CONTRACT_VIOLATION,
                            'identity_index_rebuild',
                            f'lookup key cannot index a restored solution: {error}',
                            record.solution_id,
                        )
                    ) from error
        self._lookup_index = MappingProxyType(
            {key: tuple(ids) for key, ids in lookup_index.items()}
        )
        self._lookup_keys_by_id = MappingProxyType(lookup_keys_by_id)

    @property
    def records(self) -> tuple[SolutionRecord, ...]:
        """Return registry records in insertion order."""
        return self._records

    @property
    def next_sequence(self) -> int:
        """Return the sequence number for the next package-generated ID."""
        return self._next_sequence

    @property
    def identity_component_id(self) -> ComponentId:
        """Return the registered identity component's diagnostic source ID."""
        return self._identity.component_id

    def get(self, solution_id: SolutionId) -> SolutionRecord:
        """Return a record by ID.

        :raises KeyError: If ``solution_id`` is not registered.
        """
        return self._records_by_id[solution_id]

    def resolve(self, solution: object) -> IdentityResolution:
        """Resolve a submitted solution against records in insertion order.

        Keyed lookup checks the matching bucket. With exhaustive validation
        enabled, it also compares every remaining record and reports a key
        mismatch while preserving the existing identity.
        """
        lookup_key: Hashable | None = None
        candidate_ids = {record.solution_id for record in self._records}
        if self._has_lookup_key:
            lookup_key, failure = self._compute_lookup_key(solution)
            if failure is not None:
                return IdentityResolution(None, None, True, failure)
            try:
                candidate_ids = set(self._lookup_index.get(lookup_key, ()))
            except Exception as error:
                return IdentityResolution(
                    None,
                    lookup_key,
                    True,
                    self._failure(
                        FailureKind.CONTRACT_VIOLATION,
                        'identity_resolution',
                        f'lookup key could not be used for lookup: {error}',
                    ),
                )
            if self._exhaustive_identity_validation:
                candidate_ids = {record.solution_id for record in self._records}

        matches: list[SolutionRecord] = []
        mismatched_key = False
        for record in self._records:
            if record.solution_id not in candidate_ids:
                continue
            try:
                equivalent = self._identity.equivalent(solution, record.solution)
            except Exception as error:
                return IdentityResolution(
                    None,
                    lookup_key,
                    self._has_lookup_key,
                    self._failure(
                        FailureKind.CALLBACK_EXCEPTION,
                        'identity_resolution',
                        f'SolutionIdentity.equivalent raised: {error}',
                    ),
                )
            if not isinstance(equivalent, bool):
                return IdentityResolution(
                    None,
                    lookup_key,
                    self._has_lookup_key,
                    self._failure(
                        FailureKind.CONTRACT_VIOLATION,
                        'identity_resolution',
                        'SolutionIdentity.equivalent must return bool',
                    ),
                )
            if equivalent:
                matches.append(record)
                if self._has_lookup_key:
                    try:
                        keys_match = (
                            self._lookup_keys_by_id[record.solution_id] == lookup_key
                        )
                        if not isinstance(keys_match, bool):
                            raise TypeError('lookup-key equality must return bool')
                        if not keys_match:
                            mismatched_key = True
                    except Exception as error:
                        return IdentityResolution(
                            None,
                            lookup_key,
                            True,
                            self._failure(
                                FailureKind.CONTRACT_VIOLATION,
                                'identity_resolution',
                                f'lookup key comparison failed: {error}',
                                record.solution_id,
                            ),
                        )

        if matches:
            existing_id = matches[0].solution_id
            identity_failure: ComponentFailure | None = None
            if mismatched_key:
                identity_failure = self._failure(
                    FailureKind.CONTRACT_VIOLATION,
                    'identity_resolution',
                    'equivalent solutions received different lookup keys',
                    existing_id,
                )
            elif len(matches) > 1:
                identity_failure = self._failure(
                    FailureKind.CONTRACT_VIOLATION,
                    'identity_resolution',
                    'one solution is equivalent to multiple registered IDs',
                    existing_id,
                )
            return IdentityResolution(
                existing_id,
                lookup_key,
                self._has_lookup_key,
                identity_failure,
            )
        return IdentityResolution(None, lookup_key, self._has_lookup_key)

    def with_record(
        self,
        record: SolutionRecord,
        *,
        lookup_key: object = _UNSET,
    ) -> SolutionRegistry:
        """Return a new snapshot containing ``record``.

        The caller supplies the key already computed for a new record. Registry
        snapshots keep the previous record tuple and lookup index unchanged.
        """
        if not isinstance(record, SolutionRecord):
            raise TypeError('record must be a SolutionRecord')
        if record.solution_id in self._records_by_id:
            raise ValueError('SolutionId values must be unique')
        resolved_lookup_key: Hashable | None = None
        if self._has_lookup_key:
            if lookup_key is _UNSET:
                raise ValueError('a keyed registry requires the resolved lookup key')
            resolved_lookup_key = self._validate_lookup_key(lookup_key)

        new = object.__new__(SolutionRegistry)
        new._identity = self._identity
        new._exhaustive_identity_validation = self._exhaustive_identity_validation
        new._has_lookup_key = self._has_lookup_key
        new._records = (*self._records, record)
        new._records_by_id = MappingProxyType(
            {**self._records_by_id, record.solution_id: record}
        )
        new._next_sequence = max(self._next_sequence, record.solution_id.sequence + 1)
        keys = dict(self._lookup_keys_by_id)
        index = {key: list(ids) for key, ids in self._lookup_index.items()}
        if self._has_lookup_key:
            keys[record.solution_id] = resolved_lookup_key
            try:
                index.setdefault(resolved_lookup_key, []).append(record.solution_id)
            except Exception as error:
                raise ValueError(
                    f'lookup key cannot index a solution: {error}'
                ) from error
        new._lookup_keys_by_id = MappingProxyType(keys)
        new._lookup_index = MappingProxyType(
            {key: tuple(ids) for key, ids in index.items()}
        )
        return new

    def register(
        self,
        solution: object,
        evaluation_record: EvaluationRecord,
        *,
        lookup_key: object = _UNSET,
    ) -> tuple[SolutionRegistry, SolutionRecord]:
        """Assign the next package ID and return a registry snapshot and record."""
        record = SolutionRecord(
            solution_id=SolutionId(self._next_sequence),
            solution=solution,
            evaluation_record=evaluation_record,
        )
        return self.with_record(record, lookup_key=lookup_key), record

    def with_updated_record(self, record: SolutionRecord) -> SolutionRegistry:
        """Return a snapshot with an existing record replaced by ID."""
        if not isinstance(record, SolutionRecord):
            raise TypeError('record must be a SolutionRecord')
        if record.solution_id not in self._records_by_id:
            raise KeyError(record.solution_id)
        new = object.__new__(SolutionRegistry)
        new._identity = self._identity
        new._exhaustive_identity_validation = self._exhaustive_identity_validation
        new._has_lookup_key = self._has_lookup_key
        new._records = tuple(
            record if existing.solution_id == record.solution_id else existing
            for existing in self._records
        )
        new._records_by_id = MappingProxyType(
            {**self._records_by_id, record.solution_id: record}
        )
        new._lookup_index = self._lookup_index
        new._lookup_keys_by_id = self._lookup_keys_by_id
        new._next_sequence = self._next_sequence
        return new

    def _compute_lookup_key(
        self, solution: object
    ) -> tuple[Hashable | None, ComponentFailure | None]:
        try:
            lookup_key = getattr(self._identity, 'lookup_key')
            if not callable(lookup_key):
                raise TypeError('lookup_key must be callable')
            key = lookup_key(solution)
        except Exception as error:
            return None, self._failure(
                FailureKind.CALLBACK_EXCEPTION,
                'identity_resolution',
                f'SolutionIdentity.lookup_key raised: {error}',
            )
        try:
            return self._validate_lookup_key(key), None
        except Exception as error:
            return None, self._failure(
                FailureKind.CONTRACT_VIOLATION,
                'identity_resolution',
                f'SolutionIdentity.lookup_key returned an invalid key: {error}',
            )

    @staticmethod
    def _validate_lookup_key(key: object) -> Hashable:
        try:
            hash(key)
        except Exception as error:
            raise TypeError('lookup keys must be hashable') from error
        try:
            reflexive = key == key
        except Exception as error:
            raise ValueError('lookup-key equality failed') from error
        if not isinstance(reflexive, bool) or not reflexive:
            raise ValueError('lookup-key equality must be Boolean and reflexive')
        return key

    def _failure(
        self,
        kind: FailureKind,
        phase: str,
        reason: str,
        solution_id: SolutionId | None = None,
    ) -> ComponentFailure:
        return ComponentFailure(
            kind=kind,
            source=self._identity.component_id,
            phase=phase,
            reason=reason or 'identity component failed without a detail',
            solution_id=solution_id,
        )


__all__ = ['IdentityResolution', 'SolutionRegistry']
