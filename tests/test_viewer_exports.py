"""Tests for the generic Pareto-view JSON publisher.

Created: 2026-11-12T12:44:55+00:00
Author: Michel Bierlaire, EPFL
"""

import json
from dataclasses import dataclass
from pathlib import Path

from multiobjective_vns.identifiers import InvocationId, SolutionId
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)
from multiobjective_vns.records.evaluation import (
    ApplicationEvaluation,
    EvaluationRecord,
    SolutionRecord,
)
from multiobjective_vns.records.failures import ComponentFailure, FailureKind
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    ArchiveStatus,
    ProcessingStatus,
)
from multiobjective_vns.viewer_exports import build_view_export, write_view_export


class IntegerObjective:
    def __init__(self, objective_id: str) -> None:
        self.objective_id = objective_id
        self.component_id = f'{objective_id}-component'
        self.configuration_id = f'{objective_id}-configuration'

    def compare(self, left: object, right: object) -> ObjectiveComparison:
        if left < right:  # type: ignore[operator]
            return ObjectiveComparison.LESS
        if left > right:  # type: ignore[operator]
            return ObjectiveComparison.GREATER
        return ObjectiveComparison.EQUAL

    def format(self, value: object) -> str:
        return str(value)

    def __str__(self) -> str:
        return self.objective_id.upper()


@dataclass(frozen=True)
class State:
    records: tuple[SolutionRecord, ...]
    pareto_ids: frozenset[object]

    def visited_solutions(self) -> tuple[SolutionRecord, ...]:
        return self.records

    def pareto_solutions(self) -> tuple[SolutionRecord, ...]:
        return tuple(
            record for record in self.records if record.solution_id in self.pareto_ids
        )

    def active_solutions(self) -> tuple[SolutionRecord, ...]:
        return self.records


def _record(
    sequence: int, values: tuple[int, ...], status: ArchiveStatus
) -> SolutionRecord:
    return SolutionRecord(
        solution_id=SolutionId(sequence),
        solution=('solution', sequence),
        evaluation_record=EvaluationRecord(
            ProcessingStatus.COMPLETED,
            ApplicationEvaluation(True, True, values),
            ApplicationAssessment.VALID,
        ),
        archive_status=status,
    )


def test_build_view_export_uses_scope_and_normalized_objective_order() -> None:
    pareto = _record(0, (1, 8, 5), ArchiveStatus.PARETO_OPTIMAL)
    rejected = _record(1, (3, 4, 7), ArchiveStatus.REJECTED)
    state = State((pareto, rejected), frozenset((pareto.solution_id,)))
    objectives = ObjectiveDeclaration(
        (IntegerObjective('cost'), IntegerObjective('risk'), IntegerObjective('size'))
    )

    document, diagnostics = build_view_export(
        invocation_id=InvocationId.new(),
        problem_instance_id='demo-instance',
        committed_state=state,
        commit_sequence=4,
        objective_declaration=objectives,
        scope='registry',
        is_final=False,
        solution_formatter=None,
    )

    assert not diagnostics
    assert document['record_scope'] == 'registry'
    assert document['commit_sequence'] == 4
    assert document['is_final'] is False
    solutions = document['solutions']
    assert isinstance(solutions, list)
    assert len(solutions) == 2
    first_values = solutions[0]['objective_values']
    assert [item['normalized_position'] for item in first_values] == [0.0, 1.0, 0.0]
    second_values = solutions[1]['objective_values']
    assert [item['normalized_position'] for item in second_values] == [1.0, 0.0, 1.0]

    pareto_document, _ = build_view_export(
        invocation_id=InvocationId.new(),
        problem_instance_id='demo-instance',
        committed_state=state,
        commit_sequence=4,
        objective_declaration=objectives,
        scope='pareto',
        is_final=True,
        solution_formatter=None,
    )
    assert len(pareto_document['solutions']) == 1  # type: ignore[arg-type]


def test_build_view_export_retains_failed_record_with_missing_values() -> None:
    failed = SolutionRecord(
        SolutionId(8),
        ('failed',),
        EvaluationRecord(
            ProcessingStatus.FAILED,
            None,
            None,
            (
                ComponentFailure(
                    FailureKind.CALLBACK_EXCEPTION,
                    'evaluator',
                    'evaluation',
                    'failed',
                ),
            ),
        ),
    )
    state = State((failed,), frozenset())
    document, _ = build_view_export(
        invocation_id=InvocationId.new(),
        problem_instance_id='demo-instance',
        committed_state=state,
        commit_sequence=0,
        objective_declaration=ObjectiveDeclaration((IntegerObjective('cost'),)),
        scope='registry',
        is_final=True,
        solution_formatter=None,
    )

    values = document['solutions'][0]['objective_values']  # type: ignore[index]
    assert values == [
        {'objective_id': 'cost', 'display_value': None, 'normalized_position': None}
    ]


def test_write_view_export_replaces_destination_atomically(tmp_path: Path) -> None:
    path = tmp_path / 'view.json'
    write_view_export(path, {'format': 'multiobjective-vns-pareto-view'})

    assert (
        json.loads(path.read_text(encoding='utf-8'))['format']
        == 'multiobjective-vns-pareto-view'
    )
    assert tuple(tmp_path.iterdir()) == (path,)
