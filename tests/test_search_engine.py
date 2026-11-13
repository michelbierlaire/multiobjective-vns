"""Tests for the first complete sequential search path.

Created: 2026-10-29T14:26:25+00:00
Author: Michel Bierlaire, EPFL
"""

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.callbacks import (
    ApplicationEvaluator,
    Operator,
    SolutionCodec,
    SolutionIdentity,
)
from multiobjective_vns.config import BudgetLimits, MultiobjectiveVNSConfig
from multiobjective_vns.default_logger import DefaultRunLogger
from multiobjective_vns.engine import MultiobjectiveVNS, multiobjective_vns
from multiobjective_vns.exceptions import (
    ComponentRegistrationError,
    ComponentSetupError,
    InitialSolutionsError,
    ResumeCompatibilityError,
)
from multiobjective_vns.identifiers import (
    ChoiceId,
    ChoiceKind,
    CompositionId,
    InvocationId,
    OperatorId,
    ProblemInstanceId,
    SolutionId,
    identifier_text,
)
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)
from multiobjective_vns.policies import BaseSelectionPolicy, FeedbackPolicy
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
)
from multiobjective_vns.records.failures import ComponentFailure, FailureKind
from multiobjective_vns.records.policies import (
    BaseSelectionDecision,
    BaseSelectionRequest,
    ChoiceDescriptor,
    ChoiceSelectionRequest,
    FeedbackObservation,
    OperatorChoiceCategory,
)
from multiobjective_vns.records.reporting import (
    ComponentRole,
    ObjectiveBoundEventFinding,
    ReportDiagnosticKind,
    RunProvenance,
)
from multiobjective_vns.records.results import MaintenanceResult, ViewerExportScope
from multiobjective_vns.records.state import Checkpoint, SearchState
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
)
from multiobjective_vns.services import CheckpointStore, SolutionFormatter


class Objective:
    def __init__(self, objective_id: str) -> None:
        self.objective_id = objective_id
        self.component_id = f'{objective_id}-component'
        self.configuration_id = f'{objective_id}-v1'

    def compare(self, left: object, right: object) -> ObjectiveComparison:
        if left < right:  # type: ignore[operator]
            return ObjectiveComparison.LESS
        if left > right:  # type: ignore[operator]
            return ObjectiveComparison.GREATER
        return ObjectiveComparison.EQUAL

    def format(self, value: object) -> str:
        return str(value)

    def __str__(self) -> str:
        return self.objective_id


class Identity:
    component_id = 'identity'
    configuration_id = 'identity-v1'

    def equivalent(self, left: object, right: object) -> bool:
        return left == right


class Codec:
    component_id = 'codec'
    configuration_id = 'codec-v1'

    def capture(self, solution: object) -> bytes:
        return pickle.dumps(solution)

    def restore(self, payload: bytes) -> object:
        return pickle.loads(payload)


class FailingCaptureCodec(Codec):
    component_id = 'failing-capture-codec'
    configuration_id = 'failing-capture-codec-v1'

    def capture(self, solution: object) -> bytes:
        del solution
        raise RuntimeError('cannot encode solution')


class FailingSecondCaptureCodec(Codec):
    component_id = 'failing-second-capture-codec'
    configuration_id = 'failing-second-capture-codec-v1'

    def __init__(self) -> None:
        self.calls = 0

    def capture(self, solution: object) -> bytes:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError('checkpoint payload capture failed')
        return super().capture(solution)


class Evaluator:
    component_id = 'evaluator'
    configuration_id = 'evaluator-v1'

    def evaluate(
        self, solution: object, objectives: ObjectiveDeclaration
    ) -> ApplicationEvaluation:
        del objectives
        values = cast(tuple[int, int], solution)
        return ApplicationEvaluation(True, True, values)


class TiedObjectiveEvaluator:
    """Give distinct route plans the same objective vector."""

    component_id = 'tied-objective-evaluator'
    configuration_id = 'tied-objective-evaluator-v1'

    def evaluate(
        self, solution: object, objectives: ObjectiveDeclaration
    ) -> ApplicationEvaluation:
        del solution, objectives
        return ApplicationEvaluation(True, True, (10, 20))


class InfeasibleCandidateEvaluator(Evaluator):
    def evaluate(
        self, solution: object, objectives: ObjectiveDeclaration
    ) -> ApplicationEvaluation:
        del objectives
        values = cast(tuple[int, int], solution)
        return ApplicationEvaluation(True, values[0] != 4, values)


class CandidateOperator:
    def __init__(
        self, candidate_mode: str, operator_id: str = 'candidate-operator'
    ) -> None:
        self.component_id = operator_id
        self.configuration_id = f'{operator_id}-v1'
        self.candidate_mode = candidate_mode
        self.sizes: list[int] = []
        self.base_evaluations: list[EvaluationRecord | None] = []

    def call(self, context: CandidateContext) -> OperatorResult:
        self.sizes.append(context.k)
        self.base_evaluations.append(context.base_evaluation)
        if self.candidate_mode == 'duplicate':
            solution = context.base
        else:
            base = cast(tuple[int, int], context.base)
            solution = (base[0] - context.k, base[1] + context.k)
        return OperatorResult(
            candidates=(CandidateProposal(Candidate(solution), context.k),)
        )


class CursorOperator:
    """Return duplicates except for one configured archive admission."""

    def __init__(self, operator_id: str, *, admit_on_call: int | None = None) -> None:
        self.component_id = operator_id
        self.configuration_id = f'{operator_id}-v1'
        self.admit_on_call = admit_on_call
        self.sizes: list[int] = []

    def call(self, context: CandidateContext) -> OperatorResult:
        self.sizes.append(context.k)
        solution = (8, 8) if len(self.sizes) == self.admit_on_call else context.base
        return OperatorResult(
            candidates=(CandidateProposal(Candidate(solution), context.k),)
        )


class CandidateListOperator:
    """Return two ordered candidates or repeat the supplied base."""

    def __init__(self, operator_id: str, *, candidates: bool) -> None:
        self.component_id = operator_id
        self.configuration_id = f'{operator_id}-v1'
        self.return_candidates = candidates
        self.calls: list[tuple[object, int, int]] = []

    def call(self, context: CandidateContext) -> OperatorResult:
        self.calls.append((context.base, context.k, context.max_candidates))
        if not self.return_candidates:
            candidate = Candidate(context.base)
            return OperatorResult(candidates=(CandidateProposal(candidate, 1),))
        return OperatorResult(
            candidates=(
                CandidateProposal(Candidate((9, 12)), 1),
                CandidateProposal(Candidate((8, 13)), 1),
            )
        )


class PreferDominatedBasePolicy:
    component_id = 'prefer-dominated-base-policy'
    configuration_id = 'prefer-dominated-base-policy-v1'

    def select(self, request: BaseSelectionRequest) -> BaseSelectionDecision:
        dominated = next(
            (
                record
                for record in request.eligible_records
                if record.archive_status is ArchiveStatus.DOMINATED
            ),
            None,
        )
        selected = dominated or request.eligible_records[0]
        return BaseSelectionDecision(selected.solution_id)

    def capture_state(self) -> object:
        return None

    def restore_state(self, payload: object) -> None:
        if payload is not None:
            raise TypeError('stateless base selection policy expects None')


class RecordingCheckpointStore:
    component_id = 'test-checkpoint-store'
    configuration_id = 'test-checkpoint-store-v1'

    def __init__(self) -> None:
        self.checkpoints: list[Checkpoint] = []

    def save(self, checkpoint: Checkpoint) -> str:
        self.checkpoints.append(checkpoint)
        return f'checkpoint-{len(self.checkpoints)}'

    def load(self, reference: object) -> Checkpoint:
        index = int(str(reference).removeprefix('checkpoint-')) - 1
        return self.checkpoints[index]


class FailingSaveStore(RecordingCheckpointStore):
    component_id = 'failing-checkpoint-store'
    configuration_id = 'failing-checkpoint-store-v1'

    def save(self, checkpoint: Checkpoint) -> str:
        del checkpoint
        raise RuntimeError('store unavailable')


class DuplicateIdFormatter:
    component_id = 'candidate-operator'

    def describe(self, solution: object) -> str:
        return str(solution)


class RelaxedEvaluator:
    component_id = 'relaxed-evaluator'
    configuration_id = 'relaxed-evaluator-v1'

    def evaluate(
        self, solution: object, objectives: ObjectiveDeclaration
    ) -> ApplicationEvaluation:
        del objectives
        first, second, feasible, objective_values_available = cast(
            tuple[int, int, bool, bool], solution
        )
        values = (first, second) if objective_values_available else None
        return ApplicationEvaluation(True, feasible, values)


class RelaxedOperator:
    def __init__(
        self,
        operator_id: str,
        *,
        restoration: bool,
        invalid_infeasible_values: bool = False,
        violate_restoration: bool = False,
    ) -> None:
        self.component_id = operator_id
        self.configuration_id = f'{operator_id}-v1'
        self.restoration = restoration
        self.invalid_infeasible_values = invalid_infeasible_values
        self.violate_restoration = violate_restoration
        self.requests: list[bool] = []

    def call(self, context: CandidateContext) -> OperatorResult:
        self.requests.append(context.ensure_feasible)
        if not self.restoration:
            candidate = (4, 6, False, not self.invalid_infeasible_values)
        elif context.ensure_feasible:
            candidate = (3, 7, not self.violate_restoration, True)
        else:
            candidate = (4, 6, False, True)
        return OperatorResult(
            candidates=(CandidateProposal(Candidate(candidate), context.k),)
        )


class CompositionEvaluator:
    component_id = 'composition-evaluator'
    configuration_id = 'composition-evaluator-v1'

    def __init__(self) -> None:
        self.evaluated_solutions: list[object] = []
        self.analysis = ('base analysis',)

    def evaluate(
        self, solution: object, objectives: ObjectiveDeclaration
    ) -> ApplicationEvaluation:
        del objectives
        self.evaluated_solutions.append(solution)
        return ApplicationEvaluation(
            True,
            True,
            cast(tuple[int, int], solution),
            analysis=self.analysis,
        )


class SequenceRandomSource:
    component_id = 'sequence-random-source'
    configuration_id = 'sequence-random-source-v1'

    def __init__(self, values: tuple[float, ...]) -> None:
        self._values = values
        self._index = 0

    def random(self) -> float:
        value = self._values[self._index]
        self._index += 1
        return value

    @property
    def draw_count(self) -> int:
        return self._index

    def capture_state(self) -> object:
        return self._index

    def restore_state(self, payload: object) -> None:
        self._index = cast(int, payload)


class CompositionStepOperator:
    def __init__(
        self,
        operator_id: str,
        *,
        intermediate: bool,
        modification_count: int = 1,
        fail_on_internal: bool = False,
    ) -> None:
        self.component_id = operator_id
        self.configuration_id = f'{operator_id}-v1'
        self.intermediate = intermediate
        self.modification_count = modification_count
        self.fail_on_internal = fail_on_internal
        self.calls: list[tuple[object, int, bool]] = []
        self.base_evaluations: list[EvaluationRecord | None] = []

    def call(self, context: CandidateContext) -> OperatorResult:
        self.calls.append((context.base, context.k, context.ensure_feasible))
        self.base_evaluations.append(context.base_evaluation)
        if self.fail_on_internal and context.base == 'internal composition value':
            raise RuntimeError('second composition step failed')
        if self.modification_count == 0:
            return OperatorResult(candidates=())
        if self.intermediate:
            return OperatorResult(
                candidates=(
                    CandidateProposal(
                        Candidate('internal composition value'),
                        min(context.k, self.modification_count),
                    ),
                )
            )
        return OperatorResult(
            candidates=(
                CandidateProposal(
                    Candidate((3, 7)), min(context.k, self.modification_count)
                ),
            )
        )


class BatchCompositionOperator:
    """Return ordered intermediate or final candidates for composition tests."""

    def __init__(self, operator_id: str, *, first: bool) -> None:
        self.component_id = operator_id
        self.configuration_id = f'{operator_id}-v1'
        self.first = first
        self.calls: list[tuple[object, int]] = []

    def call(self, context: CandidateContext) -> OperatorResult:
        self.calls.append((context.base, context.max_candidates))
        proposals: tuple[CandidateProposal, ...]
        if self.first:
            proposals = (
                CandidateProposal(Candidate(('intermediate', 0)), 1),
                CandidateProposal(Candidate(('intermediate', 1)), 1),
            )
        elif context.base == ('intermediate', 0):
            proposals = (
                CandidateProposal(Candidate((4, 6)), 1),
                CandidateProposal(Candidate((3, 8)), 1),
            )
        else:
            proposals = (CandidateProposal(Candidate((6, 4)), 1),)
        return OperatorResult(candidates=proposals[: context.max_candidates])


class SelectCompositionFeedback:
    component_id = 'select-composition-feedback'
    configuration_id = 'select-composition-feedback-v1'

    def __init__(
        self,
        first_id: str,
        second_id: str,
        *,
        fallback_operator_id: str | None = None,
    ) -> None:
        self.choice_id = ChoiceId.for_composition(CompositionId(first_id, second_id))
        self.fallback_choice_id = (
            ChoiceId.for_operator(fallback_operator_id)
            if fallback_operator_id is not None
            else None
        )
        self.observations: list[object] = []

    def distribution(self, request: ChoiceSelectionRequest) -> dict[ChoiceId, float]:
        choices = request.choices
        if request.category is OperatorChoiceCategory.COMPOSITION:
            assert all(
                choice.choice_id.kind is ChoiceKind.COMPOSITION for choice in choices
            )
        else:
            assert all(
                choice.choice_id.kind is ChoiceKind.OPERATOR for choice in choices
            )
        available_ids = {choice.choice_id for choice in choices}
        selected_id = self.choice_id
        if selected_id not in available_ids:
            assert self.fallback_choice_id in available_ids
            selected_id = self.fallback_choice_id
        return {
            choice.choice_id: float(choice.choice_id == selected_id)
            for choice in choices
        }

    def observe(self, observation: FeedbackObservation) -> None:
        self.observations.append(observation)

    def reset(self) -> None:
        self.observations.clear()

    def capture_state(self) -> object:
        return tuple(self.observations)

    def restore_state(self, payload: object) -> None:
        self.observations = list(cast(tuple[object, ...], payload))


class SelectPromotedCompositionFeedback:
    component_id = 'select-promoted-composition-feedback'
    configuration_id = 'select-promoted-composition-feedback-v1'

    def distribution(self, request: ChoiceSelectionRequest) -> dict[ChoiceId, float]:
        choices = request.choices
        expected_kind = (
            ChoiceKind.OPERATOR
            if request.category is OperatorChoiceCategory.STANDALONE
            else ChoiceKind.COMPOSITION
        )
        assert all(choice.choice_id.kind is expected_kind for choice in choices)
        if request.category is OperatorChoiceCategory.STANDALONE:
            selected = next(
                (
                    choice.choice_id
                    for choice in choices
                    if choice.operator_ids[0] not in ('first-step', 'second-step')
                ),
                choices[0].choice_id,
            )
        else:
            selected = next(
                (
                    choice.choice_id
                    for choice in choices
                    if choice.choice_id
                    == ChoiceId.for_composition(
                        CompositionId('first-step', 'second-step')
                    )
                ),
                choices[0].choice_id,
            )
        return {
            choice.choice_id: float(choice.choice_id == selected) for choice in choices
        }

    def observe(self, observation: FeedbackObservation) -> None:
        del observation

    def reset(self) -> None:
        return None

    def capture_state(self) -> object:
        return None

    def restore_state(self, payload: object) -> None:
        del payload


class SelectCompositionUsingPromotedFeedback(SelectPromotedCompositionFeedback):
    component_id = 'select-composition-using-promoted-feedback'
    configuration_id = 'select-composition-using-promoted-feedback-v1'

    def distribution(self, request: ChoiceSelectionRequest) -> dict[ChoiceId, float]:
        choices = request.choices
        target = next(
            (
                choice.choice_id
                for choice in choices
                if request.category is OperatorChoiceCategory.COMPOSITION
                and choice.choice_id.kind.value == 'composition'
                and any(
                    operator_id not in ('first-step', 'second-step')
                    for operator_id in choice.operator_ids
                )
            ),
            ChoiceId.for_composition(CompositionId('first-step', 'second-step')),
        )
        return {
            choice.choice_id: float(choice.choice_id == target) for choice in choices
        }


class RecordingFeedback:
    component_id = 'recording-feedback'
    configuration_id = 'recording-feedback-v1'

    def __init__(self) -> None:
        self.observations: list[FeedbackObservation] = []
        self.reset_count = 0

    def distribution(self, request: ChoiceSelectionRequest) -> dict[ChoiceId, float]:
        probability = 1.0 / len(request.choices)
        return {choice.choice_id: probability for choice in request.choices}

    def observe(self, observation: FeedbackObservation) -> None:
        self.observations.append(observation)

    def reset(self) -> None:
        self.observations.clear()
        self.reset_count += 1

    def capture_state(self) -> object:
        return tuple(self.observations), self.reset_count

    def restore_state(self, payload: object) -> None:
        observations, reset_count = cast(
            tuple[tuple[FeedbackObservation, ...], int], payload
        )
        self.observations = list(observations)
        self.reset_count = reset_count


def make_composition_engine(
    first: CompositionStepOperator,
    second: CompositionStepOperator,
    evaluator: CompositionEvaluator,
    feedback: SelectCompositionFeedback,
    operator_groups: tuple[OperatorGroup, ...] | None = None,
    max_candidate_attempts: int = 1,
    max_candidates_per_attempt: int = 1,
    composition_selection_probability: float = 1.0,
    random_source: RandomSource | None = None,
    pre_registered_compositions: tuple[OperatorCompositionRegistration, ...] = (),
    promoted_composition_catalog_path: Path | None = None,
    operator_configuration_ids: tuple[str, str] = (
        'first-step-v1',
        'second-step-v1',
    ),
) -> MultiobjectiveVNS:
    groups = operator_groups or (
        OperatorGroup('promising-pair', frozenset(('first-step', 'second-step'))),
    )
    return MultiobjectiveVNS(
        solution_codec=cast(SolutionCodec, Codec()),
        solution_identity=Identity(),
        objective_declaration=ObjectiveDeclaration(
            (Objective('composition-cost-a'), Objective('composition-cost-b'))
        ),
        operators=(
            OperatorRegistration(
                operator_id='first-step',
                configuration_id=operator_configuration_ids[0],
                operator=first,
                initial_state=None,
                max_neighborhood_size=2,
                feasibility_restoration=False,
                modification_count_definition='one internal step',
            ),
            OperatorRegistration(
                operator_id='second-step',
                configuration_id=operator_configuration_ids[1],
                operator=second,
                initial_state=None,
                max_neighborhood_size=1,
                feasibility_restoration=False,
                modification_count_definition='one final step',
            ),
        ),
        pre_registered_compositions=pre_registered_compositions,
        operator_groups=groups,
        application_evaluator=cast(ApplicationEvaluator, evaluator),
        problem_instance_id=cast(ProblemInstanceId, 'composition-problem-v1'),
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=max_candidates_per_attempt,
            max_neighborhood_size=2,
            composition_selection_probability=composition_selection_probability,
            feedback_selection_mode='uniform',
            budget_limits=BudgetLimits(
                max_wall_time_seconds=60,
                max_candidate_attempts=max_candidate_attempts,
                max_evaluation_requests=max_candidate_attempts
                * max_candidates_per_attempt,
            ),
        ),
        feedback_policy=cast(FeedbackPolicy, feedback),
        random_source=random_source,
        promoted_composition_catalog_path=promoted_composition_catalog_path,
    )


def test_pre_registered_composition_is_a_standalone_choice_with_component_events(
    tmp_path: Path,
) -> None:
    first = CompositionStepOperator('first-step', intermediate=True)
    second = CompositionStepOperator('second-step', intermediate=False)
    composition = OperatorCompositionRegistration(
        operator_id='pre-registered-move-and-repair',
        configuration_id='pre-registered-move-and-repair-v1',
        first_operator_id='first-step',
        second_operator_id='second-step',
    )
    feedback = SelectCompositionFeedback(
        'first-step',
        'second-step',
        fallback_operator_id='pre-registered-move-and-repair',
    )
    engine = make_composition_engine(
        first,
        second,
        CompositionEvaluator(),
        feedback,
        max_candidate_attempts=1,
        composition_selection_probability=0.0,
        pre_registered_compositions=(composition,),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.run_log is not None
    selected = next(
        event
        for event in result.run_log.events
        if event.event_type == 'choice_selected'
    )
    assert selected.choice_id == ChoiceId.for_operator('pre-registered-move-and-repair')
    outcome = result.candidate_outcomes[-1]
    assert outcome.choice_id == selected.choice_id
    assert outcome.component_position is CompositionPosition.SECOND
    assert outcome.submitted_solution == (3, 7)
    assert first.calls == [((5, 5), 1, False)]
    assert second.calls == [('internal composition value', 1, True)]
    assert second.base_evaluations == [None]

    operator_events = tuple(
        event
        for event in result.run_log.events
        if event.event_type == 'operator_result'
    )
    assert tuple(event.source_id for event in operator_events) == (
        'first-step',
        'second-step',
    )
    assert tuple(event.data['component_position'] for event in operator_events) == (
        CompositionPosition.FIRST,
        CompositionPosition.SECOND,
    )
    composition_event = next(
        event
        for event in result.run_log.events
        if event.event_type == 'composition_result'
    )
    assert composition_event.data['execution_source'] == 'pre_registered'
    assert composition_event.data['component_operator_ids'] == (
        'first-step',
        'second-step',
    )

    available = next(
        event
        for event in result.run_log.events
        if event.event_type == 'choice_set_available'
    )
    choice_ids = {
        item.choice_id
        for item in cast(
            tuple[ChoiceDescriptor, ...],
            available.data['newly_eligible_choices'],
        )
    }
    assert ChoiceId.for_operator('pre-registered-move-and-repair') in choice_ids
    assert (
        ChoiceId.for_composition(CompositionId('first-step', 'second-step'))
        not in choice_ids
    )
    assert result.executive_summary is not None
    entry = next(
        item
        for item in result.executive_summary.provenance.operators
        if item.operator_id == 'pre-registered-move-and-repair'
    )
    assert entry.catalog_kind == 'pre_registered_composite'
    assert entry.component_operator_ids == ('first-step', 'second-step')
    assert entry.configuration_id == 'pre-registered-move-and-repair-v1'


def test_pre_registered_composition_references_and_ordered_pairs_are_validated() -> (
    None
):
    first = CompositionStepOperator('first-step', intermediate=True)
    second = CompositionStepOperator('second-step', intermediate=False)
    feedback = SelectCompositionFeedback('first-step', 'second-step')

    with pytest.raises(ComponentRegistrationError, match='unknown'):
        make_composition_engine(
            first,
            second,
            CompositionEvaluator(),
            feedback,
            pre_registered_compositions=(
                OperatorCompositionRegistration(
                    'unknown-pair', 'unknown-pair-v1', 'missing', 'second-step'
                ),
            ),
        )

    repeated = OperatorCompositionRegistration(
        'first-composite', 'first-composite-v1', 'first-step', 'second-step'
    )
    repeated_pair = OperatorCompositionRegistration(
        'second-composite', 'second-composite-v1', 'first-step', 'second-step'
    )
    with pytest.raises(ComponentRegistrationError, match='pair'):
        make_composition_engine(
            first,
            second,
            CompositionEvaluator(),
            feedback,
            pre_registered_compositions=(repeated, repeated_pair),
        )


def test_checkpoint_rejects_changed_pre_registered_component_order(
    tmp_path: Path,
) -> None:
    first = CompositionStepOperator('first-step', intermediate=True)
    second = CompositionStepOperator('second-step', intermediate=False)
    registered = OperatorCompositionRegistration(
        'pre-registered-move-and-repair',
        'pre-registered-move-and-repair-v1',
        'first-step',
        'second-step',
    )
    first_result = make_composition_engine(
        first,
        second,
        CompositionEvaluator(),
        SelectCompositionFeedback('first-step', 'second-step'),
        pre_registered_compositions=(registered,),
    ).run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path / 'original',
    )
    reversed_registration = OperatorCompositionRegistration(
        registered.operator_id,
        registered.configuration_id,
        'second-step',
        'first-step',
    )
    resumed = make_composition_engine(
        first,
        second,
        CompositionEvaluator(),
        SelectCompositionFeedback('first-step', 'second-step'),
        pre_registered_compositions=(reversed_registration,),
    )

    with pytest.raises(ResumeCompatibilityError, match='manifest'):
        resumed.run(
            checkpoint=Checkpoint(first_result.search_state),
            log_directory=tmp_path / 'resumed',
        )


def make_relaxed_engine(
    *,
    invalid_infeasible_values: bool = False,
    violate_restoration: bool = False,
    max_candidate_attempts: int = 2,
    checkpoint_store: CheckpointStore | None = None,
) -> tuple[MultiobjectiveVNS, RelaxedOperator, RelaxedOperator]:
    ordinary = RelaxedOperator(
        'ordinary-operator',
        restoration=False,
        invalid_infeasible_values=invalid_infeasible_values,
    )
    restoration = RelaxedOperator(
        'restoration-operator',
        restoration=True,
        violate_restoration=violate_restoration,
    )
    engine = MultiobjectiveVNS(
        solution_codec=cast(SolutionCodec, Codec()),
        solution_identity=Identity(),
        objective_declaration=ObjectiveDeclaration(
            (Objective('relaxed-cost-a'), Objective('relaxed-cost-b'))
        ),
        operators=(
            OperatorRegistration(
                operator_id='ordinary-operator',
                configuration_id='ordinary-operator-v1',
                operator=ordinary,
                initial_state=None,
                max_neighborhood_size=1,
                feasibility_restoration=False,
                modification_count_definition='one requested move',
            ),
            OperatorRegistration(
                operator_id='restoration-operator',
                configuration_id='restoration-operator-v1',
                operator=restoration,
                initial_state=None,
                max_neighborhood_size=1,
                feasibility_restoration=True,
                modification_count_definition='one requested move',
            ),
        ),
        application_evaluator=cast(ApplicationEvaluator, RelaxedEvaluator()),
        problem_instance_id=cast(ProblemInstanceId, 'relaxed-test-problem-v1'),
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            max_neighborhood_size=1,
            composition_selection_probability=0.0,
            feasibility_mode='relaxed',
            max_infeasible_steps=1,
            feedback_selection_mode='uniform',
            random_seed=1,
            budget_limits=BudgetLimits(
                max_wall_time_seconds=60,
                max_candidate_attempts=max_candidate_attempts,
                max_evaluation_requests=max_candidate_attempts,
            ),
        ),
        checkpoint_store=checkpoint_store,
    )
    return engine, ordinary, restoration


def test_relaxed_search_starts_excursion_and_applies_restoration_limit(
    tmp_path: Path,
) -> None:
    engine, ordinary, restoration = make_relaxed_engine()

    result = engine.run(
        initial_solutions=((5, 5, True, True),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.run_log is not None
    transitions = [
        event.data['transition']
        for event in result.run_log.events
        if event.event_type == 'excursion_transition'
    ]
    assert transitions == ['started', 'feasible_exit']
    assert result.counters.candidate_attempts == 2
    assert ordinary.requests[0] is False
    assert True in restoration.requests
    assert [outcome.assessment for outcome in result.candidate_outcomes] == [
        ApplicationAssessment.VALID,
        ApplicationAssessment.INFEASIBLE,
        ApplicationAssessment.VALID,
    ]
    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED


def test_relaxed_search_can_continue_from_objective_invalid_infeasible_solution(
    tmp_path: Path,
) -> None:
    engine, ordinary, restoration = make_relaxed_engine(invalid_infeasible_values=True)

    result = engine.run(
        initial_solutions=((5, 5, True, True),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.run_log is not None
    transitions = [
        event
        for event in result.run_log.events
        if event.event_type == 'excursion_transition'
    ]
    invalid_record = next(
        record
        for record in result.search_state.visited_solutions()
        if record.solution == (4, 6, False, False)
    )
    assert [event.data['transition'] for event in transitions] == [
        'started',
        'feasible_exit',
    ]
    assert transitions[0].data['current_solution_id'] == invalid_record.solution_id
    assert result.candidate_outcomes[1].assessment is ApplicationAssessment.INVALID
    assert invalid_record.solution_id not in {
        record.solution_id for record in result.search_state.active_solutions()
    }
    assert ordinary.requests[0] is False
    assert True in restoration.requests


def test_restoration_guarantee_failure_preserves_excursion_position(
    tmp_path: Path,
) -> None:
    engine, _, restoration = make_relaxed_engine(violate_restoration=True)

    result = engine.run(
        initial_solutions=((5, 5, True, True),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.run_log is not None
    transitions = [
        event.data['transition']
        for event in result.run_log.events
        if event.event_type == 'excursion_transition'
    ]
    assert transitions == ['started']
    assert True in restoration.requests
    assert any(
        failure.phase == 'operator_guarantee'
        for failure in result.candidate_outcomes[-1].failures
    )
    assert result.candidate_outcomes[-1].assessment is ApplicationAssessment.INFEASIBLE


def test_resume_continues_a_committed_infeasible_excursion(tmp_path: Path) -> None:
    checkpoint_store = RecordingCheckpointStore()
    engine, ordinary, restoration = make_relaxed_engine(
        max_candidate_attempts=1,
        checkpoint_store=cast(CheckpointStore, checkpoint_store),
    )

    first = engine.run(
        initial_solutions=((5, 5, True, True),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )
    resumed = engine.run(
        checkpoint=first.checkpoint_reference,
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert first.checkpoint_reference is not None
    assert first.run_log is not None
    assert resumed.run_log is not None
    assert [
        event.data['transition']
        for event in first.run_log.events
        if event.event_type == 'excursion_transition'
    ] == ['started']
    assert any(
        event.event_type == 'base_selected'
        and event.data['selection_source'] == 'active_excursion'
        for event in resumed.run_log.events
    )
    assert any(
        event.event_type == 'choice_selected'
        and event.data['restoration_required'] is True
        for event in resumed.run_log.events
    )
    assert [
        event.data['transition']
        for event in resumed.run_log.events
        if event.event_type == 'excursion_transition'
    ] == ['feasible_exit']
    assert ordinary.requests[0] is False
    assert True in restoration.requests


def test_composition_processes_only_its_final_output_and_logs_pair_feedback(
    tmp_path: Path,
) -> None:
    first = CompositionStepOperator('first-step', intermediate=True)
    second = CompositionStepOperator('second-step', intermediate=False)
    evaluator = CompositionEvaluator()
    feedback = SelectCompositionFeedback('first-step', 'second-step')
    engine = make_composition_engine(first, second, evaluator, feedback)

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.run_log is not None
    target_choice = feedback.choice_id
    available = next(
        event
        for event in result.run_log.events
        if event.event_type == 'choice_set_available'
    )
    target_descriptor = next(
        descriptor
        for descriptor in cast(
            tuple[ChoiceDescriptor, ...],
            available.data['newly_eligible_choices'],
        )
        if descriptor.choice_id == target_choice
    )
    assert target_descriptor.max_neighborhood_size == 1
    assert target_descriptor.shared_group_ids == frozenset(('promising-pair',))
    assert first.calls == [((5, 5), 1, False)]
    assert second.calls == [('internal composition value', 1, True)]
    first_base_evaluation = first.base_evaluations[0]
    assert first_base_evaluation is not None
    assert first_base_evaluation.application_evaluation is not None
    assert first_base_evaluation.application_evaluation.analysis is evaluator.analysis
    assert second.base_evaluations == [None]
    assert evaluator.evaluated_solutions == [(5, 5), (3, 7)]
    outcome = result.candidate_outcomes[-1]
    assert outcome.submitted_solution == (3, 7)
    assert outcome.choice_id == target_choice
    assert outcome.component_position is CompositionPosition.SECOND
    assert outcome.m == 1
    operator_events = [
        event
        for event in result.run_log.events
        if event.event_type == 'operator_result'
    ]
    assert [event.data['component_position'] for event in operator_events] == [
        CompositionPosition.FIRST,
        CompositionPosition.SECOND,
    ]
    composition_event = next(
        event
        for event in result.run_log.events
        if event.event_type == 'composition_result'
    )
    assert composition_event.data['final_candidate_count'] == 1
    assert composition_event.data['final_modification_counts'] == (1,)
    assert composition_event.data['first_candidate_count'] == 1
    assert composition_event.data['second_call_count'] == 1
    assert len(feedback.observations) == 1
    assert result.executive_summary is not None
    pair_stats = [
        item
        for item in result.executive_summary.choice_statistics
        if item.choice_id == target_choice
    ]
    assert len(pair_stats) == 1
    assert pair_stats[0].attempt_count == 1
    assert pair_stats[0].archive_admission_count == 1


def test_composition_applies_second_operator_to_each_intermediate_in_order(
    tmp_path: Path,
) -> None:
    first = BatchCompositionOperator('first-step', first=True)
    second = BatchCompositionOperator('second-step', first=False)
    engine = make_composition_engine(
        cast(CompositionStepOperator, first),
        cast(CompositionStepOperator, second),
        CompositionEvaluator(),
        SelectCompositionFeedback('first-step', 'second-step'),
        max_candidates_per_attempt=3,
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert tuple(
        outcome.submitted_solution for outcome in result.candidate_outcomes[1:]
    ) == ((4, 6), (3, 8), (6, 4))
    assert tuple(
        outcome.candidate_index for outcome in result.candidate_outcomes[1:]
    ) == (1, 2, 3)
    assert first.calls == [((5, 5), 3)]
    assert second.calls == [
        (('intermediate', 0), 3),
        (('intermediate', 1), 1),
    ]
    assert result.run_log is not None
    component_events = tuple(
        event
        for event in result.run_log.events
        if event.event_type == 'operator_result'
    )
    assert tuple(event.data['candidate_count'] for event in component_events) == (
        2,
        2,
        1,
    )
    assert tuple(event.data['modification_counts'] for event in component_events) == (
        (1, 1),
        (1, 1),
        (1,),
    )
    assert tuple(event.data['component_call_index'] for event in component_events) == (
        1,
        2,
        3,
    )
    assert tuple(event.data['input_candidate_index'] for event in component_events) == (
        None,
        1,
        2,
    )
    processed_events = tuple(
        event
        for event in result.run_log.events
        if event.event_type == 'candidate_processed'
        and event.data['source'] is SubmissionSource.OPERATOR
    )
    assert tuple(event.data['candidate_index'] for event in processed_events) == (
        1,
        2,
        3,
    )
    composition_event = next(
        event
        for event in result.run_log.events
        if event.event_type == 'composition_result'
    )
    assert composition_event.data['final_candidate_count'] == 3
    assert composition_event.data['final_modification_counts'] == (1, 1, 1)
    feedback = next(
        event
        for event in result.run_log.events
        if event.event_type == 'feedback_observed'
    )
    assert feedback.data['archive_admission_count'] == 3
    assert feedback.data['attempt_admitted'] is True


def test_archive_admission_promotes_composition_and_reports_it(
    tmp_path: Path,
) -> None:
    first = CompositionStepOperator('first-step', intermediate=True)
    second = CompositionStepOperator('second-step', intermediate=False)
    engine = make_composition_engine(
        first,
        second,
        CompositionEvaluator(),
        cast(SelectCompositionFeedback, SelectPromotedCompositionFeedback()),
        max_candidate_attempts=2,
        composition_selection_probability=0.5,
        random_source=cast(
            RandomSource, SequenceRandomSource((0.1, 0.0, 0.9, 0.0, 0.1, 0.0, 0.1, 0.0))
        ),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.run_log is not None
    promotion = next(
        event
        for event in result.run_log.events
        if event.event_type == 'composition_promoted'
    )
    archive = next(
        event
        for event in result.run_log.events
        if event.event_type == 'archive_changed'
        and event.solution_id == promotion.solution_id
    )
    assert promotion.sequence_number == archive.sequence_number + 1
    assert promotion.commit_sequence == archive.commit_sequence
    assert promotion.choice_id == ChoiceId.for_composition(
        CompositionId('first-step', 'second-step')
    )
    assert promotion.solution_id == result.candidate_outcomes[1].solution_id
    assert promotion.data['component_operator_ids'] == (
        'first-step',
        'second-step',
    )
    assert promotion.data['inherited_group_ids'] == ('promising-pair',)
    assert result.executive_summary is not None
    assert result.executive_summary.composition_selection_probability == 0.5
    promoted_report = result.executive_summary.promoted_compositions[0]
    assert promoted_report.source_composition_id == CompositionId(
        'first-step', 'second-step'
    )
    assert promoted_report.promoted_in_current_invocation is True
    selected_ids = [
        event.choice_id
        for event in result.run_log.events
        if event.event_type == 'choice_selected'
    ]
    assert selected_ids[1] == ChoiceId.for_operator(promoted_report.operator_id)
    category_events = [
        event
        for event in result.run_log.events
        if event.event_type == 'choice_category_selected'
    ]
    assert [event.data['category'] for event in category_events] == [
        OperatorChoiceCategory.COMPOSITION,
        OperatorChoiceCategory.STANDALONE,
    ]
    assert [event.data['selection_probability'] for event in category_events] == [
        0.5,
        0.5,
    ]
    assert [event.data['eligible_choice_count'] for event in category_events] == [
        4,
        3,
    ]
    assert all(event.source_id is None for event in category_events)
    assert all(event.choice_id is None for event in category_events)
    choice_set_events = [
        event
        for event in result.run_log.events
        if event.event_type == 'choice_set_available'
    ]
    assert all(event.source_id is None for event in choice_set_events)
    for category_event, choice_set_event in zip(
        category_events, choice_set_events, strict=True
    ):
        expected_count_key = (
            'eligible_composition_count'
            if category_event.data['category'] is OperatorChoiceCategory.COMPOSITION
            else 'eligible_standalone_count'
        )
        assert (
            category_event.data['eligible_choice_count']
            == choice_set_event.data[expected_count_key]
        )
        assert category_event.data['selection_source'] == 'configured_probability'
    assert result.summary_path is not None
    assert 'Composition selection probability: 0.5' in result.summary_path.read_text()
    assert result.event_log_path is not None
    serialized_events = [
        json.loads(line)
        for line in result.event_log_path.read_text(encoding='utf-8').splitlines()
    ]
    assert [
        event['data']['category']
        for event in serialized_events
        if event['event_type'] == 'choice_category_selected'
    ] == ['composition', 'standalone']
    assert (
        identifier_text(promoted_report.operator_id) in result.summary_path.read_text()
    )


@pytest.mark.parametrize(
    ('probability', 'category', 'choice_kind'),
    [
        (0.0, OperatorChoiceCategory.STANDALONE, ChoiceKind.OPERATOR),
        (1.0, OperatorChoiceCategory.COMPOSITION, ChoiceKind.COMPOSITION),
    ],
)
def test_category_probability_endpoints_skip_the_category_draw(
    tmp_path: Path,
    probability: float,
    category: OperatorChoiceCategory,
    choice_kind: ChoiceKind,
) -> None:
    random_source = SequenceRandomSource((0.5,))
    feedback = RecordingFeedback()
    engine = make_composition_engine(
        CompositionStepOperator('first-step', intermediate=True),
        CompositionStepOperator('second-step', intermediate=False),
        CompositionEvaluator(),
        cast(SelectCompositionFeedback, feedback),
        composition_selection_probability=probability,
        random_source=cast(RandomSource, random_source),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.run_log is not None
    category_event = next(
        event
        for event in result.run_log.events
        if event.event_type == 'choice_category_selected'
    )
    selected_event = next(
        event
        for event in result.run_log.events
        if event.event_type == 'choice_selected'
    )
    assert category_event.data['category'] is category
    assert category_event.data['selection_probability'] == 1.0
    assert category_event.data['selection_source'] == 'configured_probability'
    assert selected_event.choice_id is not None
    assert selected_event.choice_id.kind is choice_kind
    assert random_source.draw_count == 1


def test_promoted_composition_is_restored_from_search_state(tmp_path: Path) -> None:
    first = CompositionStepOperator('first-step', intermediate=True)
    second = CompositionStepOperator('second-step', intermediate=False)
    engine = make_composition_engine(
        first,
        second,
        CompositionEvaluator(),
        cast(SelectCompositionFeedback, SelectPromotedCompositionFeedback()),
        max_candidate_attempts=1,
        composition_selection_probability=0.5,
        random_source=cast(
            RandomSource,
            SequenceRandomSource((0.1, 0.0, 0.9, 0.0)),
        ),
    )

    initial = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )
    assert initial.executive_summary is not None
    promoted_id = initial.executive_summary.promoted_compositions[0].operator_id
    resumed = engine.run(
        checkpoint=Checkpoint(initial.search_state),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert resumed.run_log is not None
    selected = next(
        event.choice_id
        for event in resumed.run_log.events
        if event.event_type == 'choice_selected'
    )
    assert selected == ChoiceId.for_operator(promoted_id)
    assert resumed.executive_summary is not None
    assert resumed.executive_summary.promoted_compositions[0].operator_id == promoted_id
    assert (
        resumed.executive_summary.promoted_compositions[
            0
        ].promoted_in_current_invocation
        is False
    )


def test_promoted_composition_catalog_seeds_a_fresh_search(tmp_path: Path) -> None:
    catalog_path = tmp_path / 'promoted-compositions.json'
    first_engine = make_composition_engine(
        CompositionStepOperator('first-step', intermediate=True),
        CompositionStepOperator('second-step', intermediate=False),
        CompositionEvaluator(),
        cast(SelectCompositionFeedback, SelectPromotedCompositionFeedback()),
        max_candidate_attempts=1,
        composition_selection_probability=1.0,
        random_source=cast(RandomSource, SequenceRandomSource((0.0, 0.0, 0.0))),
        promoted_composition_catalog_path=catalog_path,
    )
    first_run = first_engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert first_run.executive_summary is not None
    promoted_id = first_run.executive_summary.promoted_compositions[0].operator_id
    assert catalog_path.is_file()

    second_engine = make_composition_engine(
        CompositionStepOperator('first-step', intermediate=True),
        CompositionStepOperator('second-step', intermediate=False),
        CompositionEvaluator(),
        cast(SelectCompositionFeedback, SelectPromotedCompositionFeedback()),
        max_candidate_attempts=1,
        composition_selection_probability=0.0,
        random_source=cast(RandomSource, SequenceRandomSource((0.0, 0.0, 0.0))),
        promoted_composition_catalog_path=catalog_path,
    )
    second_run = second_engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert second_run.executive_summary is not None
    loaded_report = second_run.executive_summary.promoted_compositions[0]
    assert loaded_report.operator_id == promoted_id
    assert loaded_report.first_admitted_solution_id is None
    assert loaded_report.promoted_in_current_invocation is False
    assert second_run.run_log is not None
    started_event = next(
        event
        for event in second_run.run_log.events
        if event.event_type == 'invocation_started'
    )
    provenance = cast(RunProvenance, started_event.data['provenance'])
    assert any(
        entry.operator_id == promoted_id and entry.catalog_kind == 'promoted_composite'
        for entry in provenance.operators
    )
    choice_event = next(
        event
        for event in second_run.run_log.events
        if event.event_type == 'choice_selected'
    )
    assert choice_event.choice_id == ChoiceId.for_operator(promoted_id)


def test_promoted_composition_catalog_rejects_changed_operator_configuration(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / 'promoted-compositions.json'
    first_engine = make_composition_engine(
        CompositionStepOperator('first-step', intermediate=True),
        CompositionStepOperator('second-step', intermediate=False),
        CompositionEvaluator(),
        cast(SelectCompositionFeedback, SelectPromotedCompositionFeedback()),
        max_candidate_attempts=1,
        composition_selection_probability=1.0,
        random_source=cast(RandomSource, SequenceRandomSource((0.0, 0.0, 0.0))),
        promoted_composition_catalog_path=catalog_path,
    )
    first_engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )
    changed_engine = make_composition_engine(
        CompositionStepOperator('first-step', intermediate=True),
        CompositionStepOperator('second-step', intermediate=False),
        CompositionEvaluator(),
        cast(SelectCompositionFeedback, SelectPromotedCompositionFeedback()),
        promoted_composition_catalog_path=catalog_path,
        operator_configuration_ids=('first-step-v2', 'second-step-v1'),
    )

    with pytest.raises(ComponentSetupError) as error:
        changed_engine.run(
            initial_solutions=((5, 5),),
            log_directory=tmp_path,
            progress_report_interval_seconds=60,
        )
    assert error.value.failure.phase == 'promotion_catalog_load'


def test_promotion_catalog_write_failure_keeps_committed_promotion(
    tmp_path: Path,
) -> None:
    blocking_file = tmp_path / 'not-a-directory'
    blocking_file.write_text('block', encoding='utf-8')
    catalog_path = blocking_file / 'promoted-compositions.json'
    engine = make_composition_engine(
        CompositionStepOperator('first-step', intermediate=True),
        CompositionStepOperator('second-step', intermediate=False),
        CompositionEvaluator(),
        cast(SelectCompositionFeedback, SelectPromotedCompositionFeedback()),
        max_candidate_attempts=1,
        composition_selection_probability=1.0,
        random_source=cast(RandomSource, SequenceRandomSource((0.0, 0.0, 0.0))),
        promoted_composition_catalog_path=catalog_path,
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.COMPONENT_FAILURE
    assert result.search_state is not None
    assert result.executive_summary is not None
    assert len(result.executive_summary.promoted_compositions) == 1
    assert any(
        failure.phase == 'promotion_catalog_save' for failure in result.diagnostics
    )


def test_promoted_operator_can_be_composed_again(tmp_path: Path) -> None:
    first = CompositionStepOperator('first-step', intermediate=True)
    second = CompositionStepOperator('second-step', intermediate=False)
    engine = make_composition_engine(
        first,
        second,
        CompositionEvaluator(),
        cast(
            SelectCompositionFeedback,
            SelectCompositionUsingPromotedFeedback(),
        ),
        max_candidate_attempts=2,
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.run_log is not None
    selected = [
        event.choice_id
        for event in result.run_log.events
        if event.event_type == 'choice_selected'
    ]
    assert selected[0] == ChoiceId.for_composition(
        CompositionId('first-step', 'second-step')
    )
    assert selected[1] is not None
    assert selected[1].kind.value == 'composition'
    promotion = next(
        event
        for event in result.run_log.events
        if event.event_type == 'composition_promoted'
    )
    promoted_id = cast(OperatorId, promotion.data['generated_operator_id'])
    promoted_composition = cast(CompositionId, selected[1].composition_id)
    assert promoted_id in (
        promoted_composition.first_operator_id,
        promoted_composition.second_operator_id,
    )


@pytest.mark.parametrize('zero_position', ('first', 'second'))
def test_composition_zero_modification_submits_no_candidate(
    tmp_path: Path, zero_position: str
) -> None:
    first = CompositionStepOperator(
        'first-step',
        intermediate=True,
        modification_count=int(zero_position != 'first'),
    )
    second = CompositionStepOperator(
        'second-step',
        intermediate=False,
        modification_count=int(zero_position != 'second'),
    )
    evaluator = CompositionEvaluator()
    feedback = SelectCompositionFeedback('first-step', 'second-step')
    engine = make_composition_engine(first, second, evaluator, feedback)

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.run_log is not None
    assert len(result.candidate_outcomes) == 1
    assert evaluator.evaluated_solutions == [(5, 5)]
    composition_event = next(
        event
        for event in result.run_log.events
        if event.event_type == 'composition_result'
    )
    assert composition_event.data['final_candidate_count'] == 0
    assert composition_event.data['final_modification_counts'] == ()
    assert composition_event.data['first_candidate_count'] == (
        0 if zero_position == 'first' else 1
    )
    assert composition_event.data['second_call_count'] == (
        0 if zero_position == 'first' else 1
    )
    assert len(feedback.observations) == 1
    observation = cast(FeedbackObservation, feedback.observations[0])
    assert observation.modification_counts == ()
    assert observation.outcomes == ()
    assert len(second.calls) == (0 if zero_position == 'first' else 1)


def test_constructor_rejects_operator_group_log_id_collision() -> None:
    @dataclass(frozen=True)
    class CollidingGroupId:
        value: str

        def to_log_text(self) -> str:
            return 'same-group-id'

    with pytest.raises(ComponentRegistrationError, match='OperatorGroup IDs'):
        make_composition_engine(
            CompositionStepOperator('first-step', intermediate=True),
            CompositionStepOperator('second-step', intermediate=False),
            CompositionEvaluator(),
            SelectCompositionFeedback('first-step', 'second-step'),
            operator_groups=(
                OperatorGroup(
                    CollidingGroupId('first'),
                    frozenset(('first-step', 'second-step')),
                ),
                OperatorGroup(
                    CollidingGroupId('second'),
                    frozenset(('first-step', 'second-step')),
                ),
            ),
        )


def test_component_failure_disables_composition_but_keeps_operator_available(
    tmp_path: Path,
) -> None:
    first = CompositionStepOperator('first-step', intermediate=True)
    second = CompositionStepOperator(
        'second-step', intermediate=False, fail_on_internal=True
    )
    feedback = SelectCompositionFeedback(
        'first-step', 'second-step', fallback_operator_id='first-step'
    )
    engine = make_composition_engine(
        first,
        second,
        CompositionEvaluator(),
        feedback,
        max_candidate_attempts=2,
        composition_selection_probability=0.5,
        random_source=cast(RandomSource, SequenceRandomSource((0.1, 0.0, 0.9, 0.0))),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.run_log is not None
    selected = [
        event.choice_id
        for event in result.run_log.events
        if event.event_type == 'choice_selected'
    ]
    assert selected == [feedback.choice_id, ChoiceId.for_operator('first-step')]
    assert len(first.calls) == 2
    assert len(second.calls) == 1
    assert result.candidate_outcomes[-1].choice_id == ChoiceId.for_operator(
        'first-step'
    )
    assert any(
        cast(ComponentFailure, event.data['failure']).source == 'second-step'
        for event in result.run_log.events
        if event.event_type == 'component_failure'
    )
    assert feedback.choice_id not in selected[1:]


def make_engine(
    *,
    candidate_mode: str,
    max_neighborhood_size: int,
    max_candidate_attempts: int,
    solution_identity: SolutionIdentity | None = None,
    objective_declaration: ObjectiveDeclaration | None = None,
    checkpoint_store: CheckpointStore | None = None,
    solution_formatter: SolutionFormatter | None = None,
    feedback_policy: FeedbackPolicy | None = None,
    base_selection_policy: BaseSelectionPolicy | None = None,
    solution_codec: SolutionCodec | None = None,
    composition_selection_probability: float = 0.0,
    promoted_composition_catalog_path: Path | None = None,
    application_evaluator: ApplicationEvaluator | None = None,
) -> tuple[MultiobjectiveVNS, CandidateOperator]:
    operator = CandidateOperator(candidate_mode)
    engine = MultiobjectiveVNS(
        solution_codec=solution_codec or cast(SolutionCodec, Codec()),
        solution_identity=solution_identity or Identity(),
        objective_declaration=objective_declaration
        or ObjectiveDeclaration((Objective('cost_a'), Objective('cost_b'))),
        operators=(
            OperatorRegistration(
                operator_id='candidate-operator',
                configuration_id='candidate-operator-v1',
                operator=operator,
                initial_state=None,
                max_neighborhood_size=max_neighborhood_size,
                feasibility_restoration=False,
                modification_count_definition='number of requested size units realized',
            ),
        ),
        application_evaluator=application_evaluator
        or cast(ApplicationEvaluator, Evaluator()),
        problem_instance_id=cast(ProblemInstanceId, 'test-problem-v1'),
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            max_neighborhood_size=max_neighborhood_size,
            composition_selection_probability=composition_selection_probability,
            budget_limits=BudgetLimits(
                max_wall_time_seconds=60,
                max_candidate_attempts=max_candidate_attempts,
                max_evaluation_requests=max_candidate_attempts,
            ),
        ),
        checkpoint_store=checkpoint_store,
        solution_formatter=solution_formatter,
        feedback_policy=feedback_policy,
        base_selection_policy=base_selection_policy,
        promoted_composition_catalog_path=promoted_composition_catalog_path,
    )
    return engine, operator


def test_default_base_selection_uses_only_pareto_members_after_no_progress(
    tmp_path: Path,
) -> None:
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=2,
    )

    result = engine.run(
        initial_solutions=((1, 1), (2, 2)),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    run_log = result.run_log
    assert run_log is not None
    base_events = [
        event for event in run_log.events if event.event_type == 'base_selected'
    ]
    assert [event.solution_id for event in base_events] == [
        SolutionId(0),
        SolutionId(0),
    ]
    assert [event.data['eligible_base_count'] for event in base_events] == [1, 1]


def test_default_base_selection_can_choose_another_pareto_base(
    tmp_path: Path,
) -> None:
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=2,
    )

    result = engine.run(
        initial_solutions=((1, 4), (4, 1)),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    run_log = result.run_log
    assert run_log is not None
    base_events = [
        event for event in run_log.events if event.event_type == 'base_selected'
    ]
    assert len(base_events) == 2
    assert base_events[0].solution_id != base_events[1].solution_id
    assert [event.data['eligible_base_count'] for event in base_events] == [2, 2]


def test_custom_base_selection_is_limited_to_pareto_archive(
    tmp_path: Path,
) -> None:
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=2,
        base_selection_policy=cast(BaseSelectionPolicy, PreferDominatedBasePolicy()),
    )

    result = engine.run(
        initial_solutions=((1, 1), (2, 2)),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    run_log = result.run_log
    assert run_log is not None
    base_events = [
        event for event in run_log.events if event.event_type == 'base_selected'
    ]
    assert [event.solution_id for event in base_events] == [SolutionId(0)] * 2
    assert [event.data['eligible_base_count'] for event in base_events] == [1, 1]


def test_run_processes_initial_candidate_and_pareto_admission(
    tmp_path: Path,
) -> None:
    engine, operator = make_engine(
        candidate_mode='improve',
        max_neighborhood_size=2,
        max_candidate_attempts=1,
        composition_selection_probability=1.0,
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert result.counters.candidate_attempts == 1
    assert result.counters.evaluation_requests == 1
    assert operator.sizes == [1, 1]
    assert len(operator.base_evaluations) == 2
    first_base_evaluation = operator.base_evaluations[0]
    assert first_base_evaluation is not None
    assert first_base_evaluation.application_evaluation is not None
    assert first_base_evaluation.application_evaluation.objective_values == (5, 5)
    assert [record.solution for record in result.search_state.pareto_solutions()] == [
        (5, 5),
        (3, 7),
    ]
    assert [outcome.assessment for outcome in result.candidate_outcomes] == [
        ApplicationAssessment.VALID,
        ApplicationAssessment.VALID,
    ]
    assert result.candidate_outcomes[1].disposition is SearchDisposition.ARCHIVED
    assert result.run_log is not None
    assert result.executive_summary is not None
    component_roles = {
        entry.component_role for entry in result.executive_summary.provenance.components
    }
    assert ComponentRole.RUN_LOGGER in component_roles
    operator_component = next(
        entry
        for entry in result.executive_summary.provenance.components
        if entry.component_role is ComponentRole.OPERATOR
    )
    assert operator_component.configuration_id == 'candidate-operator-v1'
    assert (
        result.executive_summary.provenance.operators[0].modification_count_definition
        == 'number of requested size units realized'
    )
    assert result.executive_summary.initial_pareto_count == 1
    assert len(result.executive_summary.final_pareto_solutions) == 2
    assert result.event_log_path is not None and result.event_log_path.is_file()
    assert result.summary_path is not None and result.summary_path.is_file()
    assert result.registry_view_path is not None and result.registry_view_path.is_file()
    assert result.pareto_view_path is not None and result.pareto_view_path.is_file()
    registry_view = json.loads(result.registry_view_path.read_text(encoding='utf-8'))
    pareto_view = json.loads(result.pareto_view_path.read_text(encoding='utf-8'))
    assert registry_view['record_scope'] == 'registry'
    assert pareto_view['record_scope'] == 'pareto'
    assert registry_view['is_final'] is True
    assert pareto_view['is_final'] is True
    assert len(registry_view['solutions']) == 2
    assert len(pareto_view['solutions']) == 2
    event_records = [
        json.loads(line)
        for line in result.event_log_path.read_text(encoding='utf-8').splitlines()
    ]
    assert event_records[0]['event_type'] == 'invocation_started'
    assert (
        event_records[0]['data']['config']['composition_selection_probability'] == 1.0
    )
    assert event_records[-1]['event_type'] == 'invocation_stopped'
    summary_text = result.summary_path.read_text(encoding='utf-8')
    assert 'Invocation kind: search' in summary_text
    assert 'candidate-operator-v1' in summary_text
    assert (
        result.executive_summary.final_pareto_solutions[0].solution_id.to_log_text()
        in summary_text
    )
    assert 'Assessment counts:' in summary_text
    assert 'Disposition counts:' in summary_text
    assert 'Component failures:' in summary_text


def test_distinct_plans_with_equal_objectives_stay_in_registry_not_pareto(
    tmp_path: Path,
) -> None:
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        application_evaluator=cast(ApplicationEvaluator, TiedObjectiveEvaluator()),
    )

    with pytest.warns(UserWarning, match='search symmetry'):
        result = engine.run(
            initial_solutions=((1, 2), (2, 1)),
            log_directory=tmp_path,
            progress_report_interval_seconds=60,
        )

    registry = result.search_state.visited_solutions()
    pareto = result.search_state.pareto_solutions()
    assert [record.solution for record in registry] == [(1, 2), (2, 1)]
    assert [record.solution for record in pareto] == [(1, 2)]
    assert registry[0].archive_status is ArchiveStatus.PARETO_OPTIMAL
    assert registry[1].archive_status is ArchiveStatus.OBJECTIVE_VECTOR_DUPLICATE
    assert result.candidate_outcomes[1].disposition is (
        SearchDisposition.OBJECTIVE_VECTOR_DUPLICATE
    )
    assert result.executive_summary is not None
    assert result.executive_summary.objective_vector_duplicate_count == 1

    assert result.registry_view_path is not None
    assert result.pareto_view_path is not None
    registry_view = json.loads(result.registry_view_path.read_text(encoding='utf-8'))
    pareto_view = json.loads(result.pareto_view_path.read_text(encoding='utf-8'))
    assert [record['solution_id'] for record in registry_view['solutions']] == [
        'solution:0',
        'solution:1',
    ]
    assert [record['solution_id'] for record in pareto_view['solutions']] == [
        'solution:0',
    ]
    assert registry_view['solutions'][1]['archive_status'] == (
        'objective_vector_duplicate'
    )
    assert result.summary_path is not None
    assert 'WARNING:' in result.summary_path.read_text(encoding='utf-8')
    assert 'search symmetry' in result.summary_path.read_text(encoding='utf-8')


def test_choice_sizes_advance_independently_and_reset_on_pareto_admission(
    tmp_path: Path,
) -> None:
    first = CursorOperator('cursor-first')
    second = CursorOperator('cursor-second', admit_on_call=2)
    registrations = tuple(
        OperatorRegistration(
            operator_id=operator.component_id,
            configuration_id=operator.configuration_id,
            operator=cast(Operator, operator),
            initial_state=None,
            max_neighborhood_size=2,
            feasibility_restoration=False,
            modification_count_definition='requested size units',
        )
        for operator in (first, second)
    )
    engine = MultiobjectiveVNS(
        solution_codec=cast(SolutionCodec, Codec()),
        solution_identity=Identity(),
        objective_declaration=ObjectiveDeclaration(
            (Objective('cursor-cost-a'), Objective('cursor-cost-b'))
        ),
        operators=registrations,
        application_evaluator=cast(ApplicationEvaluator, Evaluator()),
        problem_instance_id=cast(ProblemInstanceId, 'cursor-test-problem'),
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            max_neighborhood_size=2,
            composition_selection_probability=0.0,
            feedback_selection_mode='uniform',
            budget_limits=BudgetLimits(
                max_wall_time_seconds=60,
                max_candidate_attempts=5,
                max_evaluation_requests=5,
            ),
        ),
        random_source=cast(
            RandomSource, SequenceRandomSource((0.1, 0.1, 0.9, 0.9, 0.1))
        ),
    )

    result = engine.run(
        initial_solutions=((10, 10),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert first.sizes == [1, 2, 1]
    assert second.sizes == [1, 2]
    assert result.run_log is not None
    selected = [
        event
        for event in result.run_log.events
        if event.event_type == 'choice_selected'
    ]
    assert [
        (cast(ChoiceId, event.choice_id).operator_id, event.data['k'])
        for event in selected
    ] == [
        ('cursor-first', 1),
        ('cursor-first', 2),
        ('cursor-second', 1),
        ('cursor-second', 2),
        ('cursor-first', 1),
    ]
    choice_sets = [
        event
        for event in result.run_log.events
        if event.event_type == 'choice_set_available'
    ]
    observed_ids = [
        descriptor.choice_id
        for event in choice_sets
        for descriptor in cast(
            tuple[ChoiceDescriptor, ...],
            event.data['newly_eligible_choices'],
        )
    ]
    assert len(observed_ids) == len(set(observed_ids))
    assert choice_sets[0].data['eligible_choice_count'] == len(observed_ids)
    assert all(not event.data['newly_eligible_choices'] for event in choice_sets[1:])
    assert all(event.schema_version == 4 for event in choice_sets)


def test_attempt_processes_every_ordered_candidate_after_archive_admission(
    tmp_path: Path,
) -> None:
    operator = CandidateListOperator('candidate-list', candidates=True)
    engine = MultiobjectiveVNS(
        solution_codec=cast(SolutionCodec, Codec()),
        solution_identity=Identity(),
        objective_declaration=ObjectiveDeclaration(
            (Objective('candidate-list-a'), Objective('candidate-list-b'))
        ),
        operators=(
            OperatorRegistration(
                operator_id=operator.component_id,
                configuration_id=operator.configuration_id,
                operator=cast(Operator, operator),
                initial_state=None,
                max_neighborhood_size=1,
                feasibility_restoration=False,
                modification_count_definition='one relocation',
            ),
        ),
        application_evaluator=cast(ApplicationEvaluator, Evaluator()),
        problem_instance_id=cast(ProblemInstanceId, 'candidate-list-problem'),
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=2,
            max_neighborhood_size=1,
            composition_selection_probability=0.0,
            feedback_selection_mode='uniform',
            budget_limits=BudgetLimits(
                max_wall_time_seconds=60,
                max_candidate_attempts=1,
                max_evaluation_requests=4,
            ),
        ),
        random_source=cast(RandomSource, SequenceRandomSource((0.1, 0.1))),
    )

    result = engine.run(
        initial_solutions=((10, 10),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert len(result.candidate_outcomes) == 3
    assert [outcome.candidate_index for outcome in result.candidate_outcomes] == [
        None,
        1,
        2,
    ]
    assert [outcome.disposition for outcome in result.candidate_outcomes[1:]] == [
        SearchDisposition.ARCHIVED,
        SearchDisposition.ARCHIVED,
    ]
    assert [record.solution for record in result.search_state.pareto_solutions()] == [
        (10, 10),
        (9, 12),
        (8, 13),
    ]
    assert operator.calls == [((10, 10), 1, 2)]
    assert result.run_log is not None
    feedback = next(
        event
        for event in result.run_log.events
        if event.event_type == 'feedback_observed'
    )
    assert feedback.data['archive_admission_count'] == 2
    assert feedback.data['attempt_admitted'] is True


def test_size_cursor_is_independent_for_each_pareto_base(tmp_path: Path) -> None:
    operator = CandidateListOperator('base-cursor', candidates=False)
    engine = MultiobjectiveVNS(
        solution_codec=cast(SolutionCodec, Codec()),
        solution_identity=Identity(),
        objective_declaration=ObjectiveDeclaration(
            (Objective('base-cursor-a'), Objective('base-cursor-b'))
        ),
        operators=(
            OperatorRegistration(
                operator_id=operator.component_id,
                configuration_id=operator.configuration_id,
                operator=cast(Operator, operator),
                initial_state=None,
                max_neighborhood_size=3,
                feasibility_restoration=False,
                modification_count_definition='one move',
            ),
        ),
        application_evaluator=cast(ApplicationEvaluator, Evaluator()),
        problem_instance_id=cast(ProblemInstanceId, 'base-cursor-problem'),
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            max_neighborhood_size=3,
            composition_selection_probability=0.0,
            feedback_selection_mode='uniform',
            budget_limits=BudgetLimits(
                max_wall_time_seconds=60,
                max_candidate_attempts=2,
                max_evaluation_requests=4,
            ),
        ),
        random_source=cast(RandomSource, SequenceRandomSource((0.1, 0.1, 0.1))),
    )

    engine.run(
        initial_solutions=((10, 0), (0, 10)),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert [size for _, size, _ in operator.calls] == [1, 1]
    assert len({base for base, _, _ in operator.calls}) == 2


def test_choice_size_cursor_is_restored_from_checkpoint(tmp_path: Path) -> None:
    operator = CursorOperator('checkpoint-cursor')
    store = RecordingCheckpointStore()
    engine = MultiobjectiveVNS(
        solution_codec=cast(SolutionCodec, Codec()),
        solution_identity=Identity(),
        objective_declaration=ObjectiveDeclaration(
            (Objective('checkpoint-cursor-a'), Objective('checkpoint-cursor-b'))
        ),
        operators=(
            OperatorRegistration(
                operator_id='checkpoint-cursor',
                configuration_id='checkpoint-cursor-v1',
                operator=cast(Operator, operator),
                initial_state=None,
                max_neighborhood_size=3,
                feasibility_restoration=False,
                modification_count_definition='requested size units',
            ),
        ),
        application_evaluator=cast(ApplicationEvaluator, Evaluator()),
        problem_instance_id=cast(ProblemInstanceId, 'checkpoint-cursor-problem'),
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            max_neighborhood_size=3,
            composition_selection_probability=0.0,
            feedback_selection_mode='uniform',
            budget_limits=BudgetLimits(
                max_wall_time_seconds=60,
                max_candidate_attempts=1,
                max_evaluation_requests=1,
            ),
        ),
        checkpoint_store=cast(CheckpointStore, store),
    )

    first = engine.run(
        initial_solutions=((10, 10),),
        checkpoint_interval=1,
        log_directory=tmp_path / 'first',
        progress_report_interval_seconds=60,
    )
    resumed = engine.run(
        checkpoint=first.checkpoint_reference,
        checkpoint_interval=1,
        log_directory=tmp_path / 'resumed',
        progress_report_interval_seconds=60,
    )

    assert operator.sizes == [1, 2]
    assert resumed.run_log is not None
    selected = next(
        event
        for event in resumed.run_log.events
        if event.event_type == 'choice_selected'
    )
    assert selected.data['k'] == 2


def test_checkpoint_interval_saves_after_initialization_and_attempt_commits(
    tmp_path: Path,
) -> None:
    store = RecordingCheckpointStore()
    engine, _ = make_engine(
        candidate_mode='improve',
        max_neighborhood_size=2,
        max_candidate_attempts=1,
        checkpoint_store=cast(CheckpointStore, store),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        checkpoint_interval=1,
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert [
        getattr(checkpoint.search_state, 'commit_sequence')
        for checkpoint in store.checkpoints
    ] == [1, 2]
    assert result.checkpoint_reference == 'checkpoint-2'


def test_resume_rejects_changed_composition_selection_probability(
    tmp_path: Path,
) -> None:
    original_engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        composition_selection_probability=0.0,
    )
    initial = original_engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )
    changed_engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        composition_selection_probability=0.5,
    )

    with pytest.raises(ResumeCompatibilityError, match='compatibility'):
        changed_engine.run(
            checkpoint=Checkpoint(initial.search_state),
            log_directory=tmp_path,
            progress_report_interval_seconds=60,
        )


def test_codec_capture_failure_is_attributed_to_codec_and_keeps_search_state(
    tmp_path: Path,
) -> None:
    store = RecordingCheckpointStore()
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        checkpoint_store=cast(CheckpointStore, store),
        solution_codec=cast(SolutionCodec, FailingCaptureCodec()),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.COMPONENT_FAILURE
    assert result.search_state.visited_solutions()
    assert result.checkpoint_reference is None
    assert store.checkpoints == []
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].source == 'failing-capture-codec'
    assert result.diagnostics[0].phase == 'pareto_snapshot_save'


def test_checkpoint_capture_failure_remains_separate_from_pareto_snapshot(
    tmp_path: Path,
) -> None:
    store = RecordingCheckpointStore()
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        checkpoint_store=cast(CheckpointStore, store),
        solution_codec=cast(SolutionCodec, FailingSecondCaptureCodec()),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        checkpoint_interval=1,
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.COMPONENT_FAILURE
    assert result.pareto_snapshot_path is not None
    assert result.diagnostics[0].phase == 'checkpoint_save'
    assert store.checkpoints == []


def test_checkpoint_store_failure_is_logged_after_committed_search_state(
    tmp_path: Path,
) -> None:
    store = FailingSaveStore()
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        checkpoint_store=cast(CheckpointStore, store),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.COMPONENT_FAILURE
    assert result.search_state.visited_solutions()
    assert result.checkpoint_reference is None
    assert any(
        failure.source == 'failing-checkpoint-store'
        and failure.phase == 'checkpoint_save'
        for failure in result.diagnostics
    )
    assert result.run_log is not None
    assert any(
        event.event_type == 'component_failure' for event in result.run_log.events
    )


def test_run_resumes_from_stored_checkpoint_as_a_new_invocation(
    tmp_path: Path,
) -> None:
    store = RecordingCheckpointStore()
    engine, operator = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        checkpoint_store=cast(CheckpointStore, store),
    )
    first = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    resumed = engine.run(
        checkpoint=first.checkpoint_reference,
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert first.checkpoint_reference == 'checkpoint-1'
    assert resumed.checkpoint_reference == 'checkpoint-2'
    assert resumed.counters.candidate_attempts == 1
    assert resumed.run_log is not None
    assert resumed.run_log.events[0].data['mode'] == 'resume'
    assert len(resumed.search_state.visited_solutions()) == 1
    assert len(operator.sizes) >= 2
    assert set(operator.sizes) == {1}


def test_maintenance_cleans_to_pareto_and_saves_new_checkpoint(
    tmp_path: Path,
) -> None:
    store = RecordingCheckpointStore()
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        checkpoint_store=cast(CheckpointStore, store),
    )
    original_result = engine.run(
        initial_solutions=((1, 1), (2, 2)),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )
    original_state = original_result.search_state
    original_active = original_state.active_solutions()
    original_sequence = getattr(original_state, 'commit_sequence')
    assert len(original_active) == 2
    assert len(original_state.pareto_solutions()) == 1

    result = engine.maintain(
        state=original_result.checkpoint_reference,
        clean_active_pool=True,
        log_directory=tmp_path,
    )

    assert isinstance(result, MaintenanceResult)
    assert result.termination_reason is TerminationReason.MAINTENANCE_COMPLETED
    assert len(result.checkpoint.search_state.active_solutions()) == 1
    assert len(result.checkpoint.search_state.visited_solutions()) == 2
    assert getattr(result.checkpoint.search_state, 'commit_sequence') == (
        original_sequence + 1
    )
    assert original_state.active_solutions() == original_active
    assert result.checkpoint_reference == 'checkpoint-2'
    assert store.checkpoints[-1].search_state.active_solutions() == (
        result.checkpoint.search_state.active_solutions()
    )
    assert result.run_log is not None
    assert result.executive_summary is not None
    assert result.executive_summary.invocation_kind == 'maintenance'
    assert result.executive_summary.initial_pareto_count == 1
    assert len(result.executive_summary.final_pareto_solutions) == 1
    assert result.event_log_path is not None and result.event_log_path.is_file()
    assert result.summary_path is not None and result.summary_path.is_file()
    assert 'Invocation kind: maintenance' in result.summary_path.read_text(
        encoding='utf-8'
    )
    assert [event.event_type for event in result.run_log.events] == [
        'invocation_started',
        'maintenance_requested',
        'active_pool_changed',
        'checkpoint_saved',
        'invocation_stopped',
    ]


def test_maintenance_feedback_reset_is_committed_and_logged(
    tmp_path: Path,
) -> None:
    feedback = RecordingFeedback()
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        feedback_policy=cast(FeedbackPolicy, feedback),
    )
    search = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )
    assert len(feedback.observations) == 1

    result = engine.maintain(
        state=search.search_state,
        reset_feedback=True,
        log_directory=tmp_path,
    )

    assert feedback.observations == []
    assert feedback.reset_count == 1
    assert (
        getattr(result.checkpoint.search_state, 'commit_sequence')
        == getattr(search.search_state, 'commit_sequence') + 1
    )
    assert result.run_log is not None
    reset_events = [
        event for event in result.run_log.events if event.event_type == 'feedback_reset'
    ]
    assert len(reset_events) == 1
    reset_choice_ids = cast(
        tuple[ChoiceId, ...], reset_events[0].data['reset_choice_ids']
    )
    assert ChoiceId.for_operator('candidate-operator') in reset_choice_ids
    assert all(choice.kind is ChoiceKind.OPERATOR for choice in reset_choice_ids)


def test_feedback_reset_reports_compositions_tracked_before_checkpoint_resume(
    tmp_path: Path,
) -> None:
    first = CompositionStepOperator('first-step', intermediate=True)
    second = CompositionStepOperator('second-step', intermediate=False)
    feedback = SelectCompositionFeedback('first-step', 'second-step')
    engine = make_composition_engine(
        first,
        second,
        CompositionEvaluator(),
        feedback,
    )
    search = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    result = engine.maintain(
        state=search.search_state,
        reset_feedback=True,
        log_directory=tmp_path,
    )

    assert result.run_log is not None
    reset_event = next(
        event for event in result.run_log.events if event.event_type == 'feedback_reset'
    )
    reset_choice_ids = cast(tuple[ChoiceId, ...], reset_event.data['reset_choice_ids'])
    assert search.executive_summary is not None
    assert set(reset_choice_ids) == {
        ChoiceId.for_operator('first-step'),
        ChoiceId.for_operator('second-step'),
        ChoiceId.for_operator(
            search.executive_summary.promoted_compositions[0].operator_id
        ),
        feedback.choice_id,
    }


def test_run_advances_size_after_duplicate_full_modifications(tmp_path: Path) -> None:
    operator = CandidateOperator('duplicate', 'duplicate-operator')

    result = multiobjective_vns(
        # Exercise the convenience entry point over the same complete path.
        solution_codec=cast(SolutionCodec, Codec()),
        solution_identity=Identity(),
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), Objective('cost_b'))
        ),
        operators=(
            OperatorRegistration(
                operator_id='duplicate-operator',
                configuration_id='duplicate-operator-v1',
                operator=operator,
                initial_state=None,
                max_neighborhood_size=2,
                feasibility_restoration=False,
                modification_count_definition='number of requested size units realized',
            ),
        ),
        application_evaluator=cast(ApplicationEvaluator, Evaluator()),
        problem_instance_id=cast(ProblemInstanceId, 'test-problem-v1'),
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            max_neighborhood_size=2,
            composition_selection_probability=1.0,
            budget_limits=BudgetLimits(
                max_wall_time_seconds=60,
                max_candidate_attempts=2,
                max_evaluation_requests=2,
            ),
        ),
        promoted_composition_catalog_path=None,
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert operator.sizes == [1, 1, 2, 2]
    assert result.counters.candidate_attempts == 2
    assert result.counters.evaluation_requests == 0
    assert len(result.search_state.visited_solutions()) == 1
    assert [outcome.disposition for outcome in result.candidate_outcomes[1:]] == [
        SearchDisposition.DUPLICATE,
        SearchDisposition.DUPLICATE,
    ]
    assert result.executive_summary is not None
    composition_statistics = tuple(
        item
        for item in result.executive_summary.choice_statistics
        if item.choice_id.kind.value == 'composition'
    )
    assert [item.k for item in composition_statistics] == [1, 2]
    assert [item.attempt_count for item in composition_statistics] == [1, 1]
    assert any(
        item.choice_id.kind.value == 'operator' and item.attempt_count == 0
        for item in result.executive_summary.choice_statistics
    )


def test_constructor_rejects_relaxed_search_without_restoration_choice() -> None:
    with pytest.raises(ValueError, match='restoration'):
        MultiobjectiveVNS(
            solution_codec=cast(SolutionCodec, Codec()),
            solution_identity=Identity(),
            objective_declaration=ObjectiveDeclaration(
                (Objective('one'), Objective('two'))
            ),
            operators=(
                OperatorRegistration(
                    operator_id='ordinary',
                    configuration_id='ordinary-v1',
                    operator=CandidateOperator('improve'),
                    initial_state=None,
                    max_neighborhood_size=1,
                    feasibility_restoration=False,
                    modification_count_definition='one',
                ),
            ),
            application_evaluator=cast(ApplicationEvaluator, Evaluator()),
            problem_instance_id=cast(ProblemInstanceId, 'test-problem-v1'),
            config=MultiobjectiveVNSConfig(
                max_candidates_per_attempt=1,
                feasibility_mode='relaxed',
                max_infeasible_steps=2,
            ),
        )


def test_constructor_rejects_component_id_collision_with_formatter() -> None:
    with pytest.raises(ComponentRegistrationError, match='component IDs'):
        make_engine(
            candidate_mode='improve',
            max_neighborhood_size=1,
            max_candidate_attempts=1,
            solution_formatter=cast(SolutionFormatter, DuplicateIdFormatter()),
        )


def test_identity_failure_on_one_initial_solution_does_not_stop_initialization(
    tmp_path: Path,
) -> None:
    class IntermittentIdentity(Identity):
        def lookup_key(self, solution: object) -> object:
            if solution == 'unresolvable':
                raise RuntimeError('lookup failed for this value')
            return solution

        def equivalent(self, left: object, right: object) -> bool:
            return left == right

    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        solution_identity=IntermittentIdentity(),
    )

    result = engine.run(
        initial_solutions=('unresolvable', (5, 5)),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.candidate_outcomes[0].processing_status is ProcessingStatus.FAILED
    assert result.candidate_outcomes[1].assessment is ApplicationAssessment.VALID
    assert result.search_state.pareto_solutions()[0].solution == (5, 5)


def test_malformed_operator_result_is_a_contract_failure_and_disables_operator(
    tmp_path: Path,
) -> None:
    class MalformedOperator:
        def call(self, context: CandidateContext) -> object:
            del context
            return object()

    engine = MultiobjectiveVNS(
        solution_codec=cast(SolutionCodec, Codec()),
        solution_identity=Identity(),
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), Objective('cost_b'))
        ),
        operators=(
            OperatorRegistration(
                operator_id='malformed-operator',
                configuration_id='malformed-operator-v1',
                operator=cast(Operator, MalformedOperator()),
                initial_state=None,
                max_neighborhood_size=1,
                feasibility_restoration=False,
                modification_count_definition='one',
            ),
        ),
        application_evaluator=cast(ApplicationEvaluator, Evaluator()),
        problem_instance_id=cast(ProblemInstanceId, 'test-problem-v1'),
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            max_neighborhood_size=1,
            composition_selection_probability=0.0,
            random_seed=1,
            budget_limits=BudgetLimits(
                max_wall_time_seconds=60,
                max_candidate_attempts=3,
                max_evaluation_requests=3,
            ),
        ),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.NO_AVAILABLE_OPERATOR
    assert result.counters.candidate_attempts == 1
    assert len(result.candidate_outcomes) == 1
    assert result.run_log is not None
    failures = tuple(
        cast(ComponentFailure, failure)
        for event in result.run_log.events
        if event.event_type == 'component_failure'
        for failure in (event.data['failure'],)
    )
    assert len(failures) == 1
    assert failures[0].kind is FailureKind.CONTRACT_VIOLATION


def test_random_source_failure_during_operator_call_commits_failed_attempt(
    tmp_path: Path,
) -> None:
    class FailsInsideOperatorRandomSource:
        component_id = 'test-random-source'
        configuration_id = 'test-random-source-v1'

        def __init__(self) -> None:
            self.draw_count = 0

        def random(self) -> float:
            self.draw_count += 1
            return 0.1 if self.draw_count == 1 else float('nan')

        def capture_state(self) -> object:
            return self.draw_count

        def restore_state(self, payload: object) -> None:
            self.draw_count = cast(int, payload)

    class RandomOperator:
        def call(self, context: CandidateContext) -> OperatorResult:
            context.random_source.random()
            return OperatorResult(
                candidates=(CandidateProposal(Candidate((4, 6)), context.k),)
            )

    engine = MultiobjectiveVNS(
        solution_codec=cast(SolutionCodec, Codec()),
        solution_identity=Identity(),
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), Objective('cost_b'))
        ),
        operators=(
            OperatorRegistration(
                operator_id='random-operator',
                configuration_id='random-operator-v1',
                operator=cast(Operator, RandomOperator()),
                initial_state=None,
                max_neighborhood_size=1,
                feasibility_restoration=False,
                modification_count_definition='one',
            ),
        ),
        application_evaluator=cast(ApplicationEvaluator, Evaluator()),
        problem_instance_id=cast(ProblemInstanceId, 'test-problem-v1'),
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            max_neighborhood_size=1,
            composition_selection_probability=0.0,
            budget_limits=BudgetLimits(
                max_wall_time_seconds=60,
                max_candidate_attempts=3,
                max_evaluation_requests=3,
            ),
        ),
        random_source=cast(RandomSource, FailsInsideOperatorRandomSource()),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.COMPONENT_FAILURE
    assert result.counters.candidate_attempts == 1
    assert result.counters.evaluation_requests == 0
    assert len(result.candidate_outcomes) == 1
    assert result.search_state.pareto_solutions()[0].solution == (5, 5)
    assert result.run_log is not None
    feedback_events = tuple(
        event
        for event in result.run_log.events
        if event.event_type == 'feedback_observed'
    )
    assert len(feedback_events) == 1
    assert feedback_events[0].data['modification_counts'] is None
    failure_events = tuple(
        event
        for event in result.run_log.events
        if event.event_type == 'component_failure'
    )
    failure = cast(ComponentFailure, failure_events[0].data['failure'])
    assert failure.phase == 'random_generation'


def test_objective_comparison_failure_commits_failed_candidate_attempt(
    tmp_path: Path,
) -> None:
    class FailsDuringArchiveComparison(Objective):
        def __init__(self, objective_id: str) -> None:
            super().__init__(objective_id)
            self.comparison_count = 0

        def compare(self, left: object, right: object) -> ObjectiveComparison:
            self.comparison_count += 1
            if self.comparison_count == 3:
                raise RuntimeError('comparison failed during archive admission')
            return super().compare(left, right)

    engine, _ = make_engine(
        candidate_mode='improve',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (
                FailsDuringArchiveComparison('cost_a'),
                Objective('cost_b'),
            )
        ),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.COMPONENT_FAILURE
    assert result.counters.candidate_attempts == 1
    assert result.counters.evaluation_requests == 1
    assert len(result.candidate_outcomes) == 1
    assert [record.solution for record in result.search_state.pareto_solutions()] == [
        (5, 5)
    ]
    assert result.run_log is not None
    feedback_events = tuple(
        event
        for event in result.run_log.events
        if event.event_type == 'feedback_observed'
    )
    assert len(feedback_events) == 1
    assert feedback_events[0].data['modification_counts'] == (1,)
    assert feedback_events[0].data['candidate_event_sequences'] == ()


def test_feedback_event_records_admission_count_without_reward_scoring(
    tmp_path: Path,
) -> None:
    class DuplicateThenImprove:
        component_id = 'candidate-operator'
        configuration_id = 'candidate-operator-v1'

        def __init__(self) -> None:
            self.calls = 0

        def call(self, context: CandidateContext) -> OperatorResult:
            self.calls += 1
            candidates = {1: (4, 6), 3: (3, 8)}
            solution = candidates.get(self.calls, context.base)
            return OperatorResult(
                candidates=(CandidateProposal(Candidate(solution), context.k),)
            )

    class SelectStandalone:
        component_id = 'select-standalone-feedback'
        configuration_id = 'select-standalone-feedback-v1'

        def distribution(
            self, request: ChoiceSelectionRequest
        ) -> dict[ChoiceId, float]:
            selected = ChoiceId.for_operator('candidate-operator')
            return {
                choice.choice_id: float(choice.choice_id == selected)
                for choice in request.choices
            }

        def observe(self, observation: FeedbackObservation) -> None:
            del observation

        def reset(self) -> None:
            return None

        def capture_state(self) -> object:
            return None

        def restore_state(self, payload: object) -> None:
            if payload is not None:
                raise ValueError('expected empty feedback state')

    operator = DuplicateThenImprove()
    engine = MultiobjectiveVNS(
        solution_codec=cast(SolutionCodec, Codec()),
        solution_identity=Identity(),
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), Objective('cost_b'))
        ),
        operators=(
            OperatorRegistration(
                operator_id='candidate-operator',
                configuration_id='candidate-operator-v1',
                operator=cast(Operator, operator),
                initial_state=None,
                max_neighborhood_size=3,
                feasibility_restoration=False,
                modification_count_definition='one size unit',
            ),
        ),
        application_evaluator=cast(ApplicationEvaluator, Evaluator()),
        problem_instance_id=cast(ProblemInstanceId, 'test-problem-v1'),
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=1,
            max_neighborhood_size=3,
            composition_selection_probability=0.0,
            budget_limits=BudgetLimits(
                max_wall_time_seconds=60,
                max_candidate_attempts=4,
                max_evaluation_requests=4,
            ),
        ),
        feedback_policy=cast(FeedbackPolicy, SelectStandalone()),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.run_log is not None
    observations = tuple(
        event
        for event in result.run_log.events
        if event.event_type == 'feedback_observed'
    )
    assert tuple(event.data['archive_admission_count'] for event in observations) == (
        1,
        0,
        1,
        0,
    )
    assert tuple(event.data['attempt_admitted'] for event in observations) == (
        True,
        False,
        True,
        False,
    )


def test_report_formatting_failures_are_returned_as_report_diagnostics(
    tmp_path: Path,
) -> None:
    class UnrenderableObjective(Objective):
        def __str__(self) -> str:
            raise RuntimeError('name formatting failed')

        def format(self, value: object) -> str:
            del value
            raise RuntimeError('value formatting failed')

    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (UnrenderableObjective('cost_a'), Objective('cost_b'))
        ),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert tuple(item.kind for item in result.report_diagnostics) == (
        ReportDiagnosticKind.OBJECTIVE_NAME_FAILURE,
        ReportDiagnosticKind.OBJECTIVE_VALUE_FORMAT_FAILURE,
    )
    assert result.summary_path is not None
    assert '[unformatted]' in result.summary_path.read_text(encoding='utf-8')


def test_run_saves_and_loads_the_current_pareto_set_by_default(
    tmp_path: Path,
) -> None:
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
    )

    result = engine.run(
        initial_solutions=((5, 5), (4, 6)),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.pareto_snapshot_path is not None
    assert result.run_log is not None
    assert result.pareto_snapshot_path == (
        tmp_path / f'{result.run_log.invocation_id.to_log_text()}.pareto.json'
    )
    assert result.pareto_snapshot_path.is_file()
    restored = engine.load_pareto_snapshot(result.pareto_snapshot_path)
    assert restored == ((5, 5), (4, 6))


def test_pareto_snapshot_compatibility_is_checked_before_restore(
    tmp_path: Path,
) -> None:
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
    )
    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )
    assert result.pareto_snapshot_path is not None

    other_engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (Objective('different-a'), Objective('different-b'))
        ),
    )

    with pytest.raises(ResumeCompatibilityError, match='Pareto snapshot'):
        other_engine.load_pareto_snapshot(result.pareto_snapshot_path)


def test_objective_upper_bound_screens_candidate_and_is_reported(
    tmp_path: Path,
) -> None:
    declaration = ObjectiveDeclaration(
        (Objective('cost_a'), Objective('cost_b')),
        upper_bounds=(None, 5),
    )
    engine, _ = make_engine(
        candidate_mode='improve',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=declaration,
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert [record.solution for record in result.search_state.pareto_solutions()] == [
        (5, 5)
    ]
    assert [record.solution for record in result.search_state.active_solutions()] == [
        (5, 5)
    ]
    candidate = result.candidate_outcomes[-1]
    assert candidate.assessment is ApplicationAssessment.VALID
    assert candidate.disposition is SearchDisposition.OBJECTIVE_BOUND_EXCEEDED
    assert len(candidate.objective_bound_findings) == 1
    finding = candidate.objective_bound_findings[0]
    assert (finding.objective_id, finding.objective_value, finding.upper_bound) == (
        'cost_b',
        6,
        5,
    )
    assert result.search_state.visited_solutions()[-1].objective_bound_findings == (
        finding,
    )
    assert result.run_log is not None
    processed = [
        event
        for event in result.run_log.events
        if event.event_type == 'candidate_processed'
    ][-1]
    event_findings = cast(
        tuple[ObjectiveBoundEventFinding, ...],
        processed.data['objective_bound_findings'],
    )
    event_finding = event_findings[0]
    assert event_finding.objective_id == 'cost_b'
    assert event_finding.objective_value == '6'
    assert event_finding.upper_bound == '5'
    assert result.executive_summary is not None
    assert result.executive_summary.provenance.objective_bounds[0].objective_id == (
        'cost_b'
    )
    assert result.executive_summary.objective_bound_counts[0].count == 1


def test_bound_equal_value_is_accepted_and_infeasible_exceedance_is_screened(
    tmp_path: Path,
) -> None:
    declaration = ObjectiveDeclaration(
        (Objective('cost_a'), Objective('cost_b')),
        upper_bounds=(None, 5),
    )
    engine, _ = make_engine(
        candidate_mode='improve',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=declaration,
        application_evaluator=cast(
            ApplicationEvaluator, InfeasibleCandidateEvaluator()
        ),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.candidate_outcomes[0].objective_bound_findings == ()
    candidate = result.candidate_outcomes[-1]
    assert candidate.assessment is ApplicationAssessment.INFEASIBLE
    assert candidate.disposition is SearchDisposition.OBJECTIVE_BOUND_EXCEEDED
    assert candidate.objective_bound_findings[0].objective_value == 6
    assert all(
        record.solution != (4, 6) for record in result.search_state.active_solutions()
    )


def test_initial_solutions_outside_bounds_raise_after_all_are_processed(
    tmp_path: Path,
) -> None:
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), Objective('cost_b')),
            upper_bounds=(None, 0),
        ),
    )

    with pytest.raises(InitialSolutionsError) as captured:
        engine.run(
            initial_solutions=((5, 5), (5, 5)),
            log_directory=tmp_path,
            progress_report_interval_seconds=60,
        )

    outcomes = captured.value.candidate_outcomes
    assert len(outcomes) == 2
    assert all(outcome.objective_bound_findings for outcome in outcomes)
    assert captured.value.run_log is not None
    failed = next(
        event
        for event in captured.value.run_log.events
        if event.event_type == 'initialization_failed'
    )
    assert failed.data['submission_count'] == 2
    assert failed.data['application_feasible_count'] == 2
    assert failed.data['eligible_count'] == 0


def test_resume_requires_comparator_equal_objective_bounds(tmp_path: Path) -> None:
    original, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), Objective('cost_b')),
            upper_bounds=(None, 10),
        ),
    )
    result = original.run(
        initial_solutions=((1, 1),),
        log_directory=tmp_path / 'first',
        progress_report_interval_seconds=60,
    )
    changed, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), Objective('cost_b')),
            upper_bounds=(None, 11),
        ),
    )

    with pytest.raises(ResumeCompatibilityError, match='objective bounds'):
        changed.run(
            checkpoint=Checkpoint(result.search_state),
            log_directory=tmp_path / 'resumed',
            progress_report_interval_seconds=60,
        )


def test_resume_accepts_comparator_equal_objective_bounds(tmp_path: Path) -> None:
    original, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), Objective('cost_b')),
            upper_bounds=(None, 10),
        ),
    )
    result = original.run(
        initial_solutions=((1, 1),),
        log_directory=tmp_path / 'first',
        progress_report_interval_seconds=60,
    )
    equivalent, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), Objective('cost_b')),
            upper_bounds=(None, 10.0),
        ),
    )

    resumed = equivalent.run(
        checkpoint=Checkpoint(result.search_state),
        log_directory=tmp_path / 'resumed',
        progress_report_interval_seconds=60,
    )

    assert resumed.search_state.pareto_solutions()[0].solution == (1, 1)


def test_resume_bound_comparison_callback_failure_is_a_setup_error(
    tmp_path: Path,
) -> None:
    original, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), Objective('cost_b')),
            upper_bounds=(None, 10),
        ),
    )
    result = original.run(
        initial_solutions=((1, 1),),
        log_directory=tmp_path / 'first',
        progress_report_interval_seconds=60,
    )

    class ResumeComparisonFailure(Objective):
        def compare(self, left: object, right: object) -> ObjectiveComparison:
            if left == 10 and right == 11:
                raise RuntimeError('resume comparison failed')
            return super().compare(left, right)

    changed, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), ResumeComparisonFailure('cost_b')),
            upper_bounds=(None, 11),
        ),
    )

    with pytest.raises(ComponentSetupError) as captured:
        changed.run(
            checkpoint=Checkpoint(result.search_state),
            log_directory=tmp_path / 'resumed',
            progress_report_interval_seconds=60,
        )

    assert captured.value.failure.phase == 'objective_bound_comparison'


def test_objective_bound_comparison_failure_stops_candidate_processing(
    tmp_path: Path,
) -> None:
    class FailingBoundObjective(Objective):
        def compare(self, left: object, right: object) -> ObjectiveComparison:
            if left == 6 and right == 5:
                raise RuntimeError('bound comparison failed')
            return super().compare(left, right)

    engine, _ = make_engine(
        candidate_mode='improve',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), FailingBoundObjective('cost_b')),
            upper_bounds=(None, 5),
        ),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.COMPONENT_FAILURE
    assert result.run_log is not None
    failure_event = next(
        event
        for event in result.run_log.events
        if event.event_type == 'component_failure'
        and cast(ComponentFailure, event.data['failure']).phase
        == 'objective_bound_comparison'
    )
    failure = cast(ComponentFailure, failure_event.data['failure'])
    assert failure.source == 'cost_b-component'
    assert [record.solution for record in result.search_state.pareto_solutions()] == [
        (5, 5)
    ]


def test_objective_bound_formatting_failure_is_reported_without_stopping_search(
    tmp_path: Path,
) -> None:
    class UnformattableObjective(Objective):
        def format(self, value: object) -> str:
            del value
            raise RuntimeError('cannot format value')

    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
        objective_declaration=ObjectiveDeclaration(
            (Objective('cost_a'), UnformattableObjective('cost_b')),
            upper_bounds=(None, 5),
        ),
    )

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert result.executive_summary is not None
    assert any(
        diagnostic.kind is ReportDiagnosticKind.OBJECTIVE_BOUND_FORMAT_FAILURE
        and diagnostic.objective_id == 'cost_b'
        and diagnostic.solution_id is None
        for diagnostic in result.report_diagnostics
    )


def test_final_view_publication_failure_changes_termination_and_keeps_other_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _ = make_engine(
        candidate_mode='duplicate',
        max_neighborhood_size=1,
        max_candidate_attempts=1,
    )
    original_publish = DefaultRunLogger.publish_view

    def fail_registry_view(
        logger: DefaultRunLogger,
        invocation_id: InvocationId,
        problem_instance_id: ProblemInstanceId,
        committed_state: SearchState,
        commit_sequence: int,
        objective_declaration: ObjectiveDeclaration,
        scope: ViewerExportScope,
        is_final: bool,
        solution_formatter: SolutionFormatter | None,
    ) -> Path:
        if scope == 'registry' and is_final:
            raise OSError('registry export unavailable')
        return original_publish(
            logger,
            invocation_id,
            problem_instance_id,
            committed_state,
            commit_sequence,
            objective_declaration,
            scope,
            is_final,
            solution_formatter,
        )

    monkeypatch.setattr(DefaultRunLogger, 'publish_view', fail_registry_view)

    result = engine.run(
        initial_solutions=((5, 5),),
        log_directory=tmp_path,
        progress_report_interval_seconds=60,
    )

    assert result.termination_reason is TerminationReason.COMPONENT_FAILURE
    assert any(
        failure.phase == 'registry_view_publish' for failure in result.diagnostics
    )
    assert result.registry_view_path is None
    assert result.pareto_view_path is not None and result.pareto_view_path.is_file()
    assert result.run_log is not None
    stopped = [
        event
        for event in result.run_log.events
        if event.event_type == 'invocation_stopped'
    ]
    assert len(stopped) == 1
    assert stopped[0].data['termination_reason'] is TerminationReason.COMPONENT_FAILURE
