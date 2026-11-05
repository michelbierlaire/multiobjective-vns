"""Tests for opaque continuation and public invocation result records.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from uuid import UUID

import pytest

from multiobjective_vns.events import AlgorithmEvent, EventPhase
from multiobjective_vns.identifiers import InvocationId, SolutionId
from multiobjective_vns.records.candidates import CandidateOutcome
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
    SolutionRecord,
)
from multiobjective_vns.records.policies import InvocationCounters
from multiobjective_vns.records.results import RunLog, RunResult
from multiobjective_vns.records.state import Checkpoint, SearchState
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    ProcessingStatus,
    TerminationReason,
)


class SimpleState:
    def __init__(self, record: SolutionRecord) -> None:
        self.record = record

    def pareto_solutions(self) -> tuple[SolutionRecord, ...]:
        return (self.record,)

    def active_solutions(self) -> tuple[SolutionRecord, ...]:
        return (self.record,)

    def visited_solutions(self) -> tuple[SolutionRecord, ...]:
        return (self.record,)


def test_checkpoint_wraps_an_opaque_state() -> None:
    evaluation = ApplicationEvaluation(True, True, (1, 2))
    record = SolutionRecord(
        SolutionId(1),
        ('solution',),
        EvaluationRecord(
            ProcessingStatus.COMPLETED,
            evaluation,
            ApplicationAssessment.VALID,
        ),
    )
    state: SearchState = SimpleState(record)
    checkpoint = Checkpoint(state)

    assert checkpoint.search_state.visited_solutions() == (record,)


def test_run_result_has_stable_empty_optional_collections() -> None:
    invocation_id = InvocationId(UUID(int=2))
    event = AlgorithmEvent(
        schema_version=1,
        invocation_id=invocation_id,
        sequence_number=0,
        commit_sequence=None,
        event_type='invocation_stopped',
        phase=EventPhase.INVOCATION,
        source_id=None,
        choice_id=None,
        solution_id=None,
        data={'termination_reason': 'budget_exhausted'},
    )
    record = SolutionRecord(
        SolutionId(2),
        ('solution',),
        EvaluationRecord(
            ProcessingStatus.COMPLETED,
            ApplicationEvaluation(True, True, (1, 2)),
            ApplicationAssessment.VALID,
        ),
    )
    state = SimpleState(record)
    result = RunResult(
        search_state=state,
        candidate_outcomes=(),
        termination_reason=TerminationReason.BUDGET_EXHAUSTED,
        diagnostics=(),
        counters=InvocationCounters(),
    )
    run_log = RunLog(invocation_id, (event,))

    assert result.candidate_outcomes == ()
    assert result.run_log is None
    assert run_log.events == (event,)
    with pytest.raises((AttributeError, TypeError)):
        result.termination_reason = TerminationReason.INTERRUPTED  # type: ignore[misc]


def test_candidate_outcome_type_is_available_for_result_collections() -> None:
    assert CandidateOutcome.__name__ == 'CandidateOutcome'
