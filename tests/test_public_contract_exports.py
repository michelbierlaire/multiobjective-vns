"""Tests for package-level shared-contract exports.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from inspect import Parameter, signature

import multiobjective_vns as package
from multiobjective_vns import (
    ActivePoolDecision,
    AlgorithmEvent,
    ApplicationAnalysis,
    ApplicationAssessment,
    ApplicationEvaluation,
    ArchiveDecision,
    BaseSelectionDecision,
    BudgetDecision,
    BudgetLimits,
    Candidate,
    CandidateContext,
    CandidateOutcome,
    CandidateProposal,
    Checkpoint,
    CheckpointComponent,
    CheckpointRestoreError,
    CheckpointStore,
    ChoiceId,
    ChoiceSelectionRequest,
    CodecPayload,
    ComponentFailure,
    ComponentIdentity,
    ComponentRegistrationError,
    ComponentSetupError,
    EventObserver,
    EventPhase,
    ExecutionResult,
    ExecutiveSummary,
    Executor,
    FailurePhase,
    FeedbackPolicy,
    InitialSolutionsError,
    LogFinalizationError,
    MultiobjectiveVNS,
    MultiobjectiveVNSConfig,
    ObjectiveBoundCount,
    ObjectiveBoundEventFinding,
    ObjectiveBoundFinding,
    ObjectiveBoundReport,
    ObjectiveDeclaration,
    OperatorChoiceCategory,
    OperatorRegistration,
    OperatorResult,
    PromotedCompositionReport,
    ResumeCompatibilityError,
    RunLog,
    RunLogger,
    RunProvenance,
    RunResult,
    SearchState,
    SolutionFormatter,
    SolutionRecord,
    StatePayload,
    TerminationReason,
    ViewerExportScope,
    multiobjective_vns,
)


def test_core_contracts_are_available_from_the_public_package() -> None:
    assert ApplicationAnalysis is object
    assert Candidate is not None
    assert CandidateContext is not None
    assert CandidateOutcome is not None
    assert CandidateProposal is not None
    assert OperatorResult is not None
    assert ApplicationEvaluation is not None
    assert ApplicationAssessment is not None
    assert ComponentFailure is not None
    assert ComponentIdentity is not None
    assert CheckpointComponent is not None
    assert SolutionRecord is not None
    assert ChoiceId is not None
    assert ChoiceSelectionRequest is not None
    assert OperatorChoiceCategory is not None
    assert OperatorRegistration is not None
    assert BudgetLimits is not None
    assert MultiobjectiveVNSConfig is not None
    assert ObjectiveDeclaration is not None
    assert ObjectiveBoundFinding is not None
    assert ObjectiveBoundCount is not None
    assert ObjectiveBoundEventFinding is not None
    assert ObjectiveBoundReport is not None
    assert CodecPayload is not None
    assert StatePayload is not None
    assert ArchiveDecision is not None
    assert ActivePoolDecision is not None
    assert BaseSelectionDecision is not None
    assert BudgetDecision is not None
    assert FeedbackPolicy is not None
    assert TerminationReason is not None
    assert AlgorithmEvent is not None
    assert EventObserver is not None
    assert EventPhase is not None
    assert FailurePhase is not None
    assert Checkpoint is not None
    assert SearchState is not None
    assert RunResult is not None
    assert RunLog is not None
    assert ExecutiveSummary is not None
    assert RunProvenance is not None
    assert PromotedCompositionReport is not None
    assert ExecutionResult is not None
    assert Executor is not None
    assert CheckpointStore is not None
    assert RunLogger is not None
    assert ViewerExportScope is not None
    assert SolutionFormatter is not None
    assert ComponentRegistrationError is not None
    assert ComponentSetupError is not None
    assert CheckpointRestoreError is not None
    assert ResumeCompatibilityError is not None


def test_candidate_limit_is_required_by_the_public_constructors() -> None:
    assert (
        signature(MultiobjectiveVNSConfig)
        .parameters['max_candidates_per_attempt']
        .default
        is Parameter.empty
    )
    assert signature(MultiobjectiveVNS).parameters['config'].default is Parameter.empty
    assert InitialSolutionsError is not None
    assert LogFinalizationError is not None
    assert MultiobjectiveVNS is not None
    assert multiobjective_vns is not None


def test_budget_public_contract_is_iteration_based() -> None:
    assert not hasattr(package, 'BudgetWorkUnit')
    assert not hasattr(package, 'BudgetStopScope')
