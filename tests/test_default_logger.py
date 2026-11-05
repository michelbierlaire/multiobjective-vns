"""Tests for the package-provided event-file logger.

Created: 2026-10-29T14:26:25+00:00
Author: Michel Bierlaire, EPFL
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from multiobjective_vns.default_logger import DefaultRunLogger
from multiobjective_vns.events import AlgorithmEvent, EventPhase
from multiobjective_vns.identifiers import ChoiceId, InvocationId
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.policies import InvocationCounters
from multiobjective_vns.records.reporting import (
    ExecutiveSummary,
    RunProvenance,
)
from multiobjective_vns.records.status import TerminationReason
from multiobjective_vns.registrations import OperatorGroup


@dataclass(frozen=True)
class StableOperatorId:
    text: str

    def to_log_text(self) -> str:
        return self.text

    def __str__(self) -> str:
        return 'z' if self.text == 'first' else 'a'


def test_default_logger_appends_stable_json_lines(tmp_path: Path) -> None:
    invocation_id = InvocationId.new()
    logger = DefaultRunLogger()
    logger.begin(invocation_id, tmp_path)

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
                data={
                    'mode': 'initial',
                    'choice_id': ChoiceId.for_operator('swap'),
                },
            ),
        )
    )

    path = tmp_path / f'{invocation_id.to_log_text()}.events.jsonl'
    event = json.loads(path.read_text(encoding='utf-8'))
    assert event['event_type'] == 'invocation_started'
    assert event['data']['choice_id'] == {
        'kind': 'operator',
        'operator_id': 'swap',
    }


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
