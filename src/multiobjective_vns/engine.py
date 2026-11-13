"""Coordinate construction, initialization, and sequential VNS search.

Created: 2026-10-29T14:26:25+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import json
import math
import threading
import time
import warnings
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from multiobjective_vns.callbacks import (
    ApplicationEvaluator,
    SolutionCodec,
    SolutionIdentity,
    StatefulOperator,
    StopToken,
)
from multiobjective_vns.candidate_processing import CandidateProcessor
from multiobjective_vns.components import ComponentIdentity
from multiobjective_vns.config import MultiobjectiveVNSConfig
from multiobjective_vns.default_components import (
    DefaultBudgetPolicy,
    DefaultFeedbackPolicy,
    LeastAttemptedBasePolicy,
    ParetoArchivePolicy,
    RetainingExplorationPolicy,
    SeededRandomSource,
    SequentialExecutor,
)
from multiobjective_vns.default_logger import DefaultRunLogger
from multiobjective_vns.evaluation import EvaluationProcessor
from multiobjective_vns.event_stream import EventDraft, EventJournal
from multiobjective_vns.events import EventObserver, EventPhase
from multiobjective_vns.exceptions import (
    CheckpointRestoreError,
    ComponentRegistrationError,
    ComponentSetupError,
    InitialSolutionsError,
    ResumeCompatibilityError,
)
from multiobjective_vns.extensions import StatefulSearchExtension
from multiobjective_vns.identifiers import (
    ChoiceId,
    ChoiceKind,
    ComponentConfigurationId,
    ComponentId,
    CompositionId,
    InvocationId,
    OperatorGroupId,
    OperatorId,
    ProblemInstanceId,
    SolutionId,
    identifier_text,
)
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
    ObjectiveValue,
)
from multiobjective_vns.pareto_snapshots import (
    ParetoSnapshotData,
    read_pareto_snapshot,
    write_pareto_snapshot,
)
from multiobjective_vns.policies import (
    ArchivePolicy,
    BaseSelectionPolicy,
    BudgetPolicy,
    ExplorationPolicy,
    FeedbackPolicy,
)
from multiobjective_vns.promoted_catalog import (
    PromotedCatalogEntry,
    PromotedCompositionCatalog,
    PromotedCompositionCatalogError,
)
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateOutcome,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import (
    EvaluationRecord,
    ObjectiveBoundFinding,
    SolutionRecord,
)
from multiobjective_vns.records.failures import ComponentFailure, FailureKind
from multiobjective_vns.records.policies import (
    ActivePoolDecision,
    ActivePoolRequest,
    ArchiveDecision,
    ArchiveEntry,
    ArchiveRequest,
    BaseSelectionDecision,
    BaseSelectionRequest,
    BudgetDecision,
    BudgetRequest,
    ChoiceDescriptor,
    ChoiceSelectionRequest,
    FeedbackObservation,
    InvocationCounters,
    MaintenanceAction,
    OperatorChoiceCategory,
    PoolAction,
)
from multiobjective_vns.records.reporting import (
    ComponentConfigurationEntry,
    ComponentRole,
    ObjectiveBoundEventFinding,
    ObjectiveBoundReport,
    OperatorCatalogEntry,
    PromotedCompositionReport,
    RunProvenance,
)
from multiobjective_vns.records.results import (
    LogFinalizationResult,
    MaintenanceResult,
    RunLog,
    RunResult,
)
from multiobjective_vns.records.state import (
    Checkpoint,
    CheckpointReference,
    SearchState,
)
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    ArchiveStatus,
    CompositionPosition,
    ProcessingStatus,
    SearchDisposition,
    SubmissionSource,
    TerminationReason,
)
from multiobjective_vns.registrations import (
    OperatorCompositionRegistration,
    OperatorGroup,
    OperatorRegistration,
    SearchExtensionRegistration,
)
from multiobjective_vns.registry import SolutionRegistry
from multiobjective_vns.services import (
    CheckpointStore,
    Executor,
    RunLogger,
    SolutionFormatter,
)
from multiobjective_vns.transitions import SearchCommitCoordinator
from multiobjective_vns.values import FiniteReal, finite_real, require_integer


@dataclass(frozen=True, slots=True)
class _PromotedOperatorId:
    """Package-owned, stable identifier for one promoted composition."""

    source_composition_id: CompositionId
    disambiguator: int

    def __post_init__(self) -> None:
        require_integer(self.disambiguator, name='disambiguator', minimum=0)

    def to_log_text(self) -> str:
        """Return a stable textual ID disambiguated from caller IDs."""
        return 'multiobjective-vns:promoted-operator:' + json.dumps(
            [self.source_composition_id.to_log_text(), self.disambiguator],
            ensure_ascii=True,
            separators=(',', ':'),
        )


@dataclass(frozen=True, slots=True)
class _PromotedOperator:
    """Continuation metadata for one generated composition Operator."""

    report: PromotedCompositionReport
    modification_count_definition: str


@dataclass(frozen=True, slots=True)
class _SearchSnapshot:
    """Package-owned immutable continuation data behind the opaque API."""

    registry: SolutionRegistry
    archive_ids: frozenset[SolutionId]
    active_ids: frozenset[SolutionId]
    attempt_counts: Mapping[SolutionId, int]
    tracked_choice_ids: frozenset[ChoiceId]
    choice_size_cursors: Mapping[tuple[SolutionId, ChoiceId], int]
    operator_payloads: Mapping[OperatorId, object]
    extension_payloads: Mapping[ComponentId, object]
    feedback_payload: object
    random_payload: object
    manifest: tuple[tuple[str, str, str | None], ...]
    configuration_signature: tuple[object, ...]
    problem_instance_text: str
    objective_texts: tuple[str, ...]
    objective_bounds: tuple[ObjectiveValue | None, ...]
    commit_sequence: int = 0
    base_selection_context: tuple[SolutionId, bool] | None = None
    excursion: _InfeasibleExcursion | None = None
    codec_payloads: Mapping[SolutionId, object] | None = None
    attempts_since_archive_admission: int = 0
    promoted_operators: tuple[_PromotedOperator, ...] = ()

    def pareto_solutions(self) -> tuple[SolutionRecord, ...]:
        """Return current Pareto records in registry insertion order."""
        return tuple(
            record
            for record in self.registry.records
            if record.solution_id in self.archive_ids
        )

    def active_solutions(self) -> tuple[SolutionRecord, ...]:
        """Return active records in registry insertion order."""
        return tuple(
            record
            for record in self.registry.records
            if record.solution_id in self.active_ids
        )

    def visited_solutions(self) -> tuple[SolutionRecord, ...]:
        """Return every identity-resolved registry record."""
        return self.registry.records


@dataclass(frozen=True, slots=True)
class _InfeasibleExcursion:
    """Continuation data for one bounded infeasible trajectory."""

    anchor_id: SolutionId
    current_id: SolutionId
    current_solution: object
    step_count: int


class _RandomFailure(Exception):
    """Checked random-source exception preserving its structured diagnostic."""

    def __init__(self, failure: ComponentFailure) -> None:
        super().__init__(failure.reason)
        self.failure = failure


class _NoEligibleBase(Exception):
    """No current Pareto base has an enabled, unexhausted choice."""


class _OperatorCallFailure(Exception):
    """A failed Operator call with its diagnostic and captured state, if any."""

    def __init__(
        self,
        failure: ComponentFailure,
        operator_id: OperatorId,
        state_payload: object | None,
        state_captured: bool,
        *,
        random_failure: bool = False,
    ) -> None:
        super().__init__(failure.reason)
        self.failure = failure
        self.operator_id = operator_id
        self.state_payload = state_payload
        self.state_captured = state_captured
        self.random_failure = random_failure


class _CheckedRandomSource:
    """Validate every draw before exposing it to an Operator or policy."""

    def __init__(self, source: RandomSource) -> None:
        self._source = source
        self.component_id = source.component_id
        self.configuration_id = source.configuration_id

    def random(self) -> float:
        """Return a validated Python float in ``[0, 1)``."""
        try:
            value = self._source.random()
        except Exception as error:
            raise _RandomFailure(
                ComponentFailure(
                    FailureKind.CALLBACK_EXCEPTION,
                    self.component_id,
                    'random_generation',
                    f'RandomSource.random raised: {error}',
                )
            ) from error
        if (
            not isinstance(value, float)
            or not math.isfinite(value)
            or not 0 <= value < 1
        ):
            raise _RandomFailure(
                ComponentFailure(
                    FailureKind.CONTRACT_VIOLATION,
                    self.component_id,
                    'random_generation',
                    'RandomSource.random must return a finite float in [0, 1)',
                )
            )
        return value


@dataclass(frozen=True, slots=True)
class _OperatorSpec:
    """An available registered or promoted Operator and its execution tree."""

    operator_id: OperatorId
    max_neighborhood_size: int
    feasibility_restoration: bool
    modification_count_definition: str
    group_ids: frozenset[OperatorGroupId]
    configuration_id: ComponentConfigurationId
    catalog_kind: Literal['ordinary', 'pre_registered_composite', 'promoted_composite']
    component_operator_ids: tuple[OperatorId, OperatorId] | None = None
    registration: OperatorRegistration | None = None
    first: _OperatorSpec | None = None
    second: _OperatorSpec | None = None

    def dependencies(self) -> frozenset[OperatorId]:
        """Return this Operator ID and every Operator ID in its expression."""
        dependencies = {self.operator_id}
        if self.first is not None:
            dependencies.update(self.first.dependencies())
        if self.second is not None:
            dependencies.update(self.second.dependencies())
        return frozenset(dependencies)


@dataclass(frozen=True, slots=True)
class _Choice:
    """One standalone or ordered-composition choice available to a search."""

    descriptor: ChoiceDescriptor
    first: _OperatorSpec
    second: _OperatorSpec | None = None


@dataclass(frozen=True, slots=True)
class _Submission:
    """Candidate submission work prepared before a transition is published."""

    state: _SearchSnapshot
    outcome: CandidateOutcome
    events: tuple[EventDraft, ...]
    admitted: bool
    failures: tuple[ComponentFailure, ...]
    evaluation_requested: bool
    excursion: _InfeasibleExcursion | None
    excursion_event: EventDraft | None


@dataclass(slots=True)
class _CheckpointProgress:
    """Invocation-local store reference and interval origin."""

    reference: object | None
    saved_sequence: int
    interval: int | None


@dataclass(slots=True)
class _ParetoSnapshotProgress:
    """Invocation-local destination and latest successful snapshot path."""

    destination: Path
    latest_successful_path: Path | None = None
    failed: bool = False


class MultiobjectiveVNS:
    """Run sequential multiobjective variable-neighborhood search.

    The engine coordinates registered callbacks and policies. Application
    solutions remain opaque to the package and are assessed through the
    configured ``ApplicationEvaluator``.

    :param solution_codec: Application-owned solution checkpoint codec.
    :param solution_identity: Application equivalence rule.
    :param objective_declaration: Ordered minimized objectives.
    :param operators: Registered neighborhood Operators.
    :param pre_registered_compositions: Ordered Operator pairs registered as
        standalone composite choices before search.
    :param application_evaluator: Joint representation, feasibility, and
        objective evaluator.
    :param problem_instance_id: Stable identity for the fixed problem data.
    :param config: Immutable search configuration.
    :param promoted_composition_catalog_path: File that carries promoted
        composition definitions across fresh searches, or ``None`` to disable.
    :raises ComponentRegistrationError: If registrations conflict or are
        incomplete.
    :raises ComponentSetupError: If component baselines cannot be captured.
    """

    def __init__(
        self,
        *,
        solution_codec: SolutionCodec,
        solution_identity: SolutionIdentity,
        objective_declaration: ObjectiveDeclaration,
        operators: tuple[OperatorRegistration, ...],
        pre_registered_compositions: tuple[OperatorCompositionRegistration, ...] = (),
        application_evaluator: ApplicationEvaluator,
        problem_instance_id: ProblemInstanceId,
        config: MultiobjectiveVNSConfig,
        operator_groups: tuple[OperatorGroup, ...] = (),
        extensions: tuple[SearchExtensionRegistration, ...] = (),
        archive_policy: ArchivePolicy | None = None,
        exploration_policy: ExplorationPolicy | None = None,
        base_selection_policy: BaseSelectionPolicy | None = None,
        budget_policy: BudgetPolicy | None = None,
        feedback_policy: FeedbackPolicy | None = None,
        executor: Executor | None = None,
        random_source: RandomSource | None = None,
        event_observers: tuple[EventObserver, ...] = (),
        checkpoint_store: CheckpointStore | None = None,
        solution_formatter: SolutionFormatter | None = None,
        promoted_composition_catalog_path: Path | None = Path(
            './multiobjective-vns-promoted-compositions.json'
        ),
    ) -> None:
        if not isinstance(operators, tuple) or not operators:
            raise ComponentRegistrationError('operators must be a nonempty tuple')
        if any(not isinstance(item, OperatorRegistration) for item in operators):
            raise ComponentRegistrationError('operators must be registrations')
        if not isinstance(pre_registered_compositions, tuple) or any(
            not isinstance(item, OperatorCompositionRegistration)
            for item in pre_registered_compositions
        ):
            raise ComponentRegistrationError(
                'pre_registered_compositions must be composition registrations'
            )
        if not isinstance(operator_groups, tuple) or any(
            not isinstance(group, OperatorGroup) for group in operator_groups
        ):
            raise ComponentRegistrationError('operator_groups must be a tuple')
        if not isinstance(extensions, tuple) or any(
            not isinstance(item, SearchExtensionRegistration) for item in extensions
        ):
            raise ComponentRegistrationError('extensions must be registrations')
        if not isinstance(objective_declaration, ObjectiveDeclaration):
            raise ComponentRegistrationError('objective_declaration is required')
        if not isinstance(config, MultiobjectiveVNSConfig):
            raise ComponentRegistrationError('config must be a MultiobjectiveVNSConfig')
        if promoted_composition_catalog_path is not None and not isinstance(
            promoted_composition_catalog_path, Path
        ):
            raise ComponentRegistrationError(
                'promoted_composition_catalog_path must be a Path or None'
            )

        self._solution_codec = solution_codec
        self._solution_identity = solution_identity
        self._objective_declaration = objective_declaration
        self._operators = operators
        self._operator_by_id = {
            registration.operator_id: registration for registration in operators
        }
        self._pre_registered_compositions = pre_registered_compositions
        self._pre_registered_by_id = {
            registration.operator_id: registration
            for registration in pre_registered_compositions
        }
        self._pre_registered_pairs = frozenset(
            (item.first_operator_id, item.second_operator_id)
            for item in pre_registered_compositions
        )
        self._application_evaluator = application_evaluator
        self._problem_instance_id = problem_instance_id
        self._config = config
        self._promoted_composition_catalog_path = (
            promoted_composition_catalog_path.resolve()
            if promoted_composition_catalog_path is not None
            else None
        )
        self._promoted_composition_catalog = PromotedCompositionCatalog(
            self._promoted_composition_catalog_path
        )
        self._operator_groups = operator_groups
        self._extensions = extensions
        self._archive_policy = archive_policy or ParetoArchivePolicy()
        self._exploration_policy = exploration_policy or RetainingExplorationPolicy()
        self._uses_default_base_selection = base_selection_policy is None
        self._base_policy = base_selection_policy or LeastAttemptedBasePolicy()
        self._budget_policy = budget_policy or DefaultBudgetPolicy()
        self._feedback_policy = feedback_policy or DefaultFeedbackPolicy(config)
        self._executor = executor or SequentialExecutor()
        self._random_source = random_source or SeededRandomSource(config.random_seed)
        self._checked_random = _CheckedRandomSource(self._random_source)
        self._observers = event_observers
        self._checkpoint_store = checkpoint_store
        self._solution_formatter = solution_formatter
        self._logger: RunLogger = DefaultRunLogger()
        self._active_run_lock = threading.Lock()
        self._validate_registrations()
        self._evaluation_processor = EvaluationProcessor(
            application_evaluator=application_evaluator,
            executor=self._executor,
            objective_declaration=objective_declaration,
        )
        self._candidate_processor = CandidateProcessor(
            evaluation_processor=self._evaluation_processor
        )
        self._manifest = self._build_manifest()
        self._operator_baselines = self._capture_operator_baselines()
        self._feedback_baseline = self._capture_feedback_baseline()
        self._random_baseline = self._capture_random_baseline()
        self._extension_baselines = self._capture_extension_baselines()

    def run(
        self,
        *,
        initial_solutions: tuple[object, ...] | None = None,
        checkpoint: Checkpoint | object | None = None,
        checkpoint_interval: int | None = None,
        progress_report_interval_seconds: FiniteReal = 10.0,
        log_directory: Path = Path('./multiobjective-vns-logs/'),
        stop_token: StopToken | None = None,
    ) -> RunResult:
        """Run from initial solutions or resume from a saved checkpoint.

        :raises ValueError: If the input mode or run arguments are invalid.
        :raises CheckpointRestoreError: If a store reference cannot be loaded.
        :raises ResumeCompatibilityError: If continuation setup differs.
        :raises InitialSolutionsError: If the full initial set has no valid,
            feasible solution.
        """
        started = time.monotonic()
        if not self._active_run_lock.acquire(blocking=False):
            raise RuntimeError('only one active run is allowed per instance')
        try:
            return self._run_locked(
                started,
                initial_solutions=initial_solutions,
                checkpoint=checkpoint,
                checkpoint_interval=checkpoint_interval,
                progress_report_interval_seconds=progress_report_interval_seconds,
                log_directory=log_directory,
                stop_token=stop_token,
            )
        finally:
            self._active_run_lock.release()

    def load_pareto_snapshot(self, path: Path) -> tuple[object, ...]:
        """Restore a compatible Pareto snapshot as fresh initial solutions.

        :param path: Snapshot path returned by an earlier ``RunResult``.
        :returns: Restored solutions in the snapshot's archive order.
        :raises ResumeCompatibilityError: If problem, objective, or codec
            identities differ from the snapshot.
        :raises ComponentSetupError: If the file or a solution payload cannot
            be read or restored.
        """
        if not isinstance(path, Path):
            raise TypeError('path must be a Path')
        try:
            snapshot = read_pareto_snapshot(path)
        except Exception as error:
            kind = (
                FailureKind.INFRASTRUCTURE_FAILURE
                if isinstance(error, OSError)
                else FailureKind.CONTRACT_VIOLATION
            )
            raise ComponentSetupError(
                self._failure(
                    'multiobjective-vns:pareto-snapshot-store',
                    'pareto_snapshot_restore',
                    f'Pareto snapshot could not be read: {error}',
                    kind,
                )
            ) from error

        expected_objective_ids = tuple(
            identifier_text(objective.objective_id)
            for objective in self._objective_declaration.objectives
        )
        expected_objective_configurations = tuple(
            identifier_text(objective.configuration_id)
            for objective in self._objective_declaration.objectives
        )
        if (
            snapshot.problem_instance_id != identifier_text(self._problem_instance_id)
            or snapshot.objective_ids != expected_objective_ids
            or snapshot.objective_configuration_ids != expected_objective_configurations
            or snapshot.solution_codec_component_id
            != identifier_text(self._solution_codec.component_id)
            or snapshot.solution_codec_configuration_id
            != identifier_text(self._solution_codec.configuration_id)
        ):
            raise ResumeCompatibilityError(
                'Pareto snapshot compatibility manifest differs'
            )

        solutions: list[object] = []
        for payload in snapshot.solution_payloads:
            try:
                solutions.append(self._solution_codec.restore(payload))
            except Exception as error:
                kind = (
                    FailureKind.CONTRACT_VIOLATION
                    if isinstance(error, (TypeError, ValueError))
                    else FailureKind.CALLBACK_EXCEPTION
                )
                raise ComponentSetupError(
                    self._failure(
                        self._solution_codec,
                        'pareto_snapshot_restore',
                        f'SolutionCodec.restore failed: {error}',
                        kind,
                    )
                ) from error
        return tuple(solutions)

    def maintain(
        self,
        *,
        state: SearchState | Checkpoint | CheckpointReference,
        clean_active_pool: bool = False,
        reset_feedback: bool = False,
        log_directory: Path = Path('./multiobjective-vns-logs/'),
    ) -> MaintenanceResult:
        """Clean a saved active pool, reset feedback, or perform both.

        Each requested action publishes its own immutable search-state commit.
        The supplied state remains available to its caller. A configured
        checkpoint store receives a snapshot after every successful action.

        :param state: In-memory continuation state, complete checkpoint, or
            reference loadable by the configured checkpoint store.
        :param clean_active_pool: Reduce active membership to current Pareto
            archive members.
        :param reset_feedback: Clear accumulated feedback counts.
        :param log_directory: Destination for this maintenance invocation's
            event and summary files.
        :returns: Maintenance status, updated checkpoint, diagnostics, and
            reporting artifacts.
        :raises ValueError: If no action is requested or an input is invalid.
        :raises CheckpointRestoreError: If a referenced checkpoint cannot load.
        :raises ResumeCompatibilityError: If state compatibility differs.
        :raises ComponentSetupError: If state restoration fails.
        :raises RuntimeError: If this engine already has an active invocation.
        """
        started = time.monotonic()
        if not isinstance(clean_active_pool, bool) or not isinstance(
            reset_feedback, bool
        ):
            raise TypeError('maintenance action flags must be bool')
        if not clean_active_pool and not reset_feedback:
            raise ValueError('at least one maintenance action must be requested')
        if not self._active_run_lock.acquire(blocking=False):
            raise RuntimeError('only one active invocation is allowed per instance')
        try:
            return self._maintain_locked(
                started,
                state=state,
                clean_active_pool=clean_active_pool,
                reset_feedback=reset_feedback,
                log_directory=log_directory,
            )
        finally:
            self._active_run_lock.release()

    def _maintain_locked(
        self,
        started: float,
        *,
        state: SearchState | Checkpoint | CheckpointReference,
        clean_active_pool: bool,
        reset_feedback: bool,
        log_directory: Path,
    ) -> MaintenanceResult:
        if state is None:
            raise ValueError('state is required')
        checkpoint_input: Checkpoint | object
        if isinstance(state, Checkpoint):
            checkpoint_input = state
        elif all(
            callable(getattr(state, method_name, None))
            for method_name in (
                'pareto_solutions',
                'active_solutions',
                'visited_solutions',
            )
        ):
            checkpoint_input = Checkpoint(cast(SearchState, state))
        else:
            checkpoint_input = state

        search_state, reference, saved_sequence = self._prepare_state(checkpoint_input)
        state = search_state
        checkpoint_progress = _CheckpointProgress(
            reference,
            saved_sequence if saved_sequence is not None else state.commit_sequence,
            None,
        )
        invocation_id = InvocationId.new()
        resolved_directory = log_directory.resolve()
        initial_archive_ids = tuple(
            record.solution_id for record in state.pareto_solutions()
        )
        counters = InvocationCounters()
        diagnostics: list[ComponentFailure] = []
        try:
            self._logger.begin(invocation_id, resolved_directory)
        except Exception as error:
            failure = self._failure(
                self._logger,
                'run_logging',
                f'RunLogger.begin raised: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )
            return MaintenanceResult(
                Checkpoint(state),
                TerminationReason.COMPONENT_FAILURE,
                (failure,),
                counters,
            )

        journal = EventJournal(invocation_id, self._logger)
        coordinator = self._coordinator(state, journal)
        invocation_started = self._event(
            'invocation_started',
            EventPhase.INVOCATION,
            {
                'mode': 'maintenance',
                'problem_instance_id': self._problem_instance_id,
                'objective_ids': tuple(
                    objective.objective_id
                    for objective in self._objective_declaration.objectives
                ),
                'config': self._config,
                'provenance': self._provenance(state),
                'log_directory': str(resolved_directory),
                'checkpoint_commit_sequence': state.commit_sequence,
                'wall_time_limit_seconds': None,
                'progress_report_interval_seconds': None,
                'elapsed_seconds': self._elapsed(started),
                'remaining_seconds': None,
            },
        )
        try:
            self._append_invocation(coordinator, invocation_started)
        except Exception as error:
            failure = self._failure(
                self._logger,
                'run_logging',
                f'RunLogger.append failed while starting maintenance: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )
            return MaintenanceResult(
                Checkpoint(state),
                TerminationReason.COMPONENT_FAILURE,
                (failure,),
                counters,
                coordinator.run_log,
            )

        actions = tuple(
            action
            for enabled, action in (
                (clean_active_pool, MaintenanceAction.CLEAN_ACTIVE_POOL),
                (reset_feedback, MaintenanceAction.RESET_FEEDBACK),
            )
            if enabled
        )
        termination = TerminationReason.MAINTENANCE_COMPLETED
        try:
            self._append_invocation(
                coordinator,
                self._event(
                    'maintenance_requested',
                    EventPhase.INVOCATION,
                    {'actions': actions},
                ),
            )
        except Exception as error:
            diagnostics.append(
                self._failure(
                    self._logger,
                    'run_logging',
                    f'maintenance request event append failed: {error}',
                    FailureKind.INFRASTRUCTURE_FAILURE,
                )
            )
            termination = TerminationReason.COMPONENT_FAILURE

        for action in actions:
            if termination is TerminationReason.COMPONENT_FAILURE:
                break
            if action is MaintenanceAction.CLEAN_ACTIVE_POOL:
                action_failure, next_state, event = self._prepare_pool_cleanup(state)
            else:
                action_failure, next_state, event = self._prepare_feedback_reset(state)
            if action_failure is not None:
                diagnostics.append(action_failure)
                logging_failure = self._try_append_failure_event(
                    coordinator, action_failure
                )
                if logging_failure is not None:
                    diagnostics.append(logging_failure)
                termination = TerminationReason.COMPONENT_FAILURE
                break
            assert next_state is not None and event is not None
            try:
                coordinator.commit(next_state, (event,))
            except Exception as error:
                if action is MaintenanceAction.RESET_FEEDBACK:
                    self._restore_feedback(state.feedback_payload)
                diagnostics.append(
                    self._failure(
                        self._logger,
                        'run_logging',
                        f'maintenance transition commit failed: {error}',
                        FailureKind.INFRASTRUCTURE_FAILURE,
                    )
                )
                termination = TerminationReason.COMPONENT_FAILURE
                break
            state = next_state
            if self._checkpoint_store is not None:
                checkpoint_failure = self._save_checkpoint_if_needed(
                    state,
                    checkpoint_progress,
                    coordinator,
                    force_final=True,
                )
                if checkpoint_failure is not None:
                    diagnostics.append(checkpoint_failure)
                    logging_failure = self._try_append_failure_event(
                        coordinator, checkpoint_failure
                    )
                    if logging_failure is not None:
                        diagnostics.append(logging_failure)
                    termination = TerminationReason.COMPONENT_FAILURE
                    break

        termination = self._publish_final_views(
            coordinator, state, termination, diagnostics
        )
        try:
            self._append_invocation(
                coordinator,
                self._event(
                    'invocation_stopped',
                    EventPhase.INVOCATION,
                    {
                        'termination_reason': termination,
                        'counters': counters,
                        'registry_size': len(state.registry.records),
                        'archive_size': len(state.archive_ids),
                        'active_pool_size': len(state.active_ids),
                        'wall_time_limit_seconds': None,
                        'elapsed_seconds': self._elapsed(started),
                        'remaining_seconds': None,
                    },
                ),
            )
        except Exception as error:
            diagnostics.append(
                self._failure(
                    self._logger,
                    'run_logging',
                    f'invocation_stopped event append failed: {error}',
                    FailureKind.INFRASTRUCTURE_FAILURE,
                )
            )
        finalized, finalization_failure = self._finalize(
            coordinator.run_log,
            initial_archive_ids,
            state,
            termination,
            counters,
        )
        if finalization_failure is not None:
            diagnostics.append(finalization_failure)
        diagnostics.extend(coordinator.diagnostics)
        return MaintenanceResult(
            checkpoint=Checkpoint(state),
            termination_reason=termination,
            diagnostics=tuple(diagnostics),
            counters=counters,
            run_log=coordinator.run_log,
            executive_summary=(
                finalized.executive_summary if finalized is not None else None
            ),
            report_diagnostics=(
                finalized.report_diagnostics if finalized is not None else ()
            ),
            checkpoint_reference=checkpoint_progress.reference,
            event_log_path=(
                finalized.event_log_path if finalized is not None else None
            ),
            summary_path=(finalized.summary_path if finalized is not None else None),
            registry_view_path=(
                finalized.registry_view_path if finalized is not None else None
            ),
            pareto_view_path=(
                finalized.pareto_view_path if finalized is not None else None
            ),
        )

    def _prepare_pool_cleanup(
        self, state: _SearchSnapshot
    ) -> tuple[ComponentFailure | None, _SearchSnapshot | None, EventDraft | None]:
        eligible = self._eligible_pool_ids(state)
        request = ActivePoolRequest(
            state.registry.records,
            eligible,
            frozenset((state.active_ids & eligible) | state.archive_ids),
            state.archive_ids,
            PoolAction.CLEANUP,
            None,
        )
        try:
            decision = self._exploration_policy.decide(request)
        except Exception as error:
            return (
                self._failure(
                    self._exploration_policy,
                    'policy_decision',
                    f'ExplorationPolicy.decide raised during cleanup: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                ),
                None,
                None,
            )
        if not isinstance(decision, ActivePoolDecision) or decision.active_ids != (
            state.archive_ids
        ):
            return (
                self._failure(
                    self._exploration_policy,
                    'policy_decision',
                    'ExplorationPolicy cleanup must return exactly the Pareto archive',
                    FailureKind.CONTRACT_VIOLATION,
                ),
                None,
                None,
            )
        added = tuple(
            sorted(decision.active_ids - state.active_ids, key=identifier_text)
        )
        removed = tuple(
            sorted(state.active_ids - decision.active_ids, key=identifier_text)
        )
        try:
            random_payload = self._capture_random_payload()
        except _TransitionFailure as error:
            return error.failure, None, None
        next_state = replace(
            state,
            active_ids=decision.active_ids,
            random_payload=random_payload,
            commit_sequence=state.commit_sequence + 1,
        )
        event = self._event(
            'active_pool_changed',
            EventPhase.ACTIVE_POOL,
            {
                'action': PoolAction.CLEANUP,
                'changed_solution_id': None,
                'added_solution_ids': added,
                'removed_solution_ids': removed,
            },
            source_id=self._exploration_policy.component_id,
        )
        return None, next_state, event

    def _prepare_feedback_reset(
        self, state: _SearchSnapshot
    ) -> tuple[ComponentFailure | None, _SearchSnapshot | None, EventDraft | None]:
        original_payload = state.feedback_payload
        try:
            reset_callback = cast(Callable[[], object], self._feedback_policy.reset)
            reset_result = reset_callback()
        except Exception as error:
            self._restore_feedback(original_payload)
            return (
                self._failure(
                    self._feedback_policy,
                    'policy_decision',
                    f'FeedbackPolicy.reset raised: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                ),
                None,
                None,
            )
        if reset_result is not None:
            self._restore_feedback(original_payload)
            return (
                self._failure(
                    self._feedback_policy,
                    'policy_decision',
                    'FeedbackPolicy.reset must return None',
                    FailureKind.CONTRACT_VIOLATION,
                ),
                None,
                None,
            )
        try:
            feedback_payload = self._capture_feedback_payload()
            random_payload = self._capture_random_payload()
        except _TransitionFailure as error:
            self._restore_feedback(original_payload)
            return error.failure, None, None
        next_state = replace(
            state,
            feedback_payload=feedback_payload,
            random_payload=random_payload,
            attempts_since_archive_admission=0,
            commit_sequence=state.commit_sequence + 1,
        )
        event = self._event(
            'feedback_reset',
            EventPhase.FEEDBACK,
            {
                'reset_choice_ids': self._feedback_reset_choice_ids(state),
                'attempts_since_archive_admission': 0,
            },
            source_id=self._feedback_policy.component_id,
        )
        return None, next_state, event

    def _feedback_reset_choice_ids(
        self, state: _SearchSnapshot
    ) -> tuple[ChoiceId, ...]:
        choices = {
            ChoiceId.for_operator(registration.operator_id)
            for registration in self._operators
        }
        choices.update(
            ChoiceId.for_operator(registration.operator_id)
            for registration in self._pre_registered_compositions
        )
        choices.update(
            ChoiceId.for_operator(promoted.report.operator_id)
            for promoted in state.promoted_operators
        )
        choices.update(state.tracked_choice_ids)
        return tuple(
            sorted(choices, key=lambda choice: choice.to_json_value().__repr__())
        )

    def _try_append_failure_event(
        self, coordinator: SearchCommitCoordinator, failure: ComponentFailure
    ) -> ComponentFailure | None:
        try:
            self._append_failure_event(
                coordinator,
                failure,
                base_id=None,
                k=None,
                component_position=None,
            )
        except Exception as error:
            return self._failure(
                self._logger,
                'run_logging',
                f'component_failure event append failed: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )
        return None

    def _run_locked(
        self,
        started: float,
        *,
        initial_solutions: tuple[object, ...] | None,
        checkpoint: Checkpoint | object | None,
        checkpoint_interval: int | None,
        progress_report_interval_seconds: FiniteReal,
        log_directory: Path,
        stop_token: StopToken | None,
    ) -> RunResult:
        if (initial_solutions is None) == (checkpoint is None):
            raise ValueError('supply exactly one of initial_solutions and checkpoint')
        if initial_solutions is not None and (
            not isinstance(initial_solutions, tuple) or not initial_solutions
        ):
            raise ValueError('initial_solutions must be a nonempty tuple')
        if checkpoint_interval is not None:
            require_integer(checkpoint_interval, name='checkpoint_interval', minimum=1)
            if self._checkpoint_store is None:
                raise ValueError('checkpoint_interval requires a CheckpointStore')
        progress_interval = finite_real(
            progress_report_interval_seconds,
            name='progress_report_interval_seconds',
        )
        if progress_interval <= 0:
            raise ValueError('progress_report_interval_seconds must be positive')

        state, checkpoint_reference, resume_sequence = self._prepare_state(checkpoint)
        checkpoint_progress = _CheckpointProgress(
            checkpoint_reference,
            resume_sequence if resume_sequence is not None else 0,
            checkpoint_interval,
        )
        invocation_id = InvocationId.new()
        resolved_directory = log_directory.resolve()
        snapshot_progress = _ParetoSnapshotProgress(
            resolved_directory / f'{invocation_id.to_log_text()}.pareto.json'
        )
        try:
            self._logger.begin(invocation_id, resolved_directory)
        except Exception as error:
            failure = self._failure(
                self._logger,
                'run_logging',
                f'RunLogger.begin raised: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )
            return RunResult(
                state,
                (),
                TerminationReason.COMPONENT_FAILURE,
                (failure,),
                InvocationCounters(),
            )

        journal = EventJournal(invocation_id, self._logger)
        coordinator = self._coordinator(state, journal)
        counters = InvocationCounters()
        diagnostics: list[ComponentFailure] = []
        wall_limit = float(self._config.budget_limits.max_wall_time_seconds)
        last_progress = started
        initial_archive_ids: tuple[SolutionId, ...] = tuple(state.archive_ids)
        mode = 'initial' if initial_solutions is not None else 'resume'
        invocation_started = self._event(
            'invocation_started',
            EventPhase.INVOCATION,
            {
                'mode': mode,
                'problem_instance_id': self._problem_instance_id,
                'objective_ids': tuple(
                    objective.objective_id
                    for objective in self._objective_declaration.objectives
                ),
                'config': self._config,
                'provenance': self._provenance(state),
                'log_directory': str(resolved_directory),
                'checkpoint_commit_sequence': resume_sequence,
                'wall_time_limit_seconds': wall_limit,
                'progress_report_interval_seconds': progress_interval,
                'elapsed_seconds': self._elapsed(started),
                'remaining_seconds': max(0.0, wall_limit - self._elapsed(started)),
            },
        )
        try:
            coordinator.append_invocation_events((invocation_started,))
        except Exception as error:
            failure = self._failure(
                self._logger,
                'run_logging',
                f'RunLogger.append raised: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )
            return RunResult(
                state,
                (),
                TerminationReason.COMPONENT_FAILURE,
                (failure,),
                counters,
                coordinator.run_log,
            )

        termination = TerminationReason.BUDGET_EXHAUSTED
        ready_for_checkpoint = initial_solutions is None
        eligible_initial_count = 0
        assessment_counts: Counter[ApplicationAssessment] = Counter()
        initial_submission_count = 0
        if initial_solutions is not None:
            termination = TerminationReason.INTERRUPTED
            for solution in initial_solutions:
                initial_submission_count += 1
                try:
                    submission = self._prepare_submission(
                        state,
                        Candidate(solution),
                        source=SubmissionSource.INITIAL,
                        counters=counters,
                        choice_id=None,
                        base_id=None,
                        k=None,
                        m=None,
                        component_position=None,
                        candidate_index=None,
                        ensure_feasible=None,
                    )
                except _TransitionFailure as transition_failure:
                    diagnostics.append(transition_failure.failure)
                    self._append_failure_event(
                        coordinator,
                        transition_failure.failure,
                        base_id=None,
                        k=None,
                        component_position=None,
                    )
                    termination = TerminationReason.COMPONENT_FAILURE
                    break
                if (
                    submission.outcome.assessment is ApplicationAssessment.VALID
                    and not submission.outcome.objective_bound_findings
                ):
                    eligible_initial_count += 1
                if submission.outcome.assessment is not None:
                    assessment_counts[submission.outcome.assessment] += 1
                if submission.failures and submission.outcome.solution_id is None:
                    diagnostics.extend(submission.failures)
                state, receipt_failure = self._commit_submission(
                    state,
                    submission,
                    coordinator,
                    component_events=(),
                    feedback_observation=None,
                )
                if receipt_failure is not None:
                    diagnostics.append(receipt_failure)
                    termination = TerminationReason.COMPONENT_FAILURE
                    break
                state = cast(_SearchSnapshot, coordinator.search_state)
                if submission.admitted:
                    view_failure = self._publish_view(
                        invocation_id=invocation_id,
                        state=state,
                        scope='pareto',
                        is_final=False,
                    )
                    if view_failure is not None:
                        diagnostics.append(view_failure)
                        logging_failure = self._try_append_failure_event(
                            coordinator, view_failure
                        )
                        if logging_failure is not None:
                            diagnostics.append(logging_failure)
                        termination = TerminationReason.COMPONENT_FAILURE
                        break
                    snapshot_failure = self._save_pareto_snapshot(
                        state, snapshot_progress
                    )
                    if snapshot_failure is not None:
                        diagnostics.append(snapshot_failure)
                        logging_failure = self._try_append_failure_event(
                            coordinator, snapshot_failure
                        )
                        if logging_failure is not None:
                            diagnostics.append(logging_failure)
                        snapshot_progress.failed = True
                        termination = TerminationReason.COMPONENT_FAILURE
                        break
                if self._progress_due(started, last_progress, progress_interval):
                    last_progress = self._emit_progress(
                        coordinator, started, wall_limit
                    )

            if termination is TerminationReason.COMPONENT_FAILURE:
                ready_for_checkpoint = False
            elif eligible_initial_count == 0:
                termination = TerminationReason.INITIALIZATION_FAILED
                self._append_invocation(
                    coordinator,
                    self._event(
                        'initialization_failed',
                        EventPhase.INITIALIZATION,
                        {
                            'submission_count': initial_submission_count,
                            'application_feasible_count': assessment_counts[
                                ApplicationAssessment.VALID
                            ],
                            'eligible_count': 0,
                            'reason': (
                                'no_application_feasible_solution_within_objective_bounds'
                            ),
                        },
                    ),
                )
                termination = self._stop_and_finalize(
                    coordinator,
                    state,
                    initial_archive_ids,
                    termination,
                    counters,
                    started,
                    wall_limit,
                    diagnostics,
                )
                finalization, finalization_failure = self._finalize(
                    coordinator.run_log,
                    initial_archive_ids,
                    state,
                    termination,
                    counters,
                )
                if finalization_failure is not None:
                    diagnostics.append(finalization_failure)
                raise InitialSolutionsError(
                    invocation_id=invocation_id,
                    candidate_outcomes=coordinator.candidate_outcomes,
                    run_log=coordinator.run_log,
                    executive_summary=(
                        finalization.executive_summary
                        if finalization is not None
                        else None
                    ),
                    diagnostics=tuple((*diagnostics, *coordinator.diagnostics)),
                    event_log_path=(
                        finalization.event_log_path
                        if finalization is not None
                        else None
                    ),
                    summary_path=(
                        finalization.summary_path if finalization is not None else None
                    ),
                    report_diagnostics=(
                        finalization.report_diagnostics
                        if finalization is not None
                        else ()
                    ),
                    registry_view_path=(
                        finalization.registry_view_path
                        if finalization is not None
                        else None
                    ),
                    pareto_view_path=(
                        finalization.pareto_view_path
                        if finalization is not None
                        else None
                    ),
                )
            else:
                state = cast(_SearchSnapshot, coordinator.search_state)
                initial_archive_ids = tuple(state.archive_ids)
                ready_for_checkpoint = True
                self._append_invocation(
                    coordinator,
                    self._event(
                        'initialization_completed',
                        EventPhase.INITIALIZATION,
                        {
                            'submission_count': initial_submission_count,
                            'valid_count': assessment_counts[
                                ApplicationAssessment.VALID
                            ],
                            'eligible_count': eligible_initial_count,
                            'invalid_count': assessment_counts[
                                ApplicationAssessment.INVALID
                            ],
                            'infeasible_count': assessment_counts[
                                ApplicationAssessment.INFEASIBLE
                            ],
                            'failed_count': initial_submission_count
                            - sum(assessment_counts.values()),
                            'registry_size': len(state.registry.records),
                            'archive_size': len(state.archive_ids),
                            'active_pool_size': len(state.active_ids),
                        },
                    ),
                )

        if ready_for_checkpoint and checkpoint_progress.interval is not None:
            checkpoint_failure = self._save_checkpoint_if_needed(
                state,
                checkpoint_progress,
                coordinator,
                force_final=False,
            )
            if checkpoint_failure is not None:
                diagnostics.append(checkpoint_failure)
                logging_failure = self._try_append_failure_event(
                    coordinator, checkpoint_failure
                )
                if logging_failure is not None:
                    diagnostics.append(logging_failure)
                termination = TerminationReason.COMPONENT_FAILURE

        # The initial set is fully processed before a graceful stop is honored.
        if (
            termination is not TerminationReason.COMPONENT_FAILURE
            and ready_for_checkpoint
        ):
            if self._elapsed(started) >= wall_limit:
                termination = TerminationReason.BUDGET_EXHAUSTED
            elif stop_token is not None and stop_token.is_stop_requested():
                termination = TerminationReason.INTERRUPTED
            else:
                termination, counters = self._search(
                    state,
                    coordinator,
                    counters,
                    diagnostics,
                    started,
                    wall_limit,
                    progress_interval,
                    stop_token,
                    checkpoint_progress,
                    snapshot_progress,
                )
                state = cast(_SearchSnapshot, coordinator.search_state)

        state = cast(_SearchSnapshot, coordinator.search_state)
        if (
            state.archive_ids
            and snapshot_progress.latest_successful_path is None
            and not snapshot_progress.failed
        ):
            snapshot_failure = self._save_pareto_snapshot(state, snapshot_progress)
            if snapshot_failure is not None:
                diagnostics.append(snapshot_failure)
                logging_failure = self._try_append_failure_event(
                    coordinator, snapshot_failure
                )
                if logging_failure is not None:
                    diagnostics.append(logging_failure)
                snapshot_progress.failed = True
                termination = TerminationReason.COMPONENT_FAILURE

        if ready_for_checkpoint and self._checkpoint_store is not None:
            if termination is not TerminationReason.COMPONENT_FAILURE:
                save_failure = self._save_checkpoint_if_needed(
                    state,
                    checkpoint_progress,
                    coordinator,
                    force_final=True,
                )
                if save_failure is not None:
                    diagnostics.append(save_failure)
                    logging_failure = self._try_append_failure_event(
                        coordinator, save_failure
                    )
                    if logging_failure is not None:
                        diagnostics.append(logging_failure)
                    termination = TerminationReason.COMPONENT_FAILURE
            checkpoint_reference = checkpoint_progress.reference

        state = cast(_SearchSnapshot, coordinator.search_state)
        termination = self._stop_and_finalize(
            coordinator,
            state,
            initial_archive_ids,
            termination,
            counters,
            started,
            wall_limit,
            diagnostics,
        )
        finalized, finalization_failure = self._finalize(
            coordinator.run_log,
            initial_archive_ids,
            state,
            termination,
            counters,
        )
        if finalization_failure is not None:
            diagnostics.append(finalization_failure)
        if finalized is not None:
            diagnostics.extend(coordinator.diagnostics)
            return RunResult(
                state,
                coordinator.candidate_outcomes,
                termination,
                tuple(diagnostics),
                counters,
                coordinator.run_log,
                finalized.executive_summary,
                finalized.report_diagnostics,
                checkpoint_reference,
                finalized.event_log_path,
                finalized.summary_path,
                snapshot_progress.latest_successful_path,
                finalized.registry_view_path,
                finalized.pareto_view_path,
            )
        return RunResult(
            state,
            coordinator.candidate_outcomes,
            termination,
            tuple((*diagnostics, *coordinator.diagnostics)),
            counters,
            coordinator.run_log,
            checkpoint_reference=checkpoint_reference,
            pareto_snapshot_path=snapshot_progress.latest_successful_path,
        )

    def _search(
        self,
        state: _SearchSnapshot,
        coordinator: SearchCommitCoordinator,
        counters: InvocationCounters,
        diagnostics: list[ComponentFailure],
        started: float,
        wall_limit: float,
        progress_interval: float,
        stop_token: StopToken | None,
        checkpoint_progress: _CheckpointProgress,
        snapshot_progress: _ParetoSnapshotProgress,
    ) -> tuple[TerminationReason, InvocationCounters]:
        committed = coordinator
        disabled_choices: set[ChoiceId] = set()
        disabled_operators: set[OperatorId] = set()
        reported_choice_ids: set[ChoiceId] = set()
        last_progress = started
        while True:
            if self._stop_requested(stop_token):
                return TerminationReason.INTERRUPTED, counters
            if self._hard_limit(counters, started, wall_limit):
                return TerminationReason.BUDGET_EXHAUSTED, counters

            budget_result = self._budget_decision(counters, started)
            if isinstance(budget_result, ComponentFailure):
                diagnostics.append(budget_result)
                self._append_failure_event(
                    committed,
                    budget_result,
                    base_id=None,
                    k=None,
                    component_position=None,
                )
                return TerminationReason.COMPONENT_FAILURE, counters
            request, budget = budget_result
            self._append_invocation(
                committed,
                self._event(
                    'budget_decision',
                    EventPhase.BUDGET,
                    {'request': request, 'decision': budget},
                    source_id=self._budget_policy.component_id,
                ),
            )
            if not budget.allowed:
                return TerminationReason.BUDGET_POLICY_STOP, counters

            if self._progress_due(started, last_progress, progress_interval):
                last_progress = self._emit_progress(committed, started, wall_limit)
            if self._hard_limit(counters, started, wall_limit):
                return TerminationReason.BUDGET_EXHAUSTED, counters

            try:
                base_record, base_source, eligible_base_count = self._select_base(
                    state,
                    state.base_selection_context,
                    committed,
                    counters,
                    disabled_choices,
                    disabled_operators,
                )
            except _NoEligibleBase:
                return TerminationReason.NO_AVAILABLE_OPERATOR, counters
            except _RandomFailure as error:
                diagnostics.append(error.failure)
                self._append_failure_event(
                    committed,
                    error.failure,
                    base_id=None,
                    k=None,
                    component_position=None,
                )
                return TerminationReason.COMPONENT_FAILURE, counters
            except _TransitionFailure as error:
                diagnostics.append(error.failure)
                self._append_failure_event(
                    committed,
                    error.failure,
                    base_id=None,
                    k=None,
                    component_position=None,
                )
                return TerminationReason.COMPONENT_FAILURE, counters

            excursion = state.excursion
            restoration_required = (
                excursion is not None
                and excursion.step_count >= cast(int, self._config.max_infeasible_steps)
            )
            iteration = counters.candidate_attempts + 1
            self._append_invocation(
                committed,
                self._event(
                    'base_selected',
                    EventPhase.BASE_SELECTION,
                    {
                        'iteration': iteration,
                        'eligible_base_count': eligible_base_count,
                        'selection_source': base_source,
                    },
                    solution_id=base_record.solution_id,
                ),
            )

            choices = self._choices(
                state,
                base_record.solution_id,
                restoration_required,
                disabled_choices,
                disabled_operators,
            )
            if not choices:
                return (
                    TerminationReason.NO_AVAILABLE_RESTORATION_OPERATOR
                    if restoration_required
                    else TerminationReason.NO_AVAILABLE_OPERATOR,
                    counters,
                )
            standalone_choices = tuple(
                choice
                for choice in choices
                if choice.descriptor.choice_id.kind is ChoiceKind.OPERATOR
            )
            composition_choices = tuple(
                choice
                for choice in choices
                if choice.descriptor.choice_id.kind is ChoiceKind.COMPOSITION
            )
            choice_descriptors = tuple(choice.descriptor for choice in choices)
            newly_eligible_choices = tuple(
                descriptor
                for descriptor in choice_descriptors
                if descriptor.choice_id not in reported_choice_ids
            )
            reported_choice_ids.update(
                descriptor.choice_id for descriptor in newly_eligible_choices
            )
            self._append_invocation(
                committed,
                self._event(
                    'choice_set_available',
                    EventPhase.CHOICE_SELECTION,
                    {
                        'iteration': iteration,
                        'restoration_required': restoration_required,
                        'eligible_choice_count': len(choices),
                        'eligible_standalone_count': len(standalone_choices),
                        'eligible_composition_count': len(composition_choices),
                        'newly_eligible_choices': newly_eligible_choices,
                    },
                    solution_id=base_record.solution_id,
                ),
            )
            configured_probability = float(
                self._config.composition_selection_probability
            )
            selection_source = 'configured_probability'
            try:
                if standalone_choices and composition_choices:
                    if configured_probability == 0.0:
                        category = OperatorChoiceCategory.STANDALONE
                    elif configured_probability == 1.0:
                        category = OperatorChoiceCategory.COMPOSITION
                    else:
                        category_draw = self._checked_random.random()
                        category = (
                            OperatorChoiceCategory.COMPOSITION
                            if category_draw < configured_probability
                            else OperatorChoiceCategory.STANDALONE
                        )
                    selected_choices = (
                        composition_choices
                        if category is OperatorChoiceCategory.COMPOSITION
                        else standalone_choices
                    )
                    selection_probability = (
                        configured_probability
                        if category is OperatorChoiceCategory.COMPOSITION
                        else 1.0 - configured_probability
                    )
                elif composition_choices:
                    category = OperatorChoiceCategory.COMPOSITION
                    selected_choices = composition_choices
                    selection_probability = 1.0
                    selection_source = 'only_available_category'
                elif standalone_choices:
                    category = OperatorChoiceCategory.STANDALONE
                    selected_choices = standalone_choices
                    selection_probability = 1.0
                    selection_source = 'only_available_category'
                else:
                    raise ValueError('eligible choices have no recognized category')
                self._append_invocation(
                    committed,
                    self._event(
                        'choice_category_selected',
                        EventPhase.CHOICE_SELECTION,
                        {
                            'iteration': iteration,
                            'category': category,
                            'eligible_choice_count': len(selected_choices),
                            'selection_probability': selection_probability,
                            'selection_source': selection_source,
                        },
                        solution_id=base_record.solution_id,
                    ),
                )
                category_descriptors = tuple(
                    choice.descriptor for choice in selected_choices
                )
                probabilities = self._feedback_policy.distribution(
                    ChoiceSelectionRequest(category, category_descriptors)
                )
                selected_choice = self._sample_choice(selected_choices, probabilities)
            except _RandomFailure as error:
                diagnostics.append(error.failure)
                self._append_failure_event(
                    committed,
                    error.failure,
                    base_id=base_record.solution_id,
                    k=None,
                    component_position=None,
                )
                return TerminationReason.COMPONENT_FAILURE, counters
            except Exception as error:
                failure = self._failure(
                    self._feedback_policy,
                    'policy_decision',
                    f'FeedbackPolicy selection failed: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                )
                diagnostics.append(failure)
                self._append_failure_event(
                    committed,
                    failure,
                    base_id=base_record.solution_id,
                    k=None,
                    component_position=None,
                )
                return TerminationReason.COMPONENT_FAILURE, counters

            k = selected_choice.descriptor.current_k
            remaining_requests = (
                self._config.budget_limits.max_evaluation_requests
                - counters.evaluation_requests
            )
            call_candidate_limit = min(
                self._config.max_candidates_per_attempt,
                remaining_requests,
            )
            if call_candidate_limit <= 0:
                return TerminationReason.BUDGET_EXHAUSTED, counters
            if self._hard_limit(counters, started, wall_limit):
                return TerminationReason.BUDGET_EXHAUSTED, counters
            self._append_invocation(
                committed,
                self._event(
                    'choice_selected',
                    EventPhase.CHOICE_SELECTION,
                    {
                        'iteration': iteration,
                        'operator_ids': selected_choice.descriptor.operator_ids,
                        'k': k,
                        'max_neighborhood_size': (
                            selected_choice.descriptor.max_neighborhood_size
                        ),
                        'max_candidates': call_candidate_limit,
                        'restoration_required': restoration_required,
                    },
                    choice_id=selected_choice.descriptor.choice_id,
                    solution_id=base_record.solution_id,
                    source_id=self._feedback_policy.component_id,
                ),
            )
            counters = replace(
                counters, candidate_attempts=counters.candidate_attempts + 1
            )
            previous_promotion_count = len(state.promoted_operators)
            attempt = self._attempt(
                state,
                base_record,
                selected_choice,
                k,
                restoration_required,
                call_candidate_limit,
                counters,
                committed,
                diagnostics,
            )
            if attempt[0] is not None:
                state = attempt[0]
            if attempt[1] > 0:
                counters = replace(
                    counters,
                    evaluation_requests=(counters.evaluation_requests + attempt[1]),
                )

            if attempt[0] is not None and attempt[4]:
                view_failure = self._publish_view(
                    invocation_id=committed.run_log.invocation_id,
                    state=state,
                    scope='pareto',
                    is_final=False,
                )
                if view_failure is not None:
                    diagnostics.append(view_failure)
                    logging_failure = self._try_append_failure_event(
                        committed, view_failure
                    )
                    if logging_failure is not None:
                        diagnostics.append(logging_failure)
                    return TerminationReason.COMPONENT_FAILURE, counters
                snapshot_failure = self._save_pareto_snapshot(state, snapshot_progress)
                if snapshot_failure is not None:
                    diagnostics.append(snapshot_failure)
                    logging_failure = self._try_append_failure_event(
                        committed, snapshot_failure
                    )
                    if logging_failure is not None:
                        diagnostics.append(logging_failure)
                    snapshot_progress.failed = True
                    return TerminationReason.COMPONENT_FAILURE, counters

            if (
                attempt[0] is not None
                and len(state.promoted_operators) > previous_promotion_count
            ):
                catalog_failure = self._save_promoted_catalog(state)
                if catalog_failure is not None:
                    diagnostics.append(catalog_failure)
                    logging_failure = self._try_append_failure_event(
                        committed, catalog_failure
                    )
                    if logging_failure is not None:
                        diagnostics.append(logging_failure)
                    return TerminationReason.COMPONENT_FAILURE, counters

            if attempt[0] is not None and checkpoint_progress.interval is not None:
                checkpoint_failure = self._save_checkpoint_if_needed(
                    state,
                    checkpoint_progress,
                    committed,
                    force_final=False,
                )
                if checkpoint_failure is not None:
                    diagnostics.append(checkpoint_failure)
                    logging_failure = self._try_append_failure_event(
                        committed, checkpoint_failure
                    )
                    if logging_failure is not None:
                        diagnostics.append(logging_failure)
                    return TerminationReason.COMPONENT_FAILURE, counters

            failure_status = attempt[6]
            if failure_status == 'component_failure':
                return TerminationReason.COMPONENT_FAILURE, counters
            if failure_status == 'operator_failure':
                if selected_choice.descriptor.choice_id.kind is ChoiceKind.OPERATOR:
                    disabled_operators.add(
                        cast(
                            OperatorId, selected_choice.descriptor.choice_id.operator_id
                        )
                    )
                else:
                    disabled_choices.add(selected_choice.descriptor.choice_id)

            if self._progress_due(started, last_progress, progress_interval):
                last_progress = self._emit_progress(committed, started, wall_limit)
            if self._stop_requested(stop_token):
                return TerminationReason.INTERRUPTED, counters
            if self._hard_limit(counters, started, wall_limit):
                return TerminationReason.BUDGET_EXHAUSTED, counters

    def _attempt(
        self,
        state: _SearchSnapshot,
        base: SolutionRecord,
        choice: _Choice,
        k: int,
        restoration_required: bool,
        max_candidates: int,
        counters: InvocationCounters,
        coordinator: SearchCommitCoordinator,
        diagnostics: list[ComponentFailure],
    ) -> tuple[
        _SearchSnapshot | None,
        int,
        int | None,
        bool,
        tuple[SolutionId, ...],
        bool,
        str | None,
    ]:
        choice_id = choice.descriptor.choice_id
        try:
            pre_random = self._capture_random()
            pre_feedback = self._capture_feedback()
        except Exception as error:
            failure = (
                error.failure
                if isinstance(error, _TransitionFailure)
                else self._failure(
                    self._feedback_policy,
                    'policy_state_capture',
                    f'FeedbackPolicy.capture_state failed: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                    choice_id=choice_id,
                )
            )
            diagnostics.append(failure)
            self._append_failure_event(
                coordinator,
                failure,
                base_id=base.solution_id,
                k=k,
                component_position=None,
                choice_id=choice_id,
            )
            return None, 0, None, False, (), False, 'component_failure'
        state_payloads = dict(state.operator_payloads)
        component_events: list[EventDraft] = []
        failures: list[ComponentFailure] = []
        component_call_index = [0]
        composition_parts = self._composition_parts(choice)
        composition_stats = {'first_candidate_count': 0, 'second_call_count': 0}
        active_operator = choice.first
        random_failure = False
        operator_failure = False
        result: OperatorResult | None = None
        try:
            if composition_parts is None:
                result = self._call_operator_spec(
                    choice.first,
                    base.solution,
                    k,
                    self._config.feasibility_mode == 'strict' or restoration_required,
                    state_payloads,
                    base.evaluation_record,
                    max_candidates=max_candidates,
                    choice_id=choice_id,
                    base_id=base.solution_id,
                    component_position=None,
                    input_candidate_index=None,
                    component_events=component_events,
                    component_call_index=component_call_index,
                )
            else:
                first_spec, second_spec, _ = composition_parts
                active_operator = first_spec
                result, composition_stats = self._call_operator_pair(
                    first_spec,
                    second_spec,
                    base.solution,
                    k,
                    self._config.feasibility_mode == 'strict' or restoration_required,
                    state_payloads,
                    base.evaluation_record,
                    max_candidates=max_candidates,
                    choice_id=choice_id,
                    base_id=base.solution_id,
                    component_events=component_events,
                    component_call_index=component_call_index,
                )
        except _OperatorCallFailure as error:
            if error.state_captured:
                state_payloads[error.operator_id] = error.state_payload
            failures.append(replace(error.failure, choice_id=choice_id))
            random_failure = error.random_failure
            operator_failure = True
        except _TransitionFailure as error:
            diagnostics.append(replace(error.failure, choice_id=choice_id))
            self._append_failure_event(
                coordinator,
                replace(error.failure, choice_id=choice_id),
                base_id=base.solution_id,
                k=k,
                component_position=None,
                choice_id=choice_id,
            )
            self._restore_feedback(pre_feedback)
            self._restore_random(pre_random)
            self._restore_operator_payloads(state_payloads, state.operator_payloads)
            return (
                None,
                int(error.evaluation_requested),
                None,
                False,
                (),
                False,
                ('component_failure'),
            )
        except Exception as error:
            failures.append(
                ComponentFailure(
                    FailureKind.CALLBACK_EXCEPTION,
                    active_operator.operator_id,
                    'operator_call',
                    f'Operator.call failed: {error}',
                    choice_id=choice_id,
                )
            )
            operator_failure = True

        if composition_parts is not None:
            first_spec, second_spec, execution_source = composition_parts
            component_events.append(
                self._event(
                    'composition_result',
                    EventPhase.COMPOSITION,
                    {
                        'k': k,
                        'call_status': 'failed' if operator_failure else 'completed',
                        'final_candidate_count': (
                            0 if result is None else len(result.candidates)
                        ),
                        'final_modification_counts': (
                            None
                            if result is None
                            else tuple(item.m for item in result.candidates)
                        ),
                        'first_candidate_count': composition_stats[
                            'first_candidate_count'
                        ],
                        'second_call_count': composition_stats['second_call_count'],
                        'execution_source': execution_source,
                        'component_operator_ids': (
                            first_spec.operator_id,
                            second_spec.operator_id,
                        ),
                    },
                    choice_id=choice_id,
                    solution_id=base.solution_id,
                )
            )

        if operator_failure:
            if not random_failure:
                self._restore_random(pre_random)
                self._restore_operator_payloads(state_payloads, state.operator_payloads)
            for failure in failures:
                diagnostics.append(failure)
                component_events.append(
                    self._component_failure_event(
                        failure,
                        base_id=base.solution_id,
                        k=k,
                        component_position=None,
                        choice_id=choice_id,
                    )
                )
            failed_result = self._commit_attempt_result(
                state,
                base,
                choice,
                k,
                max_candidates,
                modification_counts=None,
                outcomes=(),
                failures=tuple(failures),
                candidate_events=(),
                component_events=tuple(component_events),
                state_payloads=state_payloads,
                pre_random=pre_random,
                pre_feedback=pre_feedback,
                counters=counters,
                coordinator=coordinator,
                diagnostics=diagnostics,
                terminal_failure=None,
                evaluation_requests=0,
                excursion_events=(),
            )
            if failed_result[0] is None:
                return failed_result
            return (
                *failed_result[:6],
                'component_failure' if random_failure else 'operator_failure',
            )

        assert result is not None
        modification_counts = tuple(proposal.m for proposal in result.candidates)
        working_state = state
        outcomes: list[CandidateOutcome] = []
        candidate_events: list[EventDraft] = []
        excursion_events: list[EventDraft] = []
        admissions: list[SolutionId] = []
        evaluation_requests = 0
        guarantee_failure = False
        terminal_failure: ComponentFailure | None = None
        final_component_position = (
            CompositionPosition.SECOND if composition_parts is not None else None
        )
        # Advance the staged state after each proposal. Archive admissions
        # record attempt success while the loop processes the remaining list.
        for candidate_index, proposal in enumerate(result.candidates, start=1):
            try:
                submission = self._prepare_submission(
                    working_state,
                    proposal.candidate,
                    source=SubmissionSource.OPERATOR,
                    counters=counters,
                    choice_id=choice_id,
                    base_id=base.solution_id,
                    k=k,
                    m=proposal.m,
                    component_position=final_component_position,
                    candidate_index=candidate_index,
                    ensure_feasible=(
                        self._config.feasibility_mode == 'strict'
                        or restoration_required
                    ),
                )
            except _TransitionFailure as error:
                failure = replace(error.failure, choice_id=choice_id)
                evaluation_requests += int(error.evaluation_requested)
                if failure.phase in (
                    'objective_comparison',
                    'objective_bound_comparison',
                ):
                    diagnostics.append(failure)
                    failures.append(failure)
                    terminal_failure = failure
                    candidate_events.append(
                        self._component_failure_event(
                            failure,
                            base_id=base.solution_id,
                            k=k,
                            component_position=final_component_position,
                            choice_id=choice_id,
                        )
                    )
                    break
                diagnostics.append(failure)
                self._append_failure_event(
                    coordinator,
                    failure,
                    base_id=base.solution_id,
                    k=k,
                    component_position=final_component_position,
                    choice_id=choice_id,
                )
                self._restore_feedback(pre_feedback)
                self._restore_random(pre_random)
                self._restore_operator_payloads(state_payloads, state.operator_payloads)
                return (
                    None,
                    evaluation_requests,
                    proposal.m,
                    False,
                    (),
                    False,
                    ('component_failure'),
                )

            working_state = submission.state
            evaluation_requests += int(submission.evaluation_requested)
            outcomes.append(submission.outcome)
            failures.extend(submission.failures)
            guarantee_failure = guarantee_failure or any(
                failure.phase == 'operator_guarantee' for failure in submission.failures
            )
            candidate_events.extend(submission.events)
            if submission.excursion_event is not None:
                excursion_events.append(submission.excursion_event)
            if submission.admitted and submission.outcome.solution_id is not None:
                admissions.append(submission.outcome.solution_id)
            working_state, promotion_event = self._promote_composition(
                working_state, choice, submission.outcome
            )
            if promotion_event is not None:
                insertion_point = next(
                    (
                        index
                        for index, draft in enumerate(candidate_events)
                        if draft.event_type == 'archive_changed'
                    ),
                    next(
                        (
                            index
                            for index, draft in enumerate(candidate_events)
                            if draft.event_type == 'candidate_processed'
                        ),
                    ),
                )
                candidate_events.insert(insertion_point + 1, promotion_event)
            working_state = self._ensure_choice_cursors(working_state)

        outcome_tuple = tuple(outcomes)
        admission_count = len(admissions)
        application_success = admission_count > 0
        observation = FeedbackObservation(
            choice_id=choice_id,
            k=k,
            modification_counts=modification_counts,
            outcomes=outcome_tuple,
            failures=tuple(failures),
            archive_admission_count=admission_count,
        )
        committed = self._commit_attempt_result(
            working_state,
            base,
            choice,
            k,
            max_candidates,
            modification_counts=modification_counts,
            outcomes=outcome_tuple,
            failures=tuple(failures),
            candidate_events=tuple(candidate_events),
            component_events=tuple(component_events),
            state_payloads=state_payloads,
            pre_random=pre_random,
            pre_feedback=pre_feedback,
            counters=counters,
            coordinator=coordinator,
            diagnostics=diagnostics,
            terminal_failure=terminal_failure,
            evaluation_requests=evaluation_requests,
            excursion_events=tuple(excursion_events),
            observation=observation,
        )
        if committed[0] is None:
            return committed
        return (
            committed[0],
            committed[1],
            modification_counts[0] if modification_counts else 0,
            application_success,
            tuple(admissions),
            bool(excursion_events),
            'component_failure'
            if terminal_failure is not None
            else 'operator_failure'
            if guarantee_failure
            else None,
        )

    def _commit_attempt_result(
        self,
        state: _SearchSnapshot,
        base: SolutionRecord,
        choice: _Choice,
        k: int,
        max_candidates: int,
        *,
        modification_counts: tuple[int, ...] | None,
        outcomes: tuple[CandidateOutcome, ...],
        failures: tuple[ComponentFailure, ...],
        candidate_events: tuple[EventDraft, ...],
        component_events: tuple[EventDraft, ...],
        state_payloads: dict[OperatorId, object],
        pre_random: object | None,
        pre_feedback: object | None,
        counters: InvocationCounters,
        coordinator: SearchCommitCoordinator,
        diagnostics: list[ComponentFailure],
        terminal_failure: ComponentFailure | None,
        evaluation_requests: int,
        excursion_events: tuple[EventDraft, ...],
        observation: FeedbackObservation | None = None,
    ) -> tuple[
        _SearchSnapshot | None,
        int,
        int | None,
        bool,
        tuple[SolutionId, ...],
        bool,
        str | None,
    ]:
        """Commit one completed or failed ordered candidate attempt."""
        del max_candidates, counters
        choice_id = choice.descriptor.choice_id
        archived_count = sum(
            outcome.disposition is SearchDisposition.ARCHIVED for outcome in outcomes
        )
        if observation is None:
            observation = FeedbackObservation(
                choice_id=choice_id,
                k=k,
                modification_counts=modification_counts,
                outcomes=outcomes,
                failures=failures,
                archive_admission_count=archived_count,
            )
        try:
            self._feedback_policy.observe(observation)
            feedback_payload = self._capture_feedback()
            random_payload = self._capture_random()
        except Exception as error:
            self._restore_feedback(pre_feedback)
            self._restore_random(pre_random)
            self._restore_operator_payloads(state_payloads, state.operator_payloads)
            failure = self._failure(
                self._feedback_policy,
                'policy_decision',
                f'FeedbackPolicy.observe or state capture failed: {error}',
                FailureKind.CALLBACK_EXCEPTION,
                choice_id=choice_id,
            )
            diagnostics.append(failure)
            self._append_failure_event(
                coordinator,
                failure,
                base_id=base.solution_id,
                k=k,
                component_position=None,
                choice_id=choice_id,
            )
            return (
                None,
                evaluation_requests,
                None,
                False,
                (),
                False,
                'component_failure',
            )

        committed_admission = archived_count > 0
        cursor_base = base.solution_id
        next_state = replace(
            state,
            attempt_counts=self._increment_count(
                state.attempt_counts, base.solution_id
            ),
            tracked_choice_ids=state.tracked_choice_ids | {choice_id},
            operator_payloads=MappingProxyType(state_payloads),
            feedback_payload=feedback_payload,
            random_payload=random_payload,
            base_selection_context=(base.solution_id, committed_admission),
            attempts_since_archive_admission=(
                0 if committed_admission else state.attempts_since_archive_admission + 1
            ),
            commit_sequence=state.commit_sequence + 1,
        )
        next_state = self._ensure_choice_cursors(next_state)
        if cursor_base in next_state.archive_ids or (
            next_state.excursion is not None
            and next_state.excursion.current_id == cursor_base
        ):
            next_state = replace(
                next_state,
                choice_size_cursors=self._updated_choice_cursors(
                    next_state,
                    cursor_base,
                    choice_id,
                    k,
                    admitted=committed_admission,
                ),
            )

        start_sequence = len(coordinator.run_log.events)
        candidate_sequences = tuple(
            start_sequence + len(component_events) + index
            for index, draft in enumerate(candidate_events)
            if draft.event_type == 'candidate_processed'
        )
        component_failure_sequences = tuple(
            start_sequence + index
            for index, draft in enumerate(component_events)
            if draft.event_type == 'component_failure'
        )
        candidate_failure_sequences = tuple(
            start_sequence + len(component_events) + index
            for index, draft in enumerate(candidate_events)
            if draft.event_type == 'component_failure'
        )
        failure_sequences = (
            *component_failure_sequences,
            *candidate_failure_sequences,
        )
        feedback_event = self._event(
            'feedback_observed',
            EventPhase.FEEDBACK,
            {
                'k': k,
                'modification_counts': modification_counts,
                'candidate_event_sequences': candidate_sequences,
                'failure_event_sequences': failure_sequences,
                'archive_admission_count': archived_count,
                'attempt_admitted': committed_admission,
            },
            choice_id=choice_id,
            solution_id=base.solution_id,
            source_id=self._feedback_policy.component_id,
        )
        drafts = (
            *component_events,
            *candidate_events,
            *excursion_events,
            feedback_event,
        )
        try:
            coordinator.commit(next_state, drafts, outcomes)
        except Exception as error:
            self._restore_feedback(pre_feedback)
            self._restore_random(pre_random)
            self._restore_operator_payloads(state_payloads, state.operator_payloads)
            failure = self._failure(
                self._logger,
                'run_logging',
                f'candidate-attempt event commit failed: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
                choice_id=choice_id,
            )
            diagnostics.append(failure)
            return (
                None,
                evaluation_requests,
                None,
                False,
                (),
                False,
                'component_failure',
            )
        return (
            next_state,
            evaluation_requests,
            None,
            committed_admission,
            tuple(
                outcome.solution_id
                for outcome in outcomes
                if outcome.disposition is SearchDisposition.ARCHIVED
                and outcome.solution_id is not None
            ),
            bool(excursion_events),
            'component_failure' if terminal_failure is not None else None,
        )

    def _promote_composition(
        self,
        state: _SearchSnapshot,
        choice: _Choice,
        outcome: CandidateOutcome,
    ) -> tuple[_SearchSnapshot, EventDraft | None]:
        """Stage first-time promotion when a composition enters the archive."""
        if (
            choice.second is None
            or outcome.disposition is not SearchDisposition.ARCHIVED
            or outcome.solution_id is None
        ):
            return state, None
        composition_id = cast(CompositionId, choice.descriptor.choice_id.composition_id)
        if any(
            promoted.report.source_composition_id == composition_id
            for promoted in state.promoted_operators
        ):
            return state, None
        operator_id = self._new_promoted_operator_id(state, composition_id)
        report = PromotedCompositionReport(
            operator_id=operator_id,
            source_composition_id=composition_id,
            component_operator_ids=(
                choice.first.operator_id,
                choice.second.operator_id,
            ),
            first_admitted_solution_id=outcome.solution_id,
            max_neighborhood_size=choice.descriptor.max_neighborhood_size,
            feasibility_restoration=choice.descriptor.feasibility_restoration,
            inherited_group_ids=choice.descriptor.shared_group_ids,
            promoted_in_current_invocation=False,
        )
        promoted = _PromotedOperator(
            report,
            choice.descriptor.modification_count_definition,
        )
        next_state = replace(
            state,
            promoted_operators=(*state.promoted_operators, promoted),
        )
        next_state = self._ensure_choice_cursors(next_state)
        event = self._event(
            'composition_promoted',
            EventPhase.COMPOSITION,
            {
                'generated_operator_id': operator_id,
                'source_composition_id': composition_id,
                'component_operator_ids': report.component_operator_ids,
                'max_neighborhood_size': report.max_neighborhood_size,
                'feasibility_restoration': report.feasibility_restoration,
                'inherited_group_ids': tuple(
                    sorted(report.inherited_group_ids, key=identifier_text)
                ),
            },
            choice_id=choice.descriptor.choice_id,
            solution_id=outcome.solution_id,
        )
        return next_state, event

    def _new_promoted_operator_id(
        self, state: _SearchSnapshot, composition_id: CompositionId
    ) -> _PromotedOperatorId:
        """Derive a stable package ID with collision-free log text."""
        used_texts = {
            identifier_text(registration.operator_id)
            for registration in self._operators
        }
        used_texts.update(
            identifier_text(registration.operator_id)
            for registration in self._pre_registered_compositions
        )
        used_texts.update(
            identifier_text(promoted.report.operator_id)
            for promoted in state.promoted_operators
        )
        disambiguator = 0
        while True:
            generated = _PromotedOperatorId(composition_id, disambiguator)
            if identifier_text(generated) not in used_texts:
                return generated
            disambiguator += 1

    def _prepare_submission(
        self,
        state: _SearchSnapshot,
        candidate: Candidate,
        *,
        source: SubmissionSource,
        counters: InvocationCounters,
        choice_id: ChoiceId | None,
        base_id: SolutionId | None,
        k: int | None,
        m: int | None,
        component_position: CompositionPosition | None,
        candidate_index: int | None,
        ensure_feasible: bool | None,
    ) -> _Submission:
        result = self._candidate_processor.process(
            state.registry,
            candidate,
            source=source,
            choice_id=choice_id,
            base_solution_id=base_id,
            k=k,
            m=m,
            component_position=component_position,
            candidate_index=candidate_index,
        )
        new_state = replace(state, registry=result.registry)
        outcome = result.outcome
        failures = list(outcome.failures)
        archive_event: EventDraft | None = None
        pool_event: EventDraft | None = None
        admitted = False
        new_record = (
            outcome.solution_id is not None
            and outcome.disposition is not SearchDisposition.DUPLICATE
        )
        if (
            not new_record
            and outcome.solution_id is not None
            and outcome.disposition is SearchDisposition.DUPLICATE
        ):
            outcome = replace(
                outcome,
                objective_bound_findings=new_state.registry.get(
                    outcome.solution_id
                ).objective_bound_findings,
            )
        if new_record and outcome.solution_id is not None:
            record = new_state.registry.get(outcome.solution_id)
            evaluation = record.evaluation_record.application_evaluation
            assessment = record.evaluation_record.assessment
            try:
                bound_findings = self._objective_bound_findings(record)
            except _TransitionFailure as error:
                raise _TransitionFailure(
                    error.failure,
                    evaluation_requested=result.evaluation_requested,
                ) from error
            if bound_findings:
                record = replace(record, objective_bound_findings=bound_findings)
                new_state = replace(
                    new_state,
                    registry=new_state.registry.with_updated_record(record),
                )
                outcome = replace(
                    outcome,
                    disposition=SearchDisposition.OBJECTIVE_BOUND_EXCEEDED,
                    objective_bound_findings=bound_findings,
                )
            if (
                assessment is ApplicationAssessment.VALID
                and evaluation is not None
                and not bound_findings
            ):
                try:
                    decision, objective_vector_duplicate = self._archive_decision(
                        new_state, record
                    )
                except _TransitionFailure as error:
                    raise _TransitionFailure(
                        error.failure,
                        evaluation_requested=result.evaluation_requested,
                    ) from error
                archive_ids = set(new_state.archive_ids)
                status_changes: list[dict[str, object]] = []
                if decision.admit:
                    archive_ids.add(record.solution_id)
                    admitted = True
                    for removed_id in decision.remove_ids:
                        archive_ids.discard(removed_id)
                        removed = new_state.registry.get(removed_id)
                        status_changes.append(
                            {
                                'solution_id': removed_id,
                                'previous_status': removed.archive_status,
                                'new_status': ArchiveStatus.REJECTED,
                            }
                        )
                        new_state = replace(
                            new_state,
                            registry=new_state.registry.with_updated_record(
                                replace(removed, archive_status=ArchiveStatus.REJECTED)
                            ),
                        )
                    record = new_state.registry.get(record.solution_id)
                    new_record_value = replace(
                        record, archive_status=ArchiveStatus.PARETO_OPTIMAL
                    )
                    new_state = replace(
                        new_state,
                        registry=new_state.registry.with_updated_record(
                            new_record_value
                        ),
                        archive_ids=frozenset(archive_ids),
                    )
                    outcome = replace(outcome, disposition=SearchDisposition.ARCHIVED)
                    status_changes.append(
                        {
                            'solution_id': record.solution_id,
                            'previous_status': record.archive_status,
                            'new_status': ArchiveStatus.PARETO_OPTIMAL,
                        }
                    )
                else:
                    if objective_vector_duplicate:
                        warnings.warn(
                            'Distinct visited plans share a Pareto objective vector. '
                            'This may indicate search symmetry and can divert '
                            'evaluations from discovering new objective trade-offs.',
                            UserWarning,
                            stacklevel=2,
                        )
                    archive_status = (
                        ArchiveStatus.OBJECTIVE_VECTOR_DUPLICATE
                        if objective_vector_duplicate
                        else ArchiveStatus.DOMINATED
                    )
                    disposition = (
                        SearchDisposition.OBJECTIVE_VECTOR_DUPLICATE
                        if objective_vector_duplicate
                        else SearchDisposition.DOMINATED
                    )
                    new_record_value = replace(record, archive_status=archive_status)
                    new_state = replace(
                        new_state,
                        registry=new_state.registry.with_updated_record(
                            new_record_value
                        ),
                    )
                    outcome = replace(outcome, disposition=disposition)
                    status_changes.append(
                        {
                            'solution_id': record.solution_id,
                            'previous_status': record.archive_status,
                            'new_status': archive_status,
                        }
                    )
                if status_changes:
                    archive_event = self._event(
                        'archive_changed',
                        EventPhase.ARCHIVE,
                        {
                            'cause_solution_id': record.solution_id,
                            'status_changes': tuple(
                                sorted(
                                    status_changes,
                                    key=lambda item: identifier_text(
                                        cast(SolutionId, item['solution_id'])
                                    ),
                                )
                            ),
                        },
                        solution_id=record.solution_id,
                    )

            if (
                source is SubmissionSource.OPERATOR
                and ensure_feasible is not None
                and outcome.solution_id is not None
            ):
                guarantee = self._operator_guarantee_failure(
                    new_state.registry.get(outcome.solution_id),
                    ensure_feasible,
                    choice_id,
                )
                if guarantee is not None:
                    failures.append(guarantee)
                    stored = new_state.registry.get(outcome.solution_id)
                    updated = replace(
                        stored,
                        operator_failures=(*stored.operator_failures, guarantee),
                    )
                    new_state = replace(
                        new_state,
                        registry=new_state.registry.with_updated_record(updated),
                    )
                    outcome = replace(outcome, failures=tuple(failures))

            eligible = self._eligible_pool_ids(new_state)
            request = ActivePoolRequest(
                new_state.registry.records,
                eligible,
                frozenset((new_state.active_ids & eligible) | new_state.archive_ids),
                new_state.archive_ids,
                PoolAction.RECONCILE,
                outcome.solution_id,
            )
            try:
                pool_decision = self._exploration_policy.decide(request)
            except Exception as error:
                raise _TransitionFailure(
                    self._failure(
                        self._exploration_policy,
                        'policy_decision',
                        f'ExplorationPolicy.decide raised: {error}',
                        FailureKind.CALLBACK_EXCEPTION,
                    ),
                    evaluation_requested=result.evaluation_requested,
                ) from error
            if not isinstance(pool_decision, ActivePoolDecision):
                raise _TransitionFailure(
                    self._failure(
                        self._exploration_policy,
                        'policy_decision',
                        'ExplorationPolicy.decide returned an invalid record',
                        FailureKind.CONTRACT_VIOLATION,
                    ),
                    evaluation_requested=result.evaluation_requested,
                )
            if (
                not request.archive_ids
                <= pool_decision.active_ids
                <= request.eligible_ids
            ):
                raise _TransitionFailure(
                    self._failure(
                        self._exploration_policy,
                        'policy_decision',
                        'ExplorationPolicy returned ineligible or incomplete membership',
                        FailureKind.CONTRACT_VIOLATION,
                    ),
                    evaluation_requested=result.evaluation_requested,
                )
            added = tuple(
                sorted(
                    pool_decision.active_ids - new_state.active_ids, key=identifier_text
                )
            )
            removed_ids = tuple(
                sorted(
                    new_state.active_ids - pool_decision.active_ids, key=identifier_text
                )
            )
            new_state = replace(new_state, active_ids=pool_decision.active_ids)
            pool_event = self._event(
                'active_pool_changed',
                EventPhase.ACTIVE_POOL,
                {
                    'action': PoolAction.RECONCILE,
                    'changed_solution_id': outcome.solution_id,
                    'added_solution_ids': added,
                    'removed_solution_ids': removed_ids,
                },
                solution_id=outcome.solution_id,
            )

        if outcome.solution_id is None and outcome.failures:
            failures.extend(
                failure
                for failure in result.invocation_diagnostics
                if failure not in failures
            )
        if source is SubmissionSource.OPERATOR and outcome.solution_id is not None:
            evaluation_record = new_state.registry.get(
                outcome.solution_id
            ).evaluation_record
            app_eval = evaluation_record.application_evaluation
            guarantee_failed = any(
                failure.phase == 'operator_guarantee' for failure in outcome.failures
            )
            if (
                app_eval is not None
                and app_eval.representation_valid
                and app_eval.feasible is not None
                and not guarantee_failed
            ):
                new_excursion, excursion_event = self._update_excursion(
                    state,
                    new_state,
                    outcome,
                    app_eval.feasible,
                    cast(int, m),
                    base_id,
                )
                new_state = replace(new_state, excursion=new_excursion)
            else:
                new_excursion = state.excursion
                new_state = replace(new_state, excursion=state.excursion)
                excursion_event = None
        else:
            new_excursion = state.excursion
            excursion_event = None

        event_drafts: list[EventDraft] = []
        for failure in outcome.failures:
            origin = (
                'stored_evaluation'
                if (
                    outcome.disposition is SearchDisposition.DUPLICATE
                    and failure
                    in (
                        new_state.registry.get(
                            outcome.solution_id
                        ).evaluation_record.failures
                        if outcome.solution_id is not None
                        else ()
                    )
                )
                else 'current_operation'
            )
            event_drafts.append(
                self._component_failure_event(
                    failure,
                    base_id=base_id,
                    k=k,
                    component_position=component_position,
                    choice_id=choice_id,
                    diagnostic_origin=origin,
                )
            )
        # Candidate event sequence links are assigned when the commit is staged.
        event_drafts.append(
            self._event(
                'candidate_processed',
                EventPhase.CANDIDATE_PROCESSING,
                {
                    'source': source,
                    'processing_status': outcome.processing_status,
                    'base_solution_id': base_id,
                    'candidate_index': candidate_index,
                    'k': k,
                    'm': m,
                    'component_position': component_position,
                    'evaluation_source': (
                        'new'
                        if result.evaluation_requested
                        else 'reused'
                        if outcome.disposition is SearchDisposition.DUPLICATE
                        else 'none'
                    ),
                    'assessment': outcome.assessment,
                    'disposition': outcome.disposition,
                    'objective_bound_findings': self._format_bound_event_findings(
                        outcome.objective_bound_findings
                    ),
                    'failure_event_sequences': (),
                },
                choice_id=choice_id,
                solution_id=outcome.solution_id,
            )
        )

        if archive_event is not None:
            event_drafts.append(archive_event)
        if pool_event is not None:
            event_drafts.append(pool_event)
        if result.invocation_diagnostics:
            failures.extend(
                failure
                for failure in result.invocation_diagnostics
                if failure not in failures
            )
        return _Submission(
            new_state,
            outcome,
            tuple(event_drafts),
            admitted,
            tuple(failures),
            result.evaluation_requested,
            new_excursion,
            excursion_event,
        )

    def _objective_bound_findings(
        self, record: SolutionRecord
    ) -> tuple[ObjectiveBoundFinding, ...]:
        """Return exceeded bounds after completed application evaluation."""
        evaluation_record = record.evaluation_record
        if (
            evaluation_record.processing_status is not ProcessingStatus.COMPLETED
            or evaluation_record.assessment
            not in (ApplicationAssessment.VALID, ApplicationAssessment.INFEASIBLE)
        ):
            return ()
        evaluation = evaluation_record.application_evaluation
        if evaluation is None or evaluation.objective_values is None:
            return ()
        bounds = cast(
            tuple[ObjectiveValue | None, ...],
            self._objective_declaration.upper_bounds,
        )
        findings: list[ObjectiveBoundFinding] = []
        for objective, value, bound in zip(
            self._objective_declaration.objectives,
            evaluation.objective_values,
            bounds,
            strict=True,
        ):
            if bound is None:
                continue
            try:
                comparison = objective.compare(value, bound)
            except Exception as error:
                raise _TransitionFailure(
                    self._failure(
                        objective.component_id,
                        'objective_bound_comparison',
                        f'ObjectiveFunction.compare raised while checking bound: {error}',
                        FailureKind.CALLBACK_EXCEPTION,
                        solution_id=record.solution_id,
                    )
                ) from error
            if not isinstance(comparison, ObjectiveComparison):
                raise _TransitionFailure(
                    self._failure(
                        objective.component_id,
                        'objective_bound_comparison',
                        'ObjectiveFunction.compare returned an invalid result while checking bound',
                        FailureKind.CONTRACT_VIOLATION,
                        solution_id=record.solution_id,
                    )
                )
            if comparison is ObjectiveComparison.GREATER:
                findings.append(
                    ObjectiveBoundFinding(objective.objective_id, value, bound)
                )
        return tuple(findings)

    def _format_bound_event_findings(
        self, findings: tuple[ObjectiveBoundFinding, ...]
    ) -> tuple[ObjectiveBoundEventFinding, ...]:
        """Format findings for event JSON, retaining a reportable fallback."""
        formatted: list[ObjectiveBoundEventFinding] = []
        objective_by_id = {
            objective.objective_id: objective
            for objective in self._objective_declaration.objectives
        }
        for finding in findings:
            objective = objective_by_id[finding.objective_id]
            try:
                value_text = objective.format(finding.objective_value)
                if not isinstance(value_text, str) or not value_text:
                    value_text = '[unformatted]'
            except Exception:
                value_text = '[unformatted]'
            try:
                bound_text = objective.format(finding.upper_bound)
                if not isinstance(bound_text, str) or not bound_text:
                    bound_text = '[unformatted]'
            except Exception:
                bound_text = '[unformatted]'
            formatted.append(
                ObjectiveBoundEventFinding(
                    finding.objective_id,
                    value_text,
                    bound_text,
                )
            )
        return tuple(formatted)

    def _commit_submission(
        self,
        state: _SearchSnapshot,
        submission: _Submission,
        coordinator: SearchCommitCoordinator,
        *,
        component_events: tuple[EventDraft, ...],
        feedback_observation: FeedbackObservation | None,
    ) -> tuple[_SearchSnapshot, ComponentFailure | None]:
        del feedback_observation
        drafts = (*component_events, *submission.events)
        start_sequence = len(coordinator.run_log.events)
        failure_sequences = tuple(
            start_sequence + len(component_events) + index
            for index, draft in enumerate(submission.events)
            if draft.event_type == 'component_failure'
        )
        outcome = submission.outcome
        if outcome.failures:
            candidate_event_index = next(
                index
                for index, draft in enumerate(submission.events)
                if draft.event_type == 'candidate_processed'
            )
            candidate_draft = submission.events[candidate_event_index]
            data = dict(candidate_draft.data)
            data['failure_event_sequences'] = failure_sequences
            replacement = replace(candidate_draft, data=data)
            event_values = list(submission.events)
            event_values[candidate_event_index] = replacement
            drafts = (*component_events, *event_values)
        state_to_commit = replace(
            submission.state,
            commit_sequence=state.commit_sequence + 1,
            random_payload=self._capture_random(),
        )
        try:
            coordinator.commit(state_to_commit, tuple(drafts), (outcome,))
        except Exception as error:
            return state, self._failure(
                self._logger,
                'run_logging',
                f'candidate submission commit failed: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )
        return state_to_commit, None

    def _archive_decision(
        self, state: _SearchSnapshot, candidate: SolutionRecord
    ) -> tuple[ArchiveDecision, bool]:
        evaluation = candidate.evaluation_record.application_evaluation
        if evaluation is None or evaluation.objective_values is None:
            raise _TransitionFailure(
                self._failure(
                    self._archive_policy,
                    'policy_decision',
                    'archive candidate has no objective vector',
                    FailureKind.CONTRACT_VIOLATION,
                )
            )
        entry = ArchiveEntry(candidate.solution_id, evaluation.objective_values)
        archive_entries = tuple(
            ArchiveEntry(
                record.solution_id,
                record.evaluation_record.application_evaluation.objective_values,
            )
            for record in state.pareto_solutions()
            if record.evaluation_record.application_evaluation is not None
            and record.evaluation_record.application_evaluation.objective_values
            is not None
        )
        request = ArchiveRequest(entry, archive_entries, self._objective_declaration)
        expected, objective_vector_duplicate = self._exact_archive_decision(request)
        try:
            decision = self._archive_policy.decide(request)
        except Exception as error:
            raise _TransitionFailure(
                self._failure(
                    self._archive_policy,
                    'policy_decision',
                    f'ArchivePolicy.decide raised: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                )
            ) from error
        if not isinstance(decision, ArchiveDecision) or decision != expected:
            raise _TransitionFailure(
                self._failure(
                    self._archive_policy,
                    'policy_decision',
                    'ArchivePolicy returned a decision inconsistent with the unique-vector Pareto rule',
                    FailureKind.CONTRACT_VIOLATION,
                )
            )
        return decision, objective_vector_duplicate

    def _exact_archive_decision(
        self, request: ArchiveRequest
    ) -> tuple[ArchiveDecision, bool]:
        candidate_values = request.candidate.objective_values
        comparisons_by_entry = tuple(
            (
                entry.solution_id,
                self._compare_objective_vectors(
                    entry.objective_values,
                    candidate_values,
                ),
            )
            for entry in request.current_archive
        )
        dominated = any(
            self._dominates_comparisons(comparisons)
            for _, comparisons in comparisons_by_entry
        )
        if dominated:
            return ArchiveDecision(False, ()), False
        objective_vector_duplicate = any(
            all(comparison is ObjectiveComparison.EQUAL for comparison in comparisons)
            for _, comparisons in comparisons_by_entry
        )
        if objective_vector_duplicate:
            return ArchiveDecision(False, ()), True
        remove = tuple(
            solution_id
            for solution_id, comparisons in comparisons_by_entry
            if self._dominates_comparisons(
                tuple(
                    self._reverse_comparison(comparison) for comparison in comparisons
                )
            )
        )
        return ArchiveDecision(True, remove), False

    def _compare_objective_vectors(
        self, left: tuple[object, ...], right: tuple[object, ...]
    ) -> tuple[ObjectiveComparison, ...]:
        comparisons: list[ObjectiveComparison] = []
        for objective, left_value, right_value in zip(
            self._objective_declaration.objectives,
            left,
            right,
            strict=True,
        ):
            try:
                comparison = objective.compare(left_value, right_value)
            except Exception as error:
                raise _TransitionFailure(
                    self._failure(
                        objective,
                        'objective_comparison',
                        f'ObjectiveFunction.compare raised: {error}',
                        FailureKind.CALLBACK_EXCEPTION,
                    )
                ) from error
            if not isinstance(comparison, ObjectiveComparison):
                raise _TransitionFailure(
                    self._failure(
                        objective,
                        'objective_comparison',
                        'ObjectiveFunction.compare returned an invalid result',
                        FailureKind.CONTRACT_VIOLATION,
                    )
                )
            comparisons.append(comparison)
        return tuple(comparisons)

    @staticmethod
    def _dominates_comparisons(
        comparisons: tuple[ObjectiveComparison, ...],
    ) -> bool:
        return all(
            value in (ObjectiveComparison.LESS, ObjectiveComparison.EQUAL)
            for value in comparisons
        ) and any(value is ObjectiveComparison.LESS for value in comparisons)

    @staticmethod
    def _reverse_comparison(
        comparison: ObjectiveComparison,
    ) -> ObjectiveComparison:
        if comparison is ObjectiveComparison.LESS:
            return ObjectiveComparison.GREATER
        if comparison is ObjectiveComparison.GREATER:
            return ObjectiveComparison.LESS
        return ObjectiveComparison.EQUAL

    def _operator_guarantee_failure(
        self,
        record: SolutionRecord,
        ensure_feasible: bool,
        choice_id: ChoiceId | None,
    ) -> ComponentFailure | None:
        app_eval = record.evaluation_record.application_evaluation
        if app_eval is None:
            return None
        violation = not app_eval.representation_valid or (
            ensure_feasible
            and app_eval.representation_valid
            and app_eval.feasible is False
        )
        if not violation:
            return None
        source_id: ComponentId
        if choice_id is None or choice_id.kind.value == 'operator':
            source_id = (
                choice_id.operator_id
                if choice_id is not None and choice_id.operator_id is not None
                else self._operators[0].operator_id
            )
        else:
            composition = cast(CompositionId, choice_id.composition_id)
            source_id = composition.second_operator_id
        return ComponentFailure(
            FailureKind.CONTRACT_VIOLATION,
            source_id,
            'operator_guarantee',
            'Operator returned a representation-invalid or infeasible result contrary to its request',
            solution_id=record.solution_id,
            choice_id=choice_id,
        )

    def _update_excursion(
        self,
        old_state: _SearchSnapshot,
        new_state: _SearchSnapshot,
        outcome: CandidateOutcome,
        feasible: bool,
        m: int,
        base_id: SolutionId | None,
    ) -> tuple[_InfeasibleExcursion | None, EventDraft | None]:
        excursion = old_state.excursion
        if self._config.feasibility_mode != 'relaxed':
            return excursion, None
        if feasible:
            if excursion is None:
                return None, None
            transition = (
                'returned_to_anchor'
                if outcome.solution_id == excursion.anchor_id
                else 'feasible_exit'
            )
            event = self._event(
                'excursion_transition',
                EventPhase.EXCURSION,
                {
                    'transition': transition,
                    'anchor_solution_id': excursion.anchor_id,
                    'current_solution_id': cast(SolutionId, outcome.solution_id),
                    'step_count': excursion.step_count,
                    'step_limit': cast(int, self._config.max_infeasible_steps),
                },
                solution_id=outcome.solution_id,
                choice_id=outcome.choice_id,
            )
            return None, event
        if m == 0 or outcome.solution_id is None:
            return excursion, None
        record = new_state.registry.get(outcome.solution_id)
        event = self._event(
            'excursion_transition',
            EventPhase.EXCURSION,
            {
                'transition': 'advanced' if excursion is not None else 'started',
                'anchor_solution_id': excursion.anchor_id
                if excursion
                else cast(SolutionId, base_id),
                'current_solution_id': outcome.solution_id,
                'step_count': (excursion.step_count + 1) if excursion else 1,
                'step_limit': cast(int, self._config.max_infeasible_steps),
            },
            solution_id=outcome.solution_id,
            choice_id=outcome.choice_id,
        )
        return (
            _InfeasibleExcursion(
                excursion.anchor_id if excursion else cast(SolutionId, base_id),
                outcome.solution_id,
                record.solution,
                excursion.step_count + 1 if excursion else 1,
            ),
            event,
        )

    def _select_base(
        self,
        state: _SearchSnapshot,
        previous_context: tuple[SolutionId, bool] | None,
        coordinator: SearchCommitCoordinator,
        counters: InvocationCounters,
        disabled_choices: set[ChoiceId],
        disabled_operators: set[OperatorId],
    ) -> tuple[SolutionRecord, str, int]:
        if state.excursion is not None:
            excursion = state.excursion
            record = state.registry.get(excursion.current_id)
            # Objective-invalid excursion values are kept outside the active pool.
            return record, 'active_excursion', 1
        eligible = tuple(
            record
            for record in (
                state.registry.get(solution_id)
                for solution_id in sorted(state.archive_ids, key=identifier_text)
            )
            if self._choices(
                state,
                record.solution_id,
                False,
                disabled_choices,
                disabled_operators,
            )
        )
        if not eligible:
            raise _NoEligibleBase
        previous_id = previous_context[0] if previous_context else None
        request = BaseSelectionRequest(
            eligible,
            {
                record.solution_id: state.attempt_counts.get(record.solution_id, 0)
                for record in eligible
            },
            previous_id,
            previous_context[1] if previous_context is not None else None,
            cast(RandomSource, self._checked_random),
        )
        try:
            decision = self._base_policy.select(request)
        except _RandomFailure:
            raise
        except Exception as error:
            raise _TransitionFailure(
                self._failure(
                    self._base_policy,
                    'policy_decision',
                    f'BaseSelectionPolicy.select raised: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                )
            ) from error
        if not isinstance(
            decision, BaseSelectionDecision
        ) or decision.solution_id not in {record.solution_id for record in eligible}:
            raise _TransitionFailure(
                self._failure(
                    self._base_policy,
                    'policy_decision',
                    'BaseSelectionPolicy returned an ineligible SolutionId',
                    FailureKind.CONTRACT_VIOLATION,
                )
            )
        base = next(
            record for record in eligible if record.solution_id == decision.solution_id
        )
        return base, 'policy', len(eligible)

    def _choices(
        self,
        state: _SearchSnapshot,
        base_id: SolutionId,
        restoration_required: bool,
        disabled_choices: set[ChoiceId],
        disabled_operators: set[OperatorId],
    ) -> tuple[_Choice, ...]:
        cap = self._config.max_neighborhood_size
        choices: list[_Choice] = []
        available = tuple(
            spec
            for spec in self._operator_specs(state)
            if not spec.dependencies() & disabled_operators
        )
        for spec in available:
            max_size = min(
                spec.max_neighborhood_size,
                cap if cap is not None else spec.max_neighborhood_size,
            )
            choice_id = ChoiceId.for_operator(spec.operator_id)
            current_k = state.choice_size_cursors.get((base_id, choice_id), 1)
            if current_k > max_size:
                continue
            descriptor = ChoiceDescriptor(
                choice_id,
                (spec.operator_id,),
                current_k,
                max_size,
                spec.feasibility_restoration,
                frozenset(),
                spec.modification_count_definition,
            )
            choices.append(_Choice(descriptor, spec))
        for first in available:
            for second in available:
                if (
                    first.operator_id,
                    second.operator_id,
                ) in self._pre_registered_pairs:
                    continue
                groups = first.group_ids & second.group_ids
                choice_id = ChoiceId.for_composition(
                    CompositionId(first.operator_id, second.operator_id)
                )
                max_size = min(
                    first.max_neighborhood_size,
                    second.max_neighborhood_size,
                )
                if cap is not None:
                    max_size = min(max_size, cap)
                current_k = state.choice_size_cursors.get((base_id, choice_id), 1)
                if current_k > max_size:
                    continue
                descriptor = ChoiceDescriptor(
                    choice_id,
                    (first.operator_id, second.operator_id),
                    current_k,
                    max_size,
                    second.feasibility_restoration,
                    groups,
                    'min(m_f, m_g); component scales: '
                    f'{first.modification_count_definition}; '
                    f'{second.modification_count_definition}',
                )
                choices.append(_Choice(descriptor, first, second))
        choices = [
            choice
            for choice in choices
            if choice.descriptor.choice_id not in disabled_choices
            and not choice.first.dependencies() & disabled_operators
            and (
                choice.second is None
                or not choice.second.dependencies() & disabled_operators
            )
            and (not restoration_required or choice.descriptor.feasibility_restoration)
        ]
        return tuple(choices)

    @staticmethod
    def _composition_parts(
        choice: _Choice,
    ) -> tuple[_OperatorSpec, _OperatorSpec, str] | None:
        """Return immediate component specs and the selected composition kind."""
        if choice.second is not None:
            return choice.first, choice.second, 'dynamic'
        if choice.first.first is None or choice.first.second is None:
            return None
        source = (
            'pre_registered'
            if choice.first.catalog_kind == 'pre_registered_composite'
            else 'promoted'
        )
        return choice.first.first, choice.first.second, source

    @staticmethod
    def _updated_choice_cursors(
        state: _SearchSnapshot,
        base_id: SolutionId,
        choice_id: ChoiceId,
        k: int,
        *,
        admitted: bool,
    ) -> Mapping[tuple[SolutionId, ChoiceId], int]:
        """Advance or reset one choice's cursor for a committed attempt."""
        cursors = dict(state.choice_size_cursors)
        cursors[(base_id, choice_id)] = 1 if admitted else k + 1
        return MappingProxyType(cursors)

    def _ensure_choice_cursors(self, state: _SearchSnapshot) -> _SearchSnapshot:
        """Create size-one cursors for choices added to the dynamic catalog."""
        specs = self._operator_specs(state)
        choice_ids = {ChoiceId.for_operator(spec.operator_id) for spec in specs}
        choice_ids.update(
            ChoiceId.for_composition(
                CompositionId(first.operator_id, second.operator_id)
            )
            for first in specs
            for second in specs
            if (first.operator_id, second.operator_id) not in self._pre_registered_pairs
        )
        bases = set(state.archive_ids)
        if state.excursion is not None:
            bases.add(state.excursion.current_id)
        cursors = {
            (base_id, choice_id): current_k
            for (base_id, choice_id), current_k in state.choice_size_cursors.items()
            if base_id in bases
        }
        for base_id in bases:
            for choice_id in choice_ids:
                cursors.setdefault((base_id, choice_id), 1)
        if cursors == dict(state.choice_size_cursors):
            return state
        return replace(state, choice_size_cursors=MappingProxyType(cursors))

    def _operator_specs(self, state: _SearchSnapshot) -> tuple[_OperatorSpec, ...]:
        """Build executable Operator entries from registrations and promotions."""
        specs: dict[OperatorId, _OperatorSpec] = {}
        for operator_registration in self._operators:
            group_ids = frozenset(
                group.group_id
                for group in self._operator_groups
                if operator_registration.operator_id in group.operator_ids
            )
            specs[operator_registration.operator_id] = _OperatorSpec(
                operator_registration.operator_id,
                operator_registration.max_neighborhood_size,
                operator_registration.feasibility_restoration,
                operator_registration.modification_count_definition,
                group_ids,
                operator_registration.configuration_id,
                'ordinary',
                registration=operator_registration,
            )
        for composition_registration in self._pre_registered_compositions:
            first = specs.get(composition_registration.first_operator_id)
            second = specs.get(composition_registration.second_operator_id)
            if first is None or second is None:
                raise ValueError('pre-registered composite refers to unknown Operators')
            inherited_group_ids = first.group_ids & second.group_ids
            declared_group_ids = frozenset(
                group.group_id
                for group in self._operator_groups
                if composition_registration.operator_id in group.operator_ids
            )
            composite_group_ids = inherited_group_ids | declared_group_ids
            specs[composition_registration.operator_id] = _OperatorSpec(
                composition_registration.operator_id,
                min(first.max_neighborhood_size, second.max_neighborhood_size),
                second.feasibility_restoration,
                'min(m_first, m_second); component scales: '
                f'{first.modification_count_definition}; '
                f'{second.modification_count_definition}',
                composite_group_ids,
                composition_registration.configuration_id,
                'pre_registered_composite',
                (first.operator_id, second.operator_id),
                first=first,
                second=second,
            )
        for promoted in state.promoted_operators:
            report = promoted.report
            first = specs.get(report.component_operator_ids[0])
            second = specs.get(report.component_operator_ids[1])
            if first is None or second is None:
                raise ValueError('promoted Operator refers to an unknown component')
            configuration_id = 'multiobjective-vns:promoted-operator-config:' + (
                json.dumps(
                    [
                        report.source_composition_id.to_log_text(),
                        identifier_text(first.configuration_id),
                        identifier_text(second.configuration_id),
                        report.max_neighborhood_size,
                    ],
                    ensure_ascii=True,
                    separators=(',', ':'),
                )
            )
            specs[report.operator_id] = _OperatorSpec(
                report.operator_id,
                report.max_neighborhood_size,
                report.feasibility_restoration,
                promoted.modification_count_definition,
                report.inherited_group_ids,
                configuration_id,
                'promoted_composite',
                report.component_operator_ids,
                first=first,
                second=second,
            )
        return tuple(specs.values())

    def _sample_choice(
        self,
        choices: tuple[_Choice, ...],
        probabilities: Mapping[ChoiceId, FiniteReal],
    ) -> _Choice:
        expected = {choice.descriptor.choice_id for choice in choices}
        if not isinstance(probabilities, Mapping) or set(probabilities) != expected:
            raise ValueError(
                'FeedbackPolicy distribution must cover every choice exactly once'
            )
        normalized: dict[ChoiceId, float] = {}
        for choice_id, probability in probabilities.items():
            value = finite_real(probability, name='choice probability')
            if value < 0 or value > 1:
                raise ValueError('choice probabilities must be in [0, 1]')
            normalized[choice_id] = value
        total = sum(normalized.values())
        if not math.isfinite(total) or abs(total - 1.0) > 1e-12:
            raise ValueError('choice probabilities must sum to one')
        draw = self._checked_random.random()
        cumulative = 0.0
        for choice in choices:
            cumulative += normalized[choice.descriptor.choice_id] / total
            if draw < cumulative:
                return choice
        return choices[-1]

    def _call_operator(
        self,
        registration: OperatorRegistration,
        base: object,
        k: int,
        ensure_feasible: bool,
        previous_payload: object | None,
        base_evaluation: EvaluationRecord | None,
        max_candidates: int,
    ) -> tuple[OperatorResult, object | None, bool]:
        state = registration.initial_state
        stateful = registration.initial_state is not None
        if stateful:
            try:
                state = cast(StatefulOperator, registration.operator).restore_state(
                    previous_payload
                )
            except Exception as error:
                raise _TransitionFailure(
                    self._failure(
                        registration.operator_id,
                        'operator_state_restore',
                        f'Operator.restore_state failed: {error}',
                        FailureKind.CALLBACK_EXCEPTION,
                    )
                ) from error
        try:
            result = registration.operator.call(
                CandidateContext(
                    base=base,
                    k=k,
                    ensure_feasible=ensure_feasible,
                    operator_state=state,
                    random_source=cast(RandomSource, self._checked_random),
                    base_evaluation=base_evaluation,
                    max_candidates=max_candidates,
                )
            )
        except _RandomFailure as error:
            payload = self._capture_operator_call_state(registration, state)
            raise _OperatorCallFailure(
                error.failure,
                registration.operator_id,
                payload,
                stateful,
                random_failure=True,
            ) from error
        except Exception as error:
            payload = self._capture_operator_call_state(registration, state)
            raise _OperatorCallFailure(
                ComponentFailure(
                    FailureKind.CALLBACK_EXCEPTION,
                    registration.operator_id,
                    'operator_call',
                    f'Operator.call raised: {error}',
                ),
                registration.operator_id,
                payload,
                stateful,
            ) from error
        payload = self._capture_operator_call_state(registration, state)
        if not isinstance(result, OperatorResult):
            raise _OperatorCallFailure(
                ComponentFailure(
                    FailureKind.CONTRACT_VIOLATION,
                    registration.operator_id,
                    'operator_call',
                    'Operator.call must return an OperatorResult',
                ),
                registration.operator_id,
                payload,
                stateful,
            )
        if len(result.candidates) > max_candidates:
            raise _OperatorCallFailure(
                ComponentFailure(
                    FailureKind.CONTRACT_VIOLATION,
                    registration.operator_id,
                    'operator_call',
                    'OperatorResult exceeds CandidateContext.max_candidates',
                ),
                registration.operator_id,
                payload,
                stateful,
            )
        if any(proposal.m > k for proposal in result.candidates):
            raise _OperatorCallFailure(
                ComponentFailure(
                    FailureKind.CONTRACT_VIOLATION,
                    registration.operator_id,
                    'operator_call',
                    'a CandidateProposal.m cannot exceed requested k',
                ),
                registration.operator_id,
                payload,
                stateful,
            )
        return result, payload, stateful

    def _call_operator_spec(
        self,
        spec: _OperatorSpec,
        base: object,
        k: int,
        ensure_feasible: bool,
        state_payloads: dict[OperatorId, object],
        base_evaluation: EvaluationRecord | None,
        *,
        max_candidates: int,
        choice_id: ChoiceId,
        base_id: SolutionId,
        component_position: CompositionPosition | None,
        input_candidate_index: int | None,
        component_events: list[EventDraft],
        component_call_index: list[int],
    ) -> OperatorResult:
        """Run one registered Operator or a generated composition expression."""
        if spec.registration is not None:
            result, payload, stateful = self._call_operator(
                spec.registration,
                base,
                k,
                ensure_feasible,
                state_payloads.get(spec.operator_id),
                base_evaluation,
                max_candidates,
            )
            if stateful:
                state_payloads[spec.operator_id] = payload
            component_call_index[0] += 1
            component_events.append(
                self._operator_result_event(
                    choice_id,
                    base_id,
                    spec,
                    result,
                    k,
                    component_position,
                    component_call_index[0],
                    input_candidate_index,
                )
            )
            return result
        if spec.first is None or spec.second is None:
            raise ValueError('promoted Operator has an incomplete expression')
        result, _ = self._call_operator_pair(
            spec.first,
            spec.second,
            base,
            k,
            ensure_feasible,
            state_payloads,
            base_evaluation,
            max_candidates=max_candidates,
            choice_id=choice_id,
            base_id=base_id,
            component_events=component_events,
            component_call_index=component_call_index,
            inherited_position=component_position,
            inherited_input_candidate_index=input_candidate_index,
        )
        return result

    def _call_operator_pair(
        self,
        first: _OperatorSpec,
        second: _OperatorSpec,
        base: object,
        k: int,
        ensure_feasible: bool,
        state_payloads: dict[OperatorId, object],
        base_evaluation: EvaluationRecord | None,
        *,
        max_candidates: int,
        choice_id: ChoiceId,
        base_id: SolutionId,
        component_events: list[EventDraft],
        component_call_index: list[int],
        inherited_position: CompositionPosition | None = None,
        inherited_input_candidate_index: int | None = None,
    ) -> tuple[OperatorResult, dict[str, int]]:
        """Compose ordered candidate lists while respecting the final limit."""
        first_position = inherited_position or CompositionPosition.FIRST
        second_position = inherited_position or CompositionPosition.SECOND
        first_result = self._call_operator_spec(
            first,
            base,
            k,
            False,
            state_payloads,
            base_evaluation,
            max_candidates=max_candidates,
            choice_id=choice_id,
            base_id=base_id,
            component_position=first_position,
            input_candidate_index=inherited_input_candidate_index,
            component_events=component_events,
            component_call_index=component_call_index,
        )
        statistics = {
            'first_candidate_count': len(first_result.candidates),
            'second_call_count': 0,
        }
        final_proposals: list[CandidateProposal] = []
        for input_index, intermediate in enumerate(first_result.candidates, start=1):
            remaining = max_candidates - len(final_proposals)
            if remaining <= 0:
                break
            statistics['second_call_count'] += 1
            second_result = self._call_operator_spec(
                second,
                intermediate.candidate.solution,
                k,
                ensure_feasible,
                state_payloads,
                None,
                max_candidates=remaining,
                choice_id=choice_id,
                base_id=base_id,
                component_position=second_position,
                input_candidate_index=input_index,
                component_events=component_events,
                component_call_index=component_call_index,
            )
            for final in second_result.candidates:
                if len(final_proposals) >= max_candidates:
                    break
                final_proposals.append(
                    CandidateProposal(
                        final.candidate,
                        min(intermediate.m, final.m),
                    )
                )
        return OperatorResult(candidates=tuple(final_proposals)), statistics

    def _capture_operator_call_state(
        self, registration: OperatorRegistration, state: object | None
    ) -> object | None:
        if registration.initial_state is None:
            return None
        try:
            return cast(StatefulOperator, registration.operator).capture_state(state)
        except Exception as error:
            raise _TransitionFailure(
                self._failure(
                    registration.operator_id,
                    'operator_state_capture',
                    f'Operator.capture_state failed: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                )
            ) from error

    def _operator_result_event(
        self,
        choice_id: ChoiceId,
        base_id: SolutionId,
        registration: _OperatorSpec,
        result: OperatorResult,
        k: int,
        position: CompositionPosition | None,
        component_call_index: int,
        input_candidate_index: int | None,
    ) -> EventDraft:
        return self._event(
            'operator_result',
            EventPhase.OPERATOR_CALL,
            {
                'k': k,
                'candidate_count': len(result.candidates),
                'modification_counts': tuple(
                    proposal.m for proposal in result.candidates
                ),
                'component_position': position,
                'component_call_index': component_call_index,
                'input_candidate_index': input_candidate_index,
            },
            source_id=registration.operator_id,
            choice_id=choice_id,
            solution_id=base_id,
        )

    def _budget_decision(
        self,
        counters: InvocationCounters,
        started: float,
    ) -> tuple[BudgetRequest, BudgetDecision] | ComponentFailure:
        request = BudgetRequest(
            counters,
            self._config.budget_limits,
            self._elapsed(started),
        )
        try:
            decision = self._budget_policy.decide(request)
        except Exception as error:
            return self._failure(
                self._budget_policy,
                'policy_decision',
                f'BudgetPolicy.decide raised: {error}',
                FailureKind.CALLBACK_EXCEPTION,
            )
        if not isinstance(decision, BudgetDecision):
            return self._failure(
                self._budget_policy,
                'policy_decision',
                'BudgetPolicy.decide returned an invalid record',
                FailureKind.CONTRACT_VIOLATION,
            )
        return request, decision

    def _prepare_state(
        self, checkpoint: Checkpoint | object | None
    ) -> tuple[_SearchSnapshot, object | None, int | None]:
        if checkpoint is None:
            self._restore_baselines()
            registry = SolutionRegistry(
                self._solution_identity,
                exhaustive_identity_validation=self._config.exhaustive_identity_validation,
            )
            initial_state = _SearchSnapshot(
                registry=registry,
                archive_ids=frozenset(),
                active_ids=frozenset(),
                attempt_counts=MappingProxyType({}),
                tracked_choice_ids=frozenset(),
                choice_size_cursors=MappingProxyType({}),
                operator_payloads=MappingProxyType(dict(self._operator_baselines)),
                extension_payloads=MappingProxyType(dict(self._extension_baselines)),
                feedback_payload=self._feedback_baseline,
                random_payload=self._random_baseline,
                manifest=self._manifest,
                configuration_signature=self._configuration_signature(),
                problem_instance_text=identifier_text(self._problem_instance_id),
                objective_texts=tuple(
                    identifier_text(obj.objective_id)
                    for obj in self._objective_declaration.objectives
                ),
                objective_bounds=cast(
                    tuple[ObjectiveValue | None, ...],
                    self._objective_declaration.upper_bounds,
                ),
            )
            initial_state = self._load_promoted_catalog(initial_state)
            return (
                self._ensure_choice_cursors(initial_state),
                None,
                None,
            )
        reference: object | None = None
        checkpoint_value: Checkpoint
        if isinstance(checkpoint, Checkpoint):
            checkpoint_value = checkpoint
        else:
            if self._checkpoint_store is None:
                raise ValueError('CheckpointReference requires a CheckpointStore')
            reference = checkpoint
            try:
                checkpoint_value = self._checkpoint_store.load(checkpoint)
            except Exception as error:
                raise CheckpointRestoreError(
                    checkpoint,
                    self._failure(
                        self._checkpoint_store,
                        'checkpoint_restore',
                        f'CheckpointStore.load failed: {error}',
                        FailureKind.INFRASTRUCTURE_FAILURE,
                    ),
                ) from error
        saved = checkpoint_value.search_state
        if not isinstance(saved, _SearchSnapshot):
            raise ResumeCompatibilityError(
                'checkpoint SearchState was created elsewhere'
            )
        if (
            saved.manifest != self._manifest
            or saved.configuration_signature != self._configuration_signature()
            or saved.problem_instance_text != identifier_text(self._problem_instance_id)
            or saved.objective_texts
            != tuple(
                identifier_text(obj.objective_id)
                for obj in self._objective_declaration.objectives
            )
        ):
            raise ResumeCompatibilityError('checkpoint compatibility manifest differs')
        if not self._objective_bounds_match(saved.objective_bounds):
            raise ResumeCompatibilityError('checkpoint objective bounds differ')
        records = saved.registry.records
        if saved.codec_payloads is not None:
            restored_records = []
            for record in records:
                payload = saved.codec_payloads[record.solution_id]
                if not isinstance(payload, bytes):
                    raise CheckpointRestoreError(
                        checkpoint,
                        self._failure(
                            self._solution_codec,
                            'checkpoint_restore',
                            'saved SolutionCodec payload must be bytes',
                            FailureKind.CONTRACT_VIOLATION,
                            solution_id=record.solution_id,
                        ),
                    )
                try:
                    solution = self._solution_codec.restore(payload)
                except Exception as error:
                    kind = (
                        FailureKind.CONTRACT_VIOLATION
                        if isinstance(error, (TypeError, ValueError))
                        else FailureKind.CALLBACK_EXCEPTION
                    )
                    raise CheckpointRestoreError(
                        checkpoint,
                        self._failure(
                            self._solution_codec,
                            'checkpoint_restore',
                            f'SolutionCodec.restore failed: {error}',
                            kind,
                            solution_id=record.solution_id,
                        ),
                    ) from error
                restored_records.append(replace(record, solution=solution))
            records = tuple(restored_records)
        try:
            registry = SolutionRegistry(
                self._solution_identity,
                records=records,
                exhaustive_identity_validation=self._config.exhaustive_identity_validation,
            )
            self._restore_snapshot_components(saved)
        except Exception as error:
            if isinstance(error, ComponentSetupError):
                raise
            raise ComponentSetupError(
                self._failure(
                    self._solution_identity,
                    'identity_index_rebuild',
                    f'continuation state could not be restored: {error}',
                    FailureKind.CONTRACT_VIOLATION,
                )
            ) from error
        restored = replace(saved, registry=registry, codec_payloads=None)
        return restored, reference, saved.commit_sequence

    def _save_checkpoint_if_needed(
        self,
        state: _SearchSnapshot,
        progress: _CheckpointProgress,
        coordinator: SearchCommitCoordinator,
        *,
        force_final: bool,
    ) -> ComponentFailure | None:
        if self._checkpoint_store is None:
            return None
        interval_due = (
            progress.interval is not None
            and state.commit_sequence - progress.saved_sequence >= progress.interval
        )
        final_due = force_final and (
            progress.reference is None
            or state.commit_sequence > progress.saved_sequence
        )
        if not interval_due and not final_due:
            return None
        try:
            payloads = {}
            for record in state.registry.records:
                try:
                    payloads[record.solution_id] = self._solution_codec.capture(
                        record.solution
                    )
                except Exception as error:
                    return self._failure(
                        self._solution_codec,
                        'checkpoint_save',
                        f'SolutionCodec.capture failed: {error}',
                        FailureKind.CALLBACK_EXCEPTION,
                        solution_id=record.solution_id,
                    )
            checkpoint = Checkpoint(
                replace(state, codec_payloads=MappingProxyType(payloads))
            )
        except Exception as error:
            return self._failure(
                self._solution_codec,
                'checkpoint_save',
                f'checkpoint preparation failed: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )
        try:
            new_reference = self._checkpoint_store.save(checkpoint)
        except Exception as error:
            return self._failure(
                self._checkpoint_store,
                'checkpoint_save',
                f'CheckpointStore.save failed: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )
        progress.reference = new_reference
        progress.saved_sequence = state.commit_sequence
        try:
            self._append_invocation(
                coordinator,
                self._event(
                    'checkpoint_saved',
                    EventPhase.CHECKPOINT,
                    {'snapshot_commit_sequence': state.commit_sequence},
                ),
            )
        except Exception as error:
            return self._failure(
                self._logger,
                'run_logging',
                f'checkpoint_saved event append failed: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )
        return None

    def _save_pareto_snapshot(
        self,
        state: _SearchSnapshot,
        progress: _ParetoSnapshotProgress,
    ) -> ComponentFailure | None:
        """Capture and atomically publish the current archive."""
        records = state.pareto_solutions()
        if not records:
            return None
        payloads: list[bytes] = []
        for record in records:
            try:
                payload = self._solution_codec.capture(record.solution)
            except Exception as error:
                return self._failure(
                    self._solution_codec,
                    'pareto_snapshot_save',
                    f'SolutionCodec.capture failed: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                    solution_id=record.solution_id,
                )
            if type(payload) is not bytes:
                return self._failure(
                    self._solution_codec,
                    'pareto_snapshot_save',
                    'SolutionCodec.capture must return bytes',
                    FailureKind.CONTRACT_VIOLATION,
                    solution_id=record.solution_id,
                )
            payloads.append(payload)

        try:
            snapshot = ParetoSnapshotData(
                problem_instance_id=identifier_text(self._problem_instance_id),
                objective_ids=tuple(
                    identifier_text(objective.objective_id)
                    for objective in self._objective_declaration.objectives
                ),
                objective_configuration_ids=tuple(
                    identifier_text(objective.configuration_id)
                    for objective in self._objective_declaration.objectives
                ),
                solution_codec_component_id=identifier_text(
                    self._solution_codec.component_id
                ),
                solution_codec_configuration_id=identifier_text(
                    self._solution_codec.configuration_id
                ),
                solution_payloads=tuple(payloads),
            )
            write_pareto_snapshot(progress.destination, snapshot)
        except Exception as error:
            return self._failure(
                'multiobjective-vns:pareto-snapshot-store',
                'pareto_snapshot_save',
                f'Pareto snapshot publication failed: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )
        progress.latest_successful_path = progress.destination
        return None

    def _capture_operator_baselines(self) -> dict[OperatorId, object]:
        payloads: dict[OperatorId, object] = {}
        for registration in self._operators:
            if registration.initial_state is None:
                continue
            try:
                payloads[registration.operator_id] = cast(
                    StatefulOperator, registration.operator
                ).capture_state(registration.initial_state)
            except Exception as error:
                raise ComponentSetupError(
                    self._failure(
                        registration.operator_id,
                        'operator_state_capture',
                        f'Operator baseline capture failed: {error}',
                        FailureKind.CALLBACK_EXCEPTION,
                    )
                ) from error
        return payloads

    def _capture_feedback_baseline(self) -> object:
        try:
            return self._feedback_policy.capture_state()
        except Exception as error:
            raise ComponentSetupError(
                self._failure(
                    self._feedback_policy,
                    'policy_state_capture',
                    f'FeedbackPolicy baseline capture failed: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                )
            ) from error

    def _capture_random_baseline(self) -> object:
        try:
            return self._random_source.capture_state()
        except Exception as error:
            raise ComponentSetupError(
                self._failure(
                    self._random_source,
                    'random_state_capture',
                    f'RandomSource baseline capture failed: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                )
            ) from error

    def _capture_extension_baselines(self) -> dict[ComponentId, object]:
        payloads: dict[ComponentId, object] = {}
        for registration in self._extensions:
            if registration.stateful:
                try:
                    payloads[registration.component_id] = cast(
                        StatefulSearchExtension, registration.extension
                    ).capture_state()
                except Exception as error:
                    raise ComponentSetupError(
                        self._failure(
                            registration.component_id,
                            f'extension.{registration.namespace}.state_capture',
                            f'SearchExtension baseline capture failed: {error}',
                            FailureKind.CALLBACK_EXCEPTION,
                        )
                    ) from error
        return payloads

    def _capture_feedback(self) -> object:
        return self._feedback_policy.capture_state()

    def _capture_random(self) -> object:
        return self._random_source.capture_state()

    def _restore_baselines(self) -> None:
        try:
            for registration in self._operators:
                if registration.initial_state is not None:
                    cast(StatefulOperator, registration.operator).restore_state(
                        self._operator_baselines[registration.operator_id]
                    )
            self._feedback_policy.restore_state(self._feedback_baseline)
            self._random_source.restore_state(self._random_baseline)
            for extension_registration in self._extensions:
                if extension_registration.stateful:
                    cast(
                        StatefulSearchExtension,
                        extension_registration.extension,
                    ).restore_state(
                        self._extension_baselines[extension_registration.component_id]
                    )
        except Exception as error:
            component = self._feedback_policy
            raise ComponentSetupError(
                self._failure(
                    component,
                    'policy_state_restore',
                    f'component baseline restoration failed: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                )
            ) from error

    def _restore_snapshot_components(self, state: _SearchSnapshot) -> None:
        for registration in self._operators:
            if registration.initial_state is not None:
                cast(StatefulOperator, registration.operator).restore_state(
                    state.operator_payloads[registration.operator_id]
                )
        self._feedback_policy.restore_state(state.feedback_payload)
        self._random_source.restore_state(state.random_payload)
        for extension_registration in self._extensions:
            if extension_registration.stateful:
                cast(
                    StatefulSearchExtension, extension_registration.extension
                ).restore_state(
                    state.extension_payloads[extension_registration.component_id]
                )

    def _restore_feedback(self, payload: object) -> None:
        try:
            self._feedback_policy.restore_state(payload)
        except Exception:
            return

    def _restore_random(self, payload: object) -> None:
        try:
            self._random_source.restore_state(payload)
        except Exception:
            return

    def _restore_operator_payloads(
        self,
        attempted: Mapping[OperatorId, object],
        original: Mapping[OperatorId, object],
    ) -> None:
        del attempted
        for registration in self._operators:
            if (
                registration.operator_id in original
                and registration.initial_state is not None
            ):
                try:
                    cast(StatefulOperator, registration.operator).restore_state(
                        original[registration.operator_id]
                    )
                except Exception:
                    continue

    def _validate_registrations(self) -> None:
        if len(self._operator_by_id) != len(self._operators):
            raise ComponentRegistrationError('Operator IDs must be unique')
        if len(self._pre_registered_by_id) != len(self._pre_registered_compositions):
            raise ComponentRegistrationError(
                'pre-registered composite Operator IDs must be unique'
            )
        all_operator_ids = set(self._operator_by_id)
        operator_texts = {
            identifier_text(operator_id) for operator_id in self._operator_by_id
        }
        composition_pairs: set[tuple[OperatorId, OperatorId]] = set()
        for registration in self._pre_registered_compositions:
            operator_id_text = identifier_text(registration.operator_id)
            if (
                registration.operator_id in all_operator_ids
                or operator_id_text in operator_texts
            ):
                raise ComponentRegistrationError(
                    'ordinary and pre-registered composite Operator IDs must be unique'
                )
            all_operator_ids.add(registration.operator_id)
            operator_texts.add(operator_id_text)
            if (
                registration.first_operator_id not in self._operator_by_id
                or registration.second_operator_id not in self._operator_by_id
            ):
                raise ComponentRegistrationError(
                    'pre-registered composite references an unknown Operator'
                )
            pair = (
                registration.first_operator_id,
                registration.second_operator_id,
            )
            if pair in composition_pairs:
                raise ComponentRegistrationError(
                    'pre-registered composition pairs must be unique'
                )
            composition_pairs.add(pair)
        group_ids: set[object] = set()
        group_id_texts: set[str] = set()
        for group in self._operator_groups:
            group_id_text = identifier_text(group.group_id)
            if group.group_id in group_ids or group_id_text in group_id_texts:
                raise ComponentRegistrationError('OperatorGroup IDs must be unique')
            group_ids.add(group.group_id)
            group_id_texts.add(group_id_text)
            if not group.operator_ids <= all_operator_ids:
                raise ComponentRegistrationError(
                    'OperatorGroup references unknown Operators'
                )
        has_restoration = any(
            registration.feasibility_restoration for registration in self._operators
        ) or any(
            self._operator_by_id[
                registration.second_operator_id
            ].feasibility_restoration
            for registration in self._pre_registered_compositions
        )
        if self._config.feasibility_mode == 'relaxed' and not has_restoration:
            raise ValueError('relaxed mode requires a feasibility-restoration Operator')
        components = (
            self._solution_codec,
            self._solution_identity,
            self._application_evaluator,
            self._archive_policy,
            self._exploration_policy,
            self._base_policy,
            self._budget_policy,
            self._feedback_policy,
            self._executor,
            self._random_source,
            self._logger,
            *self._observers,
            *self._objective_declaration.objectives,
            *((self._checkpoint_store,) if self._checkpoint_store is not None else ()),
            *(
                (self._solution_formatter,)
                if self._solution_formatter is not None
                else ()
            ),
        )
        texts: dict[str, object] = {}
        reserved = {
            'multiobjective-vns:archive-policy',
            'multiobjective-vns:exploration-policy',
            'multiobjective-vns:base-selection-policy',
            'multiobjective-vns:budget-policy',
            'multiobjective-vns:feedback-policy',
            'multiobjective-vns:executor',
            'multiobjective-vns:random-source',
            'multiobjective-vns:run-logger',
        }
        for operator_registration in self._operators:
            text = identifier_text(operator_registration.operator_id)
            if text in reserved:
                raise ComponentRegistrationError(
                    'caller registration uses a reserved ID'
                )
            if text in texts:
                raise ComponentRegistrationError('component IDs must be unique')
            texts[text] = operator_registration.operator
        for composition_registration in self._pre_registered_compositions:
            text = identifier_text(composition_registration.operator_id)
            if text in reserved:
                raise ComponentRegistrationError(
                    'caller registration uses a reserved ID'
                )
            if text in texts:
                raise ComponentRegistrationError('component IDs must be unique')
            texts[text] = composition_registration
        for component in components:
            try:
                component_id = component.component_id
                text = identifier_text(component_id)
                config_id = getattr(component, 'configuration_id', None)
                if config_id is not None:
                    identifier_text(config_id)
            except Exception as error:
                raise ComponentRegistrationError(
                    f'component registration is invalid: {error}'
                ) from error
            if text in texts:
                raise ComponentRegistrationError('component IDs must be unique')
            texts[text] = component
        namespaces: set[str] = set()
        for extension_registration in self._extensions:
            text = identifier_text(extension_registration.component_id)
            if text in texts:
                raise ComponentRegistrationError('component IDs must be unique')
            texts[text] = extension_registration.extension
            if extension_registration.namespace in namespaces:
                raise ComponentRegistrationError('extension namespaces must be unique')
            namespaces.add(extension_registration.namespace)

    def _build_manifest(self) -> tuple[tuple[str, str, str | None], ...]:
        components: list[tuple[str, str, str | None]] = []
        for role, component in (
            ('solution_codec', self._solution_codec),
            ('solution_identity', self._solution_identity),
            ('application_evaluator', self._application_evaluator),
            ('archive_policy', self._archive_policy),
            ('exploration_policy', self._exploration_policy),
            ('base_selection_policy', self._base_policy),
            ('budget_policy', self._budget_policy),
            ('feedback_policy', self._feedback_policy),
            ('executor', self._executor),
            ('random_source', self._random_source),
            ('run_logger', self._logger),
        ):
            configuration_id = getattr(component, 'configuration_id', None)
            components.append(
                (
                    role,
                    identifier_text(component.component_id),
                    identifier_text(configuration_id)
                    if configuration_id is not None
                    else None,
                )
            )
        for observer in self._observers:
            components.append(
                (
                    'event_observer',
                    identifier_text(observer.component_id),
                    None,
                )
            )
        if self._checkpoint_store is not None:
            configuration_id = getattr(self._checkpoint_store, 'configuration_id', None)
            components.append(
                (
                    'checkpoint_store',
                    identifier_text(self._checkpoint_store.component_id),
                    identifier_text(cast(ComponentConfigurationId, configuration_id))
                    if configuration_id is not None
                    else None,
                )
            )
        if self._solution_formatter is not None:
            configuration_id = getattr(
                self._solution_formatter, 'configuration_id', None
            )
            components.append(
                (
                    'solution_formatter',
                    identifier_text(self._solution_formatter.component_id),
                    identifier_text(cast(ComponentConfigurationId, configuration_id))
                    if configuration_id is not None
                    else None,
                )
            )
        for objective in self._objective_declaration.objectives:
            components.append(
                (
                    'objective_function',
                    identifier_text(objective.component_id),
                    identifier_text(objective.configuration_id),
                )
            )
        for operator_registration in self._operators:
            components.append(
                (
                    'operator',
                    identifier_text(operator_registration.operator_id),
                    identifier_text(operator_registration.configuration_id),
                )
            )
        for composition_registration in self._pre_registered_compositions:
            components.append(
                (
                    'operator',
                    identifier_text(composition_registration.operator_id),
                    identifier_text(composition_registration.configuration_id),
                )
            )
            components.append(
                (
                    'operator_composition',
                    identifier_text(composition_registration.operator_id),
                    json.dumps(
                        [
                            identifier_text(composition_registration.first_operator_id),
                            identifier_text(
                                composition_registration.second_operator_id
                            ),
                        ],
                        ensure_ascii=True,
                        separators=(',', ':'),
                    ),
                )
            )
        for extension in self._extensions:
            components.append(
                (
                    'search_extension',
                    identifier_text(extension.component_id),
                    identifier_text(extension.configuration_id),
                )
            )
        return tuple(components)

    def _configuration_signature(self) -> tuple[object, ...]:
        """Return configuration fields that must remain stable on resume."""
        config_signature = tuple(
            getattr(self._config, field.name)
            for field in fields(self._config)
            if field.name != 'budget_limits'
        )
        return (
            *config_signature,
            (
                'promoted_composition_catalog_path',
                (
                    str(self._promoted_composition_catalog_path)
                    if self._promoted_composition_catalog_path is not None
                    else None
                ),
            ),
        )

    def _objective_bounds_match(
        self, saved_bounds: tuple[ObjectiveValue | None, ...]
    ) -> bool:
        """Compare saved bounds with current bounds through objective ordering."""
        current_bounds = cast(
            tuple[ObjectiveValue | None, ...],
            self._objective_declaration.upper_bounds,
        )
        if len(saved_bounds) != len(current_bounds):
            return False
        for objective, saved_bound, current_bound in zip(
            self._objective_declaration.objectives,
            saved_bounds,
            current_bounds,
            strict=True,
        ):
            if saved_bound is None or current_bound is None:
                if saved_bound is not current_bound:
                    return False
                continue
            try:
                comparison = objective.compare(saved_bound, current_bound)
            except Exception as error:
                raise ComponentSetupError(
                    self._failure(
                        objective.component_id,
                        'objective_bound_comparison',
                        f'ObjectiveFunction.compare raised while matching bounds: {error}',
                        FailureKind.CALLBACK_EXCEPTION,
                    )
                ) from error
            if not isinstance(comparison, ObjectiveComparison):
                raise ComponentSetupError(
                    self._failure(
                        objective.component_id,
                        'objective_bound_comparison',
                        'ObjectiveFunction.compare returned an invalid result while matching bounds',
                        FailureKind.CONTRACT_VIOLATION,
                    )
                )
            if comparison is not ObjectiveComparison.EQUAL:
                return False
        return True

    def _promotion_catalog_compatibility(self) -> dict[str, object]:
        """Build the stable application declaration for saved promotions."""
        objectives = [
            {
                'objective_id': identifier_text(item.objective_id),
                'configuration_id': identifier_text(item.configuration_id),
            }
            for item in self._objective_declaration.objectives
        ]
        operators: list[dict[str, object]] = []
        for registration in self._operators:
            operators.append(
                {
                    'catalog_kind': 'ordinary',
                    'operator_id': identifier_text(registration.operator_id),
                    'configuration_id': identifier_text(registration.configuration_id),
                    'component_operator_ids': None,
                    'max_neighborhood_size': registration.max_neighborhood_size,
                    'feasibility_restoration': registration.feasibility_restoration,
                    'modification_count_definition': (
                        registration.modification_count_definition
                    ),
                }
            )
        for composition_registration in self._pre_registered_compositions:
            first = self._operator_by_id[composition_registration.first_operator_id]
            second = self._operator_by_id[composition_registration.second_operator_id]
            maximum = min(
                first.max_neighborhood_size,
                second.max_neighborhood_size,
            )
            if self._config.max_neighborhood_size is not None:
                maximum = min(maximum, self._config.max_neighborhood_size)
            operators.append(
                {
                    'catalog_kind': 'pre_registered_composite',
                    'operator_id': identifier_text(
                        composition_registration.operator_id
                    ),
                    'configuration_id': identifier_text(
                        composition_registration.configuration_id
                    ),
                    'component_operator_ids': [
                        identifier_text(composition_registration.first_operator_id),
                        identifier_text(composition_registration.second_operator_id),
                    ],
                    'max_neighborhood_size': maximum,
                    'feasibility_restoration': second.feasibility_restoration,
                    'modification_count_definition': (
                        'min(m_first, m_second); component scales: '
                        f'{first.modification_count_definition}; '
                        f'{second.modification_count_definition}'
                    ),
                }
            )
        operator_groups = [
            {
                'group_id': identifier_text(group.group_id),
                'operator_ids': sorted(
                    identifier_text(operator_id) for operator_id in group.operator_ids
                ),
            }
            for group in self._operator_groups
        ]
        return {
            'objectives': objectives,
            'operators': operators,
            'operator_groups': operator_groups,
        }

    def _load_promoted_catalog(self, state: _SearchSnapshot) -> _SearchSnapshot:
        """Restore validated promotion definitions into a fresh search state."""
        if self._promoted_composition_catalog_path is None:
            return state
        try:
            entries = self._promoted_composition_catalog.load(
                identifier_text(self._problem_instance_id),
                self._promotion_catalog_compatibility(),
                configuration_id_aliases=getattr(
                    self._solution_identity,
                    'compatibility_configuration_id_aliases',
                    {},
                ),
            )
            for entry in entries:
                specs = self._operator_specs(state)
                specs_by_text = {
                    identifier_text(spec.operator_id): spec for spec in specs
                }
                first = specs_by_text.get(entry.component_operator_id_texts[0])
                second = specs_by_text.get(entry.component_operator_id_texts[1])
                if first is None or second is None:
                    raise ValueError('promotion references an unknown Operator')
                expected_configuration_ids = (
                    identifier_text(first.configuration_id),
                    identifier_text(second.configuration_id),
                )
                if entry.component_configuration_id_texts != (
                    expected_configuration_ids
                ):
                    raise ValueError('promotion component configuration differs')
                composition_id = CompositionId(first.operator_id, second.operator_id)
                if any(
                    promoted.report.source_composition_id == composition_id
                    for promoted in state.promoted_operators
                ):
                    raise ValueError('promotion composition is repeated')
                maximum = min(
                    first.max_neighborhood_size,
                    second.max_neighborhood_size,
                )
                if entry.max_neighborhood_size > maximum:
                    raise ValueError('promotion maximum neighborhood size is invalid')
                inherited_groups = first.group_ids & second.group_ids
                expected_group_texts = tuple(
                    sorted(identifier_text(group_id) for group_id in inherited_groups)
                )
                if entry.inherited_group_id_texts != expected_group_texts:
                    raise ValueError('promotion Operator groups differ')
                if entry.feasibility_restoration != second.feasibility_restoration:
                    raise ValueError('promotion restoration capability differs')
                modification_count_definition = (
                    'min(m_f, m_g); component scales: '
                    f'{first.modification_count_definition}; '
                    f'{second.modification_count_definition}'
                )
                if entry.modification_count_definition != modification_count_definition:
                    raise ValueError('promotion modification-count definition differs')
                operator_id = self._new_promoted_operator_id(state, composition_id)
                if identifier_text(operator_id) != entry.operator_id_text:
                    raise ValueError('promotion generated Operator ID differs')
                report = PromotedCompositionReport(
                    operator_id=operator_id,
                    source_composition_id=composition_id,
                    component_operator_ids=(first.operator_id, second.operator_id),
                    first_admitted_solution_id=None,
                    max_neighborhood_size=entry.max_neighborhood_size,
                    feasibility_restoration=entry.feasibility_restoration,
                    inherited_group_ids=inherited_groups,
                    promoted_in_current_invocation=False,
                )
                promoted = _PromotedOperator(
                    report,
                    entry.modification_count_definition,
                )
                state = replace(
                    state,
                    promoted_operators=(*state.promoted_operators, promoted),
                )
            return state
        except PromotedCompositionCatalogError as error:
            raise ComponentSetupError(
                self._failure(
                    self._promoted_composition_catalog.component_id,
                    'promotion_catalog_load',
                    str(error),
                    error.kind,
                )
            ) from error
        except ComponentSetupError:
            raise
        except Exception as error:
            raise ComponentSetupError(
                self._failure(
                    self._promoted_composition_catalog.component_id,
                    'promotion_catalog_load',
                    f'promoted composition catalog could not be restored: {error}',
                    FailureKind.CONTRACT_VIOLATION,
                )
            ) from error

    def _save_promoted_catalog(self, state: _SearchSnapshot) -> ComponentFailure | None:
        """Persist all committed generated Operators for future fresh runs."""
        if self._promoted_composition_catalog_path is None:
            return None
        specs = {spec.operator_id: spec for spec in self._operator_specs(state)}
        entries: list[PromotedCatalogEntry] = []
        for promoted in state.promoted_operators:
            report = promoted.report
            first = specs[report.component_operator_ids[0]]
            second = specs[report.component_operator_ids[1]]
            entries.append(
                PromotedCatalogEntry(
                    operator_id_text=identifier_text(report.operator_id),
                    component_operator_id_texts=(
                        identifier_text(first.operator_id),
                        identifier_text(second.operator_id),
                    ),
                    component_configuration_id_texts=(
                        identifier_text(first.configuration_id),
                        identifier_text(second.configuration_id),
                    ),
                    max_neighborhood_size=report.max_neighborhood_size,
                    feasibility_restoration=report.feasibility_restoration,
                    inherited_group_id_texts=tuple(
                        sorted(
                            identifier_text(group_id)
                            for group_id in report.inherited_group_ids
                        )
                    ),
                    modification_count_definition=(
                        promoted.modification_count_definition
                    ),
                )
            )
        try:
            self._promoted_composition_catalog.save(
                identifier_text(self._problem_instance_id),
                self._promotion_catalog_compatibility(),
                tuple(entries),
            )
        except PromotedCompositionCatalogError as error:
            return self._failure(
                self._promoted_composition_catalog.component_id,
                'promotion_catalog_save',
                str(error),
                error.kind,
            )
        except Exception as error:
            return self._failure(
                self._promoted_composition_catalog.component_id,
                'promotion_catalog_save',
                f'promoted composition catalog could not be saved: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )
        return None

    def _provenance(self, state: _SearchSnapshot) -> RunProvenance:
        role_by_name = {role.value: role for role in ComponentRole}
        entries = []
        for role_text, component_id, configuration_id in self._manifest:
            if role_text not in role_by_name:
                continue
            objective_id = None
            if role_text == 'objective_function':
                objective = next(
                    item
                    for item in self._objective_declaration.objectives
                    if identifier_text(item.component_id) == component_id
                )
                objective_id = objective.objective_id
            entries.append(
                ComponentConfigurationEntry(
                    role_by_name[role_text],
                    component_id,
                    configuration_id,
                    objective_id,
                )
            )
        operator_specs = self._operator_specs(state)
        return RunProvenance(
            tuple(entries),
            tuple(
                OperatorCatalogEntry(
                    operator_id=spec.operator_id,
                    configuration_id=spec.configuration_id,
                    catalog_kind=spec.catalog_kind,
                    max_neighborhood_size=spec.max_neighborhood_size,
                    feasibility_restoration=spec.feasibility_restoration,
                    modification_count_definition=(spec.modification_count_definition),
                    component_operator_ids=spec.component_operator_ids,
                )
                for spec in operator_specs
            ),
            self._operator_groups,
            self._objective_bound_reports(),
        )

    def _objective_bound_reports(self) -> tuple[ObjectiveBoundReport, ...]:
        """Build declaration-ordered, safely formatted objective bounds."""
        reports: list[ObjectiveBoundReport] = []
        bounds = cast(
            tuple[ObjectiveValue | None, ...],
            self._objective_declaration.upper_bounds,
        )
        for objective, bound in zip(
            self._objective_declaration.objectives, bounds, strict=True
        ):
            if bound is None:
                continue
            try:
                formatted = objective.format(bound)
                if not isinstance(formatted, str) or not formatted:
                    formatted = '[unformatted]'
            except Exception:
                formatted = '[unformatted]'
            reports.append(ObjectiveBoundReport(objective.objective_id, formatted))
        return tuple(reports)

    def _eligible_pool_ids(self, state: _SearchSnapshot) -> frozenset[SolutionId]:
        eligible: set[SolutionId] = set()
        for record in state.registry.records:
            evaluation = record.evaluation_record
            if record.objective_bound_findings:
                continue
            if evaluation.processing_status is not ProcessingStatus.COMPLETED:
                continue
            if evaluation.assessment is ApplicationAssessment.VALID:
                eligible.add(record.solution_id)
            elif (
                self._config.feasibility_mode == 'relaxed'
                and evaluation.assessment is ApplicationAssessment.INFEASIBLE
            ):
                eligible.add(record.solution_id)
        eligible.update(state.archive_ids)
        return frozenset(eligible)

    def _coordinator(
        self, state: _SearchSnapshot, journal: EventJournal
    ) -> SearchCommitCoordinator:
        return SearchCommitCoordinator(
            state,
            journal,
            commit_sequence=state.commit_sequence,
            observers=self._observers,
        )

    def _append_invocation(
        self, coordinator: SearchCommitCoordinator, event: EventDraft
    ) -> None:
        coordinator.append_invocation_events((event,))

    def _append_failure_event(
        self,
        coordinator: SearchCommitCoordinator,
        failure: ComponentFailure,
        *,
        base_id: SolutionId | None,
        k: int | None,
        component_position: CompositionPosition | None,
        choice_id: ChoiceId | None = None,
    ) -> None:
        self._append_invocation(
            coordinator,
            self._component_failure_event(
                failure,
                base_id=base_id,
                k=k,
                component_position=component_position,
                choice_id=choice_id,
            ),
        )

    def _component_failure_event(
        self,
        failure: ComponentFailure,
        *,
        base_id: SolutionId | None,
        k: int | None,
        component_position: CompositionPosition | None,
        choice_id: ChoiceId | None = None,
        diagnostic_origin: str = 'current_operation',
    ) -> EventDraft:
        return self._event(
            'component_failure',
            EventPhase.FAILURE,
            {
                'failure': failure,
                'diagnostic_origin': diagnostic_origin,
                'base_solution_id': base_id,
                'k': k,
                'component_position': component_position,
            },
            source_id=failure.source,
            choice_id=choice_id or failure.choice_id,
            solution_id=failure.solution_id,
        )

    def _stop_and_finalize(
        self,
        coordinator: SearchCommitCoordinator,
        state: _SearchSnapshot,
        initial_archive_ids: tuple[SolutionId, ...],
        termination: TerminationReason,
        counters: InvocationCounters,
        started: float,
        wall_limit: float,
        diagnostics: list[ComponentFailure],
    ) -> TerminationReason:
        termination = self._publish_final_views(
            coordinator, state, termination, diagnostics
        )
        elapsed = self._elapsed(started)
        remaining = max(0.0, wall_limit - elapsed)
        try:
            self._append_invocation(
                coordinator,
                self._event(
                    'invocation_stopped',
                    EventPhase.INVOCATION,
                    {
                        'termination_reason': termination,
                        'counters': counters,
                        'registry_size': len(state.registry.records),
                        'archive_size': len(state.archive_ids),
                        'active_pool_size': len(state.active_ids),
                        'wall_time_limit_seconds': wall_limit,
                        'elapsed_seconds': elapsed,
                        'remaining_seconds': remaining,
                    },
                ),
            )
        except Exception as error:
            diagnostics.append(
                self._failure(
                    self._logger,
                    'run_logging',
                    f'invocation_stopped event append failed: {error}',
                    FailureKind.INFRASTRUCTURE_FAILURE,
                )
            )
        return termination

    def _publish_final_views(
        self,
        coordinator: SearchCommitCoordinator,
        state: _SearchSnapshot,
        termination: TerminationReason,
        diagnostics: list[ComponentFailure],
    ) -> TerminationReason:
        """Publish final registry and Pareto files before stopping the run."""
        for scope in ('registry', 'pareto'):
            failure = self._publish_view(
                invocation_id=coordinator.run_log.invocation_id,
                state=state,
                scope=scope,
                is_final=True,
            )
            if failure is None:
                continue
            diagnostics.append(failure)
            logging_failure = self._try_append_failure_event(coordinator, failure)
            if logging_failure is not None:
                diagnostics.append(logging_failure)
            termination = TerminationReason.COMPONENT_FAILURE
        return termination

    def _finalize(
        self,
        run_log: RunLog,
        initial_archive_ids: tuple[SolutionId, ...],
        state: _SearchSnapshot,
        termination: TerminationReason,
        counters: InvocationCounters,
    ) -> tuple[LogFinalizationResult | None, ComponentFailure | None]:
        try:
            promoted_sources = {
                event.choice_id.composition_id
                for event in run_log.events
                if event.event_type == 'composition_promoted'
                and event.choice_id is not None
                and event.choice_id.composition_id is not None
            }
            promoted_reports = tuple(
                replace(
                    promoted.report,
                    promoted_in_current_invocation=(
                        promoted.report.source_composition_id in promoted_sources
                    ),
                )
                for promoted in state.promoted_operators
            )
            result = self._logger.finalize(
                run_log,
                initial_archive_ids,
                state,
                self._objective_declaration,
                termination,
                counters,
                self._config.feedback_selection_mode,
                promoted_reports,
                self._solution_formatter,
            )
            return result, None
        except Exception as error:
            return None, self._failure(
                self._logger,
                'run_logging',
                f'RunLogger.finalize raised: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )

    def _publish_view(
        self,
        *,
        invocation_id: InvocationId,
        state: _SearchSnapshot,
        scope: Literal['registry', 'pareto'],
        is_final: bool,
    ) -> ComponentFailure | None:
        """Publish one immutable committed state through the configured logger."""
        try:
            self._logger.publish_view(
                invocation_id,
                self._problem_instance_id,
                state,
                state.commit_sequence,
                self._objective_declaration,
                scope,
                is_final,
                self._solution_formatter,
            )
            return None
        except Exception as error:
            return self._failure(
                self._logger,
                f'{scope}_view_publish',
                f'RunLogger.publish_view failed: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            )

    def _emit_progress(
        self,
        coordinator: SearchCommitCoordinator,
        started: float,
        wall_limit: float,
    ) -> float:
        elapsed = self._elapsed(started)
        self._append_invocation(
            coordinator,
            self._event(
                'progress_report',
                EventPhase.BUDGET,
                {
                    'wall_time_limit_seconds': wall_limit,
                    'elapsed_seconds': elapsed,
                    'remaining_seconds': max(0.0, wall_limit - elapsed),
                },
            ),
        )
        return time.monotonic()

    @staticmethod
    def _progress_due(started: float, previous: float, interval: float) -> bool:
        del started
        return time.monotonic() - previous >= interval

    def _hard_limit(
        self, counters: InvocationCounters, started: float, wall_limit: float
    ) -> bool:
        return (
            self._elapsed(started) >= wall_limit
            or counters.candidate_attempts
            >= self._config.budget_limits.max_candidate_attempts
            or counters.evaluation_requests
            >= self._config.budget_limits.max_evaluation_requests
        )

    @staticmethod
    def _stop_requested(token: StopToken | None) -> bool:
        if token is None:
            return False
        try:
            return token.is_stop_requested()
        except Exception:
            return True

    @staticmethod
    def _increment_count(
        counts: Mapping[SolutionId, int], solution_id: SolutionId
    ) -> Mapping[SolutionId, int]:
        next_counts = dict(counts)
        next_counts[solution_id] = next_counts.get(solution_id, 0) + 1
        return MappingProxyType(next_counts)

    def _capture_feedback_payload(self) -> object:
        try:
            return self._feedback_policy.capture_state()
        except Exception as error:
            raise _TransitionFailure(
                self._failure(
                    self._feedback_policy,
                    'policy_state_capture',
                    f'FeedbackPolicy.capture_state failed: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                )
            ) from error

    def _capture_random_payload(self) -> object:
        try:
            return self._random_source.capture_state()
        except Exception as error:
            raise _TransitionFailure(
                self._failure(
                    self._random_source,
                    'random_state_capture',
                    f'RandomSource.capture_state failed: {error}',
                    FailureKind.CALLBACK_EXCEPTION,
                )
            ) from error

    def _event(
        self,
        event_type: str,
        phase: EventPhase,
        data: Mapping[str, object],
        *,
        source_id: ComponentId | None = None,
        choice_id: ChoiceId | None = None,
        solution_id: SolutionId | None = None,
    ) -> EventDraft:
        return EventDraft(
            event_type,
            phase,
            data,
            source_id=source_id,
            choice_id=choice_id,
            solution_id=solution_id,
        )

    @staticmethod
    def _elapsed(started: float) -> float:
        return max(0.0, time.monotonic() - started)

    @staticmethod
    def _failure(
        component: ComponentIdentity | ComponentId,
        phase: str,
        reason: str,
        kind: FailureKind,
        *,
        solution_id: SolutionId | None = None,
        choice_id: ChoiceId | None = None,
    ) -> ComponentFailure:
        return ComponentFailure(
            kind,
            cast(ComponentId, getattr(component, 'component_id', component)),
            phase,
            reason,
            solution_id,
            choice_id,
        )


class _TransitionFailure(Exception):
    """Search callback or comparison failure that aborts a pending transition."""

    def __init__(
        self, failure: ComponentFailure, *, evaluation_requested: bool = False
    ) -> None:
        super().__init__(failure.reason)
        self.failure = failure
        self.evaluation_requested = evaluation_requested


def multiobjective_vns(
    *,
    solution_codec: SolutionCodec,
    solution_identity: SolutionIdentity,
    objective_declaration: ObjectiveDeclaration,
    operators: tuple[OperatorRegistration, ...],
    pre_registered_compositions: tuple[OperatorCompositionRegistration, ...] = (),
    application_evaluator: ApplicationEvaluator,
    problem_instance_id: ProblemInstanceId,
    config: MultiobjectiveVNSConfig,
    operator_groups: tuple[OperatorGroup, ...] = (),
    extensions: tuple[SearchExtensionRegistration, ...] = (),
    archive_policy: ArchivePolicy | None = None,
    exploration_policy: ExplorationPolicy | None = None,
    base_selection_policy: BaseSelectionPolicy | None = None,
    budget_policy: BudgetPolicy | None = None,
    feedback_policy: FeedbackPolicy | None = None,
    executor: Executor | None = None,
    random_source: RandomSource | None = None,
    event_observers: tuple[EventObserver, ...] = (),
    checkpoint_store: CheckpointStore | None = None,
    solution_formatter: SolutionFormatter | None = None,
    promoted_composition_catalog_path: Path | None = Path(
        './multiobjective-vns-promoted-compositions.json'
    ),
    initial_solutions: tuple[object, ...] | None = None,
    checkpoint: Checkpoint | object | None = None,
    checkpoint_interval: int | None = None,
    progress_report_interval_seconds: FiniteReal = 10.0,
    log_directory: Path = Path('./multiobjective-vns-logs/'),
    stop_token: StopToken | None = None,
) -> RunResult:
    """Create a search engine and execute one run invocation.

    :param pre_registered_compositions: Ordered Operator pairs available as
        standalone choices from the first search iteration.
    :param promoted_composition_catalog_path: File that carries promoted
        composition definitions across fresh searches, or ``None`` to disable.
    """
    engine = MultiobjectiveVNS(
        solution_codec=solution_codec,
        solution_identity=solution_identity,
        objective_declaration=objective_declaration,
        operators=operators,
        pre_registered_compositions=pre_registered_compositions,
        application_evaluator=application_evaluator,
        problem_instance_id=problem_instance_id,
        config=config,
        operator_groups=operator_groups,
        extensions=extensions,
        archive_policy=archive_policy,
        exploration_policy=exploration_policy,
        base_selection_policy=base_selection_policy,
        budget_policy=budget_policy,
        feedback_policy=feedback_policy,
        executor=executor,
        random_source=random_source,
        event_observers=event_observers,
        checkpoint_store=checkpoint_store,
        solution_formatter=solution_formatter,
        promoted_composition_catalog_path=promoted_composition_catalog_path,
    )
    return engine.run(
        initial_solutions=initial_solutions,
        checkpoint=checkpoint,
        checkpoint_interval=checkpoint_interval,
        progress_report_interval_seconds=progress_report_interval_seconds,
        log_directory=log_directory,
        stop_token=stop_token,
    )


__all__ = ['MultiobjectiveVNS', 'multiobjective_vns']
