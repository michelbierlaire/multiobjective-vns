"""Tests for the package-provided event-file logger.

Created: 2026-10-29T14:26:25+00:00
Author: Michel Bierlaire, EPFL
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.default_logger import DefaultRunLogger
from multiobjective_vns.events import AlgorithmEvent, EventPhase
from multiobjective_vns.identifiers import ChoiceId, InvocationId, SolutionId
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.policies import ChoiceDescriptor, InvocationCounters
from multiobjective_vns.records.reporting import (
    ExecutiveSummary,
    ModificationCountFrequency,
    RunProvenance,
)
from multiobjective_vns.records.results import RunLog
from multiobjective_vns.records.status import TerminationReason
from multiobjective_vns.registrations import OperatorGroup


@dataclass(frozen=True)
class StableOperatorId:
    text: str

    def to_log_text(self) -> str:
        return self.text

    def __str__(self) -> str:
        return 'z' if self.text == 'first' else 'a'


def test_default_logger_appends_stable_json_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation_id = InvocationId.new()
    logger = DefaultRunLogger()
    logger.begin(invocation_id, tmp_path)

    first = AlgorithmEvent(
        schema_version=1,
        invocation_id=invocation_id,
        sequence_number=0,
        commit_sequence=None,
        event_type='invocation_started',
        phase=EventPhase.INVOCATION,
        source_id=None,
        choice_id=None,
        solution_id=None,
        data={
            'mode': 'initial',
            'choice_id': ChoiceId.for_operator('swap'),
        },
    )
    second = AlgorithmEvent(
        schema_version=1,
        invocation_id=invocation_id,
        sequence_number=1,
        commit_sequence=None,
        event_type='progress_report',
        phase=EventPhase.BUDGET,
        source_id=None,
        choice_id=None,
        solution_id=None,
        data={'elapsed_seconds': 1.0},
    )

    def fail_if_atomic_rewrite(path: Path, content: str) -> None:
        raise AssertionError(f'append rewrote {path} with {len(content)} characters')

    monkeypatch.setattr(
        DefaultRunLogger,
        '_atomic_write',
        staticmethod(fail_if_atomic_rewrite),
    )
    logger.append((first,))
    logger.append((second,))

    path = tmp_path / f'{invocation_id.to_log_text()}.events.jsonl'
    events = [
        json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
    ]
    assert [event['event_type'] for event in events] == [
        'invocation_started',
        'progress_report',
    ]
    assert events[0]['data']['choice_id'] == {
        'kind': 'operator',
        'operator_id': 'swap',
    }


def test_default_logger_rolls_back_a_failed_event_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation_id = InvocationId.new()
    logger = DefaultRunLogger()
    logger.begin(invocation_id, tmp_path)
    first = AlgorithmEvent(
        schema_version=1,
        invocation_id=invocation_id,
        sequence_number=0,
        commit_sequence=None,
        event_type='invocation_started',
        phase=EventPhase.INVOCATION,
        source_id=None,
        choice_id=None,
        solution_id=None,
        data={'mode': 'initial'},
    )
    second = AlgorithmEvent(
        schema_version=1,
        invocation_id=invocation_id,
        sequence_number=1,
        commit_sequence=None,
        event_type='progress_report',
        phase=EventPhase.BUDGET,
        source_id=None,
        choice_id=None,
        solution_id=None,
        data={'elapsed_seconds': 1.0},
    )
    logger.append((first,))
    path = tmp_path / f'{invocation_id.to_log_text()}.events.jsonl'
    previous_contents = path.read_bytes()
    real_fsync = os.fsync
    should_fail = True

    def fail_first_sync(file_descriptor: int) -> None:
        nonlocal should_fail
        if should_fail:
            should_fail = False
            raise OSError('simulated sync failure')
        real_fsync(file_descriptor)

    monkeypatch.setattr('multiobjective_vns.default_logger.os.fsync', fail_first_sync)

    with pytest.raises(OSError, match='simulated sync failure'):
        logger.append((second,))

    assert path.read_bytes() == previous_contents


def test_default_logger_sorts_set_identifiers_by_their_stable_text(
    tmp_path: Path,
) -> None:
    invocation_id = InvocationId.new()
    logger = DefaultRunLogger()
    logger.begin(invocation_id, tmp_path)
    group = OperatorGroup(
        'group',
        frozenset((StableOperatorId('first'), StableOperatorId('second'))),
    )

    logger.append(
        (
            AlgorithmEvent(
                schema_version=1,
                invocation_id=invocation_id,
                sequence_number=0,
                commit_sequence=None,
                event_type='invocation_started',
                phase=EventPhase.INVOCATION,
                source_id=None,
                choice_id=None,
                solution_id=None,
                data={'group': group},
            ),
        )
    )

    path = tmp_path / f'{invocation_id.to_log_text()}.events.jsonl'
    event = json.loads(path.read_text(encoding='utf-8'))
    assert event['data']['group']['operator_ids'] == ['first', 'second']


def test_human_summary_uses_candidate_attempt_count() -> None:
    summary = ExecutiveSummary(
        invocation_id=InvocationId.new(),
        invocation_kind='search',
        termination_reason=TerminationReason.BUDGET_EXHAUSTED,
        elapsed_seconds=1.0,
        wall_time_limit_seconds=5.0,
        remaining_seconds=4.0,
        initial_pareto_count=0,
        final_pareto_solutions=(),
        counters=InvocationCounters(candidate_attempts=3, evaluation_requests=2),
        provenance=RunProvenance((), (), ()),
        choice_statistics=(),
        promoted_compositions=(),
        assessment_counts=(),
        failure_counts=(),
        disposition_counts=(),
        excursion_frequencies=(),
        composition_selection_probability=0.2,
        feedback_selection_mode='uniform',
    )

    rendered = DefaultRunLogger._render_summary(
        summary, cast(ObjectiveDeclaration, object()), []
    )

    assert 'Candidate attempts: 3' in rendered
    assert 'Base-search episodes' not in rendered


def test_choice_statistics_aggregate_candidate_lists_and_attempt_success() -> None:
    invocation_id = InvocationId.new()
    choice_id = ChoiceId.for_operator('swap')
    descriptor = ChoiceDescriptor(
        choice_id=choice_id,
        operator_ids=('swap',),
        current_k=1,
        max_neighborhood_size=2,
        feasibility_restoration=False,
        shared_group_ids=frozenset(),
        modification_count_definition='number of swaps',
    )

    def event(
        sequence_number: int,
        event_type: str,
        phase: EventPhase,
        data: dict[str, object],
    ) -> AlgorithmEvent:
        return AlgorithmEvent(
            schema_version=2,
            invocation_id=invocation_id,
            sequence_number=sequence_number,
            commit_sequence=None,
            event_type=event_type,
            phase=phase,
            source_id=None,
            choice_id=choice_id if event_type == 'feedback_observed' else None,
            solution_id=SolutionId(0) if event_type == 'feedback_observed' else None,
            data=data,
        )

    run_log = RunLog(
        invocation_id,
        (
            event(
                0,
                'choice_set_available',
                EventPhase.CHOICE_SELECTION,
                {'newly_eligible_choices': (descriptor,)},
            ),
            event(
                1,
                'feedback_observed',
                EventPhase.FEEDBACK,
                {
                    'k': 1,
                    'modification_counts': (1, 1),
                    'archive_admission_count': 2,
                    'attempt_admitted': True,
                },
            ),
            event(
                2,
                'feedback_observed',
                EventPhase.FEEDBACK,
                {
                    'k': 1,
                    'modification_counts': (),
                    'archive_admission_count': 0,
                    'attempt_admitted': False,
                },
            ),
            event(
                3,
                'feedback_observed',
                EventPhase.FEEDBACK,
                {
                    'k': 1,
                    'modification_counts': None,
                    'archive_admission_count': 0,
                    'attempt_admitted': False,
                },
            ),
        ),
    )

    statistics = DefaultRunLogger._choice_statistics(run_log)

    assert len(statistics) == 2
    size_one, size_two = statistics
    assert size_one.attempt_count == 3
    assert size_one.candidate_count == 2
    assert size_one.admitting_attempt_count == 1
    assert size_one.archive_admission_count == 2
    assert size_one.modification_count_frequencies == (
        ModificationCountFrequency(None, 1),
        ModificationCountFrequency(0, 1),
        ModificationCountFrequency(1, 2),
    )
    assert size_two.attempt_count == 0
    assert size_two.candidate_count == 0
