"""Structured executive summaries and run-provenance records.

Created: 2026-10-29T13:46:09+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from multiobjective_vns.config import FeedbackSelectionMode
from multiobjective_vns.identifiers import (
    ChoiceId,
    ComponentConfigurationId,
    ComponentId,
    CompositionId,
    InvocationId,
    ObjectiveId,
    OperatorGroupId,
    OperatorId,
    SolutionId,
    identifier_text,
)
from multiobjective_vns.objectives import ObjectiveValue
from multiobjective_vns.records.failures import (
    FailureKind,
    FailurePhase,
)
from multiobjective_vns.records.policies import InvocationCounters
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    SearchDisposition,
    TerminationReason,
)
from multiobjective_vns.values import FiniteReal, finite_real, require_integer

if TYPE_CHECKING:
    from multiobjective_vns.registrations import OperatorGroup

InvocationKind = Literal['search', 'maintenance']


class ReportDiagnosticKind(StrEnum):
    """Reporting callback failure categories."""

    SOLUTION_DESCRIPTION_FAILURE = 'solution_description_failure'
    OBJECTIVE_NAME_FAILURE = 'objective_name_failure'
    OBJECTIVE_VALUE_FORMAT_FAILURE = 'objective_value_format_failure'


@dataclass(frozen=True, slots=True)
class ReportDiagnostic:
    """Nonfatal rendering failure that leaves the search result intact."""

    kind: ReportDiagnosticKind
    source: ComponentId
    solution_id: SolutionId | None
    objective_id: ObjectiveId | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ReportDiagnosticKind):
            raise TypeError('kind must be a ReportDiagnosticKind')
        identifier_text(self.source)
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError('reason must be nonempty')
        if self.kind is ReportDiagnosticKind.SOLUTION_DESCRIPTION_FAILURE:
            if self.solution_id is None or self.objective_id is not None:
                raise ValueError(
                    'solution-description diagnostics identify one solution'
                )
        elif self.objective_id is None:
            raise ValueError('objective diagnostics identify one objective')
        if self.kind is ReportDiagnosticKind.OBJECTIVE_NAME_FAILURE:
            if self.solution_id is not None:
                raise ValueError('objective-name failures have no solution ID')
        elif self.solution_id is None:
            raise ValueError('objective-value failures identify one solution')


@dataclass(frozen=True, slots=True)
class ParetoSolutionReport:
    """One final Pareto solution and its objective values for reporting."""

    solution_id: SolutionId
    objective_values: tuple[ObjectiveValue, ...]
    description: str | None


@dataclass(frozen=True, slots=True)
class ModificationCountFrequency:
    """Count of committed attempts with one observed modification count."""

    m: int | None
    count: int

    def __post_init__(self) -> None:
        if self.m is not None:
            require_integer(self.m, name='m', minimum=0)
        require_integer(self.count, name='count', minimum=0)


@dataclass(frozen=True, slots=True)
class ChoiceStatistics:
    """Committed attempt, archive, and reward statistics for one choice size."""

    choice_id: ChoiceId
    k: int
    modification_count_definition: str
    attempt_count: int
    archive_admission_count: int
    archive_reward_total: FiniteReal
    modification_count_frequencies: tuple[ModificationCountFrequency, ...]

    def __post_init__(self) -> None:
        require_integer(self.k, name='k', minimum=1)
        require_integer(self.attempt_count, name='attempt_count', minimum=0)
        require_integer(
            self.archive_admission_count,
            name='archive_admission_count',
            minimum=0,
        )
        if self.archive_admission_count > self.attempt_count:
            raise ValueError('archive admissions cannot exceed attempts')
        reward_total = finite_real(
            self.archive_reward_total, name='archive_reward_total'
        )
        if not 0.0 <= reward_total <= self.attempt_count:
            raise ValueError('archive_reward_total must be between zero and attempts')
        object.__setattr__(self, 'archive_reward_total', reward_total)
        if (
            not isinstance(self.modification_count_definition, str)
            or not self.modification_count_definition.strip()
        ):
            raise ValueError('modification_count_definition must be nonempty')
        if not isinstance(self.modification_count_frequencies, tuple) or any(
            not isinstance(item, ModificationCountFrequency)
            for item in self.modification_count_frequencies
        ):
            raise TypeError('modification_count_frequencies must be a tuple')
        if (
            sum(item.count for item in self.modification_count_frequencies)
            != self.attempt_count
        ):
            raise ValueError('modification frequencies must sum to attempt_count')
        if any(
            item.m is not None and item.m > self.k
            for item in self.modification_count_frequencies
        ):
            raise ValueError('m cannot exceed k')

    @property
    def admission_rate(self) -> float | None:
        """Return archive admissions per committed attempt, when observed."""
        if self.attempt_count == 0:
            return None
        return self.archive_admission_count / self.attempt_count

    @property
    def archive_reward_rate(self) -> float | None:
        """Return mean difficulty-weighted archive reward, when observed."""
        if self.attempt_count == 0:
            return None
        return self.archive_reward_total / self.attempt_count


class ComponentRole(StrEnum):
    """Registered component role recorded in run provenance."""

    SOLUTION_IDENTITY = 'solution_identity'
    SOLUTION_CODEC = 'solution_codec'
    OBJECTIVE_FUNCTION = 'objective_function'
    APPLICATION_EVALUATOR = 'application_evaluator'
    OPERATOR = 'operator'
    SEARCH_EXTENSION = 'search_extension'
    ARCHIVE_POLICY = 'archive_policy'
    EXPLORATION_POLICY = 'exploration_policy'
    BASE_SELECTION_POLICY = 'base_selection_policy'
    BUDGET_POLICY = 'budget_policy'
    FEEDBACK_POLICY = 'feedback_policy'
    EXECUTOR = 'executor'
    RANDOM_SOURCE = 'random_source'
    EVENT_OBSERVER = 'event_observer'
    CHECKPOINT_STORE = 'checkpoint_store'
    SOLUTION_FORMATTER = 'solution_formatter'
    RUN_LOGGER = 'run_logger'


@dataclass(frozen=True, slots=True)
class ComponentConfigurationEntry:
    """One component identity/configuration pair in run provenance."""

    component_role: ComponentRole
    component_id: ComponentId
    configuration_id: ComponentConfigurationId | None
    objective_id: ObjectiveId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.component_role, ComponentRole):
            raise TypeError('component_role must be a ComponentRole')
        identifier_text(self.component_id)
        if self.configuration_id is not None:
            identifier_text(self.configuration_id)
        if self.objective_id is not None:
            identifier_text(self.objective_id)
        if (self.component_role is ComponentRole.OBJECTIVE_FUNCTION) != (
            self.objective_id is not None
        ):
            raise ValueError('objective_id is present only for objective functions')


@dataclass(frozen=True, slots=True)
class OperatorCatalogEntry:
    """Registered Operator metadata copied into the run provenance."""

    operator_id: OperatorId
    max_neighborhood_size: int
    feasibility_restoration: bool
    modification_count_definition: str

    def __post_init__(self) -> None:
        identifier_text(self.operator_id)
        require_integer(
            self.max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )
        if not isinstance(self.feasibility_restoration, bool):
            raise TypeError('feasibility_restoration must be bool')
        if not self.modification_count_definition.strip():
            raise ValueError('modification_count_definition must be nonempty')


@dataclass(frozen=True, slots=True)
class PromotedCompositionReport:
    """One successful composition recorded as a generated Operator."""

    operator_id: OperatorId
    source_composition_id: CompositionId
    component_operator_ids: tuple[OperatorId, OperatorId]
    first_admitted_solution_id: SolutionId
    max_neighborhood_size: int
    feasibility_restoration: bool
    inherited_group_ids: frozenset[OperatorGroupId]
    promoted_in_current_invocation: bool

    def __post_init__(self) -> None:
        identifier_text(self.operator_id)
        if not isinstance(self.source_composition_id, CompositionId):
            raise TypeError('source_composition_id must be a CompositionId')
        if (
            not isinstance(self.component_operator_ids, tuple)
            or len(self.component_operator_ids) != 2
        ):
            raise TypeError('component_operator_ids must be a pair')
        for operator_id in self.component_operator_ids:
            identifier_text(operator_id)
        if self.component_operator_ids != (
            self.source_composition_id.first_operator_id,
            self.source_composition_id.second_operator_id,
        ):
            raise ValueError('component_operator_ids must match source_composition_id')
        if not isinstance(self.first_admitted_solution_id, SolutionId):
            raise TypeError('first_admitted_solution_id must be a SolutionId')
        require_integer(
            self.max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )
        if not isinstance(self.feasibility_restoration, bool):
            raise TypeError('feasibility_restoration must be bool')
        if not isinstance(self.inherited_group_ids, frozenset):
            raise TypeError('inherited_group_ids must be a frozenset')
        for group_id in self.inherited_group_ids:
            identifier_text(group_id)
        if not isinstance(self.promoted_in_current_invocation, bool):
            raise TypeError('promoted_in_current_invocation must be bool')


@dataclass(frozen=True, slots=True)
class RunProvenance:
    """Registered components, Operators, and groups for one invocation."""

    components: tuple[ComponentConfigurationEntry, ...]
    operators: tuple[OperatorCatalogEntry, ...]
    operator_groups: tuple[OperatorGroup, ...]


@dataclass(frozen=True, slots=True)
class AssessmentCount:
    """Number of submission occurrences with one application assessment."""

    assessment: ApplicationAssessment
    count: int

    def __post_init__(self) -> None:
        if not isinstance(self.assessment, ApplicationAssessment):
            raise TypeError('assessment must be an ApplicationAssessment')
        require_integer(self.count, name='count', minimum=0)


@dataclass(frozen=True, slots=True)
class FailureCount:
    """Number of event occurrences for one component failure classification."""

    source: ComponentId
    kind: FailureKind
    phase: FailurePhase
    count: int

    def __post_init__(self) -> None:
        identifier_text(self.source)
        if not isinstance(self.kind, FailureKind):
            raise TypeError('kind must be a FailureKind')
        if not isinstance(self.phase, str) or not self.phase:
            raise ValueError('phase must be nonempty')
        require_integer(self.count, name='count', minimum=0)


@dataclass(frozen=True, slots=True)
class DispositionCount:
    """Number of candidate outcomes with one search disposition."""

    disposition: SearchDisposition
    count: int

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, SearchDisposition):
            raise TypeError('disposition must be a SearchDisposition')
        require_integer(self.count, name='count', minimum=0)


@dataclass(frozen=True, slots=True)
class ExcursionFrequency:
    """Frequency of active or completed infeasible excursions."""

    status: Literal['active', 'completed']
    step_count: int
    exit_kind: Literal['returned_to_anchor', 'feasible_exit'] | None
    archive_admitted: bool | None
    count: int

    def __post_init__(self) -> None:
        require_integer(self.step_count, name='step_count', minimum=0)
        require_integer(self.count, name='count', minimum=0)
        if self.status == 'active':
            if self.exit_kind is not None or self.archive_admitted is not None:
                raise ValueError('active excursions have no exit result')
        elif self.status == 'completed':
            if self.exit_kind not in ('returned_to_anchor', 'feasible_exit'):
                raise ValueError('completed excursions require an exit kind')
            if not isinstance(self.archive_admitted, bool):
                raise ValueError('completed excursions require archive_admitted')
        else:
            raise ValueError('status must be active or completed')


@dataclass(frozen=True, slots=True)
class ExecutiveSummary:
    """Structured summary for a search or maintenance invocation."""

    invocation_id: InvocationId
    invocation_kind: InvocationKind
    termination_reason: TerminationReason
    elapsed_seconds: FiniteReal
    wall_time_limit_seconds: FiniteReal | None
    remaining_seconds: FiniteReal | None
    initial_pareto_count: int
    final_pareto_solutions: tuple[ParetoSolutionReport, ...]
    counters: InvocationCounters
    provenance: RunProvenance
    choice_statistics: tuple[ChoiceStatistics, ...]
    promoted_compositions: tuple[PromotedCompositionReport, ...]
    assessment_counts: tuple[AssessmentCount, ...]
    failure_counts: tuple[FailureCount, ...]
    disposition_counts: tuple[DispositionCount, ...]
    excursion_frequencies: tuple[ExcursionFrequency, ...]
    composition_selection_probability: FiniteReal
    feedback_selection_mode: FeedbackSelectionMode | None

    def __post_init__(self) -> None:
        if not isinstance(self.invocation_id, InvocationId):
            raise TypeError('invocation_id must be an InvocationId')
        if self.invocation_kind not in ('search', 'maintenance'):
            raise ValueError('invocation_kind must be search or maintenance')
        if not isinstance(self.termination_reason, TerminationReason):
            raise TypeError('termination_reason must be a TerminationReason')
        probability = finite_real(
            self.composition_selection_probability,
            name='composition_selection_probability',
        )
        if not 0.0 <= probability <= 1.0:
            raise ValueError('composition_selection_probability must be in [0, 1]')
        object.__setattr__(self, 'composition_selection_probability', probability)
        object.__setattr__(
            self,
            'elapsed_seconds',
            finite_real(self.elapsed_seconds, name='elapsed_seconds'),
        )
        if self.elapsed_seconds < 0:
            raise ValueError('elapsed_seconds must be nonnegative')
        for field_name in ('wall_time_limit_seconds', 'remaining_seconds'):
            value = getattr(self, field_name)
            if value is not None:
                normalized = finite_real(value, name=field_name)
                if normalized < 0:
                    raise ValueError(f'{field_name} must be nonnegative')
                object.__setattr__(self, field_name, normalized)
        if self.invocation_kind == 'maintenance' and (
            self.wall_time_limit_seconds is not None
            or self.remaining_seconds is not None
        ):
            raise ValueError('maintenance summaries have no wall-time budget')
        require_integer(
            self.initial_pareto_count, name='initial_pareto_count', minimum=0
        )
        if not isinstance(self.final_pareto_solutions, tuple):
            raise TypeError('final_pareto_solutions must be a tuple')
        if not isinstance(self.promoted_compositions, tuple) or any(
            not isinstance(item, PromotedCompositionReport)
            for item in self.promoted_compositions
        ):
            raise TypeError(
                'promoted_compositions must be a tuple of PromotedCompositionReport'
            )
        if not isinstance(self.counters, InvocationCounters):
            raise TypeError('counters must be InvocationCounters')
