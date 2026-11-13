"""Immutable requests, decisions, and observations exchanged with policies.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from multiobjective_vns.config import BudgetLimits
from multiobjective_vns.identifiers import (
    ChoiceId,
    ChoiceKind,
    OperatorGroupId,
    OperatorId,
    SolutionId,
    identifier_text,
)
from multiobjective_vns.objectives import ObjectiveDeclaration, ObjectiveValue
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateOutcome
from multiobjective_vns.records.evaluation import SolutionRecord
from multiobjective_vns.records.failures import ComponentFailure
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    ProcessingStatus,
    SearchDisposition,
)
from multiobjective_vns.values import FiniteReal, finite_real, require_integer


class PoolAction(StrEnum):
    """Reason for asking an exploration policy to choose membership."""

    RECONCILE = 'reconcile'
    CLEANUP = 'cleanup'


class MaintenanceAction(StrEnum):
    """Public state-maintenance operation requested by a caller."""

    CLEAN_ACTIVE_POOL = 'clean_active_pool'
    RESET_FEEDBACK = 'reset_feedback'


class OperatorChoiceCategory(StrEnum):
    """Category selected before feedback chooses an individual choice."""

    STANDALONE = 'standalone'
    COMPOSITION = 'composition'


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    """Identified feasible solution and its complete objective vector."""

    solution_id: SolutionId
    objective_values: tuple[ObjectiveValue, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.solution_id, SolutionId):
            raise TypeError('solution_id must be a SolutionId')
        if not isinstance(self.objective_values, tuple):
            raise TypeError('objective_values must be a tuple')


@dataclass(frozen=True, slots=True)
class ArchiveRequest:
    """Archive membership request under one ordered objective declaration."""

    candidate: ArchiveEntry
    current_archive: tuple[ArchiveEntry, ...]
    objective_declaration: ObjectiveDeclaration

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, ArchiveEntry):
            raise TypeError('candidate must be an ArchiveEntry')
        if not isinstance(self.current_archive, tuple) or any(
            not isinstance(entry, ArchiveEntry) for entry in self.current_archive
        ):
            raise TypeError('current_archive must be a tuple of ArchiveEntry records')
        if not isinstance(self.objective_declaration, ObjectiveDeclaration):
            raise TypeError('objective_declaration must be an ObjectiveDeclaration')
        size = len(self.objective_declaration.objectives)
        if len(self.candidate.objective_values) != size:
            raise ValueError('candidate objective vector has the wrong length')
        if any(len(entry.objective_values) != size for entry in self.current_archive):
            raise ValueError('archive objective vector has the wrong length')
        if any(
            entry.solution_id == self.candidate.solution_id
            for entry in self.current_archive
        ):
            raise ValueError('candidate ID must be distinct from current archive IDs')


@dataclass(frozen=True, slots=True)
class ArchiveDecision:
    """Unique-vector Pareto admission and removal result."""

    admit: bool
    remove_ids: tuple[SolutionId, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.admit, bool):
            raise TypeError('admit must be bool')
        if not isinstance(self.remove_ids, tuple) or any(
            not isinstance(solution_id, SolutionId) for solution_id in self.remove_ids
        ):
            raise TypeError('remove_ids must be a tuple of SolutionId values')
        if len(self.remove_ids) != len(set(self.remove_ids)):
            raise ValueError('remove_ids must be unique')
        if not self.admit and self.remove_ids:
            raise ValueError('a declined archive candidate cannot remove members')


@dataclass(frozen=True, slots=True)
class ActivePoolRequest:
    """Current registry, eligibility boundary, and requested pool action."""

    registry_records: tuple[SolutionRecord, ...]
    eligible_ids: frozenset[SolutionId]
    active_ids: frozenset[SolutionId]
    archive_ids: frozenset[SolutionId]
    action: PoolAction
    changed_solution_id: SolutionId | None

    def __post_init__(self) -> None:
        if not isinstance(self.registry_records, tuple) or any(
            not isinstance(record, SolutionRecord) for record in self.registry_records
        ):
            raise TypeError('registry_records must be a tuple of SolutionRecord values')
        for name in ('eligible_ids', 'active_ids', 'archive_ids'):
            value = getattr(self, name)
            if not isinstance(value, frozenset) or any(
                not isinstance(solution_id, SolutionId) for solution_id in value
            ):
                raise TypeError(f'{name} must be a frozenset of SolutionId values')
        if not isinstance(self.action, PoolAction):
            raise TypeError('action must be a PoolAction')
        record_ids = {record.solution_id for record in self.registry_records}
        if len(record_ids) != len(self.registry_records):
            raise ValueError('registry records must have unique solution IDs')
        if not self.eligible_ids <= record_ids:
            raise ValueError('eligible IDs must identify supplied registry records')
        if not self.active_ids <= self.eligible_ids:
            raise ValueError('active IDs must be eligible')
        if not self.archive_ids <= self.eligible_ids:
            raise ValueError('archive IDs must be eligible')
        if not self.archive_ids <= self.active_ids:
            raise ValueError('archive IDs must already be active')
        if self.action is PoolAction.RECONCILE and self.changed_solution_id is None:
            raise ValueError('reconciliation requires changed_solution_id')
        if self.action is PoolAction.CLEANUP and self.changed_solution_id is not None:
            raise ValueError('cleanup requires changed_solution_id=None')


@dataclass(frozen=True, slots=True)
class ActivePoolDecision:
    """Complete active-pool membership requested by an exploration policy."""

    active_ids: frozenset[SolutionId]

    def __post_init__(self) -> None:
        if not isinstance(self.active_ids, frozenset) or any(
            not isinstance(solution_id, SolutionId) for solution_id in self.active_ids
        ):
            raise TypeError('active_ids must be a frozenset of SolutionId values')


@dataclass(frozen=True, slots=True)
class BaseSelectionRequest:
    """Eligible base records and prior iteration context."""

    eligible_records: tuple[SolutionRecord, ...]
    attempt_counts: Mapping[SolutionId, int]
    previous_base_id: SolutionId | None
    previous_attempt_admitted: bool | None
    random_source: RandomSource

    def __post_init__(self) -> None:
        if not isinstance(self.eligible_records, tuple) or not self.eligible_records:
            raise ValueError('eligible_records must be a nonempty tuple')
        if any(
            not isinstance(record, SolutionRecord) for record in self.eligible_records
        ):
            raise TypeError('eligible_records must contain SolutionRecord values')
        for record in self.eligible_records:
            evaluation = record.evaluation_record
            if (
                evaluation.processing_status is not ProcessingStatus.COMPLETED
                or evaluation.assessment is not ApplicationAssessment.VALID
            ):
                raise ValueError('base records must have a valid feasible assessment')
        eligible_ids = {record.solution_id for record in self.eligible_records}
        counts = dict(self.attempt_counts)
        if set(counts) != eligible_ids:
            raise ValueError('attempt_counts must cover exactly the eligible IDs')
        for count in counts.values():
            require_integer(count, name='attempt count', minimum=0)
        object.__setattr__(self, 'attempt_counts', MappingProxyType(counts))
        if self.previous_base_id is not None and not isinstance(
            self.previous_base_id, SolutionId
        ):
            raise TypeError('previous_base_id must be a SolutionId or None')
        if self.previous_attempt_admitted is not None and not isinstance(
            self.previous_attempt_admitted, bool
        ):
            raise TypeError('previous_attempt_admitted must be bool or None')


@dataclass(frozen=True, slots=True)
class BaseSelectionDecision:
    """The selected base's registry identifier."""

    solution_id: SolutionId

    def __post_init__(self) -> None:
        if not isinstance(self.solution_id, SolutionId):
            raise TypeError('solution_id must be a SolutionId')


@dataclass(frozen=True, slots=True)
class InvocationCounters:
    """Per-invocation work totals used by policy requests and results."""

    candidate_attempts: int = 0
    evaluation_requests: int = 0

    def __post_init__(self) -> None:
        for name in ('candidate_attempts', 'evaluation_requests'):
            require_integer(getattr(self, name), name=name, minimum=0)


@dataclass(frozen=True, slots=True)
class BudgetRequest:
    """Invocation counters, configured ceilings, and elapsed time."""

    counters: InvocationCounters
    limits: BudgetLimits
    elapsed_seconds: FiniteReal

    def __post_init__(self) -> None:
        if not isinstance(self.counters, InvocationCounters):
            raise TypeError('counters must be InvocationCounters')
        if not isinstance(self.limits, BudgetLimits):
            raise TypeError('limits must be BudgetLimits')
        object.__setattr__(
            self,
            'elapsed_seconds',
            finite_real(self.elapsed_seconds, name='elapsed_seconds'),
        )
        if self.elapsed_seconds < 0:
            raise ValueError('elapsed_seconds must be nonnegative')


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """Whether the next search iteration may proceed."""

    allowed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError('allowed must be bool')


@dataclass(frozen=True, slots=True)
class ChoiceDescriptor:
    """Metadata for one eligible standalone Operator or composition choice."""

    choice_id: ChoiceId
    operator_ids: tuple[OperatorId, ...]
    current_k: int
    max_neighborhood_size: int
    feasibility_restoration: bool
    shared_group_ids: frozenset[OperatorGroupId]
    modification_count_definition: str

    def __post_init__(self) -> None:
        if not isinstance(self.choice_id, ChoiceId):
            raise TypeError('choice_id must be a ChoiceId')
        if not isinstance(self.operator_ids, tuple) or len(self.operator_ids) not in (
            1,
            2,
        ):
            raise ValueError('operator_ids must contain one or two IDs')
        if not isinstance(self.shared_group_ids, frozenset):
            raise TypeError('shared_group_ids must be a frozenset')
        for operator_id in self.operator_ids:
            identifier_text(operator_id)
        for group_id in self.shared_group_ids:
            identifier_text(group_id)
        composition = self.choice_id.composition_id
        if self.choice_id.kind.value == 'operator':
            if (
                len(self.operator_ids) != 1
                or self.operator_ids[0] != self.choice_id.operator_id
            ):
                raise ValueError('standalone descriptor must match its choice ID')
            if self.shared_group_ids:
                raise ValueError('standalone choices have no shared groups')
        elif composition is None or self.operator_ids != (
            composition.first_operator_id,
            composition.second_operator_id,
        ):
            raise ValueError('composition descriptor must match its ordered choice ID')
        require_integer(
            self.max_neighborhood_size,
            name='max_neighborhood_size',
            minimum=1,
        )
        require_integer(self.current_k, name='current_k', minimum=1)
        if self.current_k > self.max_neighborhood_size:
            raise ValueError('current_k cannot exceed max_neighborhood_size')
        if not isinstance(self.feasibility_restoration, bool):
            raise TypeError('feasibility_restoration must be bool')
        if (
            not isinstance(self.modification_count_definition, str)
            or not self.modification_count_definition.strip()
        ):
            raise ValueError('modification_count_definition must be nonempty')


@dataclass(frozen=True, slots=True)
class ChoiceSelectionRequest:
    """Eligible choices within the category selected for one iteration."""

    category: OperatorChoiceCategory
    choices: tuple[ChoiceDescriptor, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.category, OperatorChoiceCategory):
            raise TypeError('category must be an OperatorChoiceCategory')
        if not isinstance(self.choices, tuple) or not self.choices:
            raise ValueError('choices must be a nonempty tuple')
        if any(not isinstance(choice, ChoiceDescriptor) for choice in self.choices):
            raise TypeError('choices must contain ChoiceDescriptor values')
        ids = tuple(choice.choice_id for choice in self.choices)
        if len(ids) != len(set(ids)):
            raise ValueError('choice IDs must be unique')
        expected_kind = (
            ChoiceKind.OPERATOR
            if self.category is OperatorChoiceCategory.STANDALONE
            else ChoiceKind.COMPOSITION
        )
        if any(choice.choice_id.kind is not expected_kind for choice in self.choices):
            raise ValueError('every choice must match the request category')


@dataclass(frozen=True, slots=True)
class FeedbackObservation:
    """Outcomes, modification counts, and failures from one committed attempt."""

    choice_id: ChoiceId
    k: int
    modification_counts: tuple[int, ...] | None
    outcomes: tuple[CandidateOutcome, ...]
    failures: tuple[ComponentFailure, ...]
    archive_admission_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.choice_id, ChoiceId):
            raise TypeError('choice_id must be a ChoiceId')
        require_integer(self.k, name='k', minimum=1)
        if self.modification_counts is not None:
            if not isinstance(self.modification_counts, tuple):
                raise TypeError('modification_counts must be a tuple or None')
            for count in self.modification_counts:
                require_integer(count, name='modification count', minimum=1)
                if count > self.k:
                    raise ValueError('modification counts cannot exceed k')
        if not isinstance(self.outcomes, tuple) or any(
            not isinstance(outcome, CandidateOutcome) for outcome in self.outcomes
        ):
            raise TypeError('outcomes must be a tuple of CandidateOutcome records')
        if any(outcome.choice_id != self.choice_id for outcome in self.outcomes):
            raise ValueError('feedback outcomes must match the observed choice')
        if self.modification_counts is None and self.outcomes:
            raise ValueError('a failed choice call has no candidate outcomes')
        indexes = tuple(outcome.candidate_index for outcome in self.outcomes)
        if any(index is None for index in indexes) or indexes != tuple(
            range(1, len(indexes) + 1)
        ):
            raise ValueError('feedback outcomes must have ordered candidate indexes')
        if self.modification_counts is not None and len(self.outcomes) > len(
            self.modification_counts
        ):
            raise ValueError('outcomes cannot exceed returned candidate proposals')
        if not isinstance(self.failures, tuple) or any(
            not isinstance(failure, ComponentFailure) for failure in self.failures
        ):
            raise TypeError('failures must be a tuple of ComponentFailure records')
        require_integer(
            self.archive_admission_count,
            name='archive_admission_count',
            minimum=0,
        )
        actual_admissions = sum(
            outcome.disposition is SearchDisposition.ARCHIVED
            for outcome in self.outcomes
        )
        if self.archive_admission_count != actual_admissions:
            raise ValueError('archive_admission_count must match archived outcomes')


__all__ = [
    'ActivePoolDecision',
    'ActivePoolRequest',
    'ArchiveDecision',
    'ArchiveEntry',
    'ArchiveRequest',
    'BaseSelectionDecision',
    'BaseSelectionRequest',
    'BudgetDecision',
    'BudgetRequest',
    'ChoiceDescriptor',
    'ChoiceSelectionRequest',
    'FeedbackObservation',
    'InvocationCounters',
    'MaintenanceAction',
    'PoolAction',
]
