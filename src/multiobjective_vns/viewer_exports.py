"""Build and atomically publish objective-based viewer exports.

Created: 2026-11-12T12:44:55+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import json
import os
import tempfile
from functools import cmp_to_key
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from multiobjective_vns.identifiers import (
    InvocationId,
    ProblemInstanceId,
    identifier_text,
)
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
    ObjectiveValue,
)
from multiobjective_vns.records.reporting import (
    ReportDiagnostic,
    ReportDiagnosticKind,
)
from multiobjective_vns.records.state import SearchState

if TYPE_CHECKING:
    from multiobjective_vns.services import SolutionFormatter

ViewerExportScope = Literal['registry', 'pareto']
VIEW_FORMAT = 'multiobjective-vns-pareto-view'
VIEW_FORMAT_VERSION = 2


def build_view_export(
    *,
    invocation_id: InvocationId,
    problem_instance_id: ProblemInstanceId,
    committed_state: SearchState,
    commit_sequence: int,
    objective_declaration: ObjectiveDeclaration,
    scope: ViewerExportScope,
    is_final: bool,
    solution_formatter: SolutionFormatter | None,
) -> tuple[dict[str, object], tuple[ReportDiagnostic, ...]]:
    """Create one schema-version-1 viewer document and reporting diagnostics.

    :param invocation_id: Invocation represented by the committed state.
    :param problem_instance_id: Fixed application instance identity.
    :param committed_state: Immutable state snapshot being exported.
    :param commit_sequence: Sequence of the represented search commit.
    :param objective_declaration: Ordered minimized objectives.
    :param scope: ``registry`` for visited records or ``pareto`` for archive members.
    :param is_final: Whether the represented state is final for its invocation.
    :param solution_formatter: Optional application description callback.
    :returns: JSON-compatible document and nonfatal display diagnostics.
    """
    if not isinstance(invocation_id, InvocationId):
        raise TypeError('invocation_id must be InvocationId')
    instance_text = identifier_text(problem_instance_id)
    if not isinstance(commit_sequence, int) or isinstance(commit_sequence, bool):
        raise TypeError('commit_sequence must be an integer')
    if commit_sequence < 0:
        raise ValueError('commit_sequence must be nonnegative')
    if scope not in ('registry', 'pareto'):
        raise ValueError("scope must be 'registry' or 'pareto'")
    if not isinstance(is_final, bool):
        raise TypeError('is_final must be bool')

    records = (
        committed_state.visited_solutions()
        if scope == 'registry'
        else committed_state.pareto_solutions()
    )
    objectives = objective_declaration.objectives
    diagnostics: list[ReportDiagnostic] = []
    objective_labels: list[str] = []
    for objective in objectives:
        objective_id = objective.objective_id
        try:
            label = str(objective)
            if not label.strip():
                raise ValueError('objective name must be nonempty')
        except Exception as error:
            label = identifier_text(objective_id)
            diagnostics.append(
                ReportDiagnostic(
                    ReportDiagnosticKind.OBJECTIVE_NAME_FAILURE,
                    objective.component_id,
                    None,
                    objective_id,
                    f'ObjectiveFunction.__str__ failed: {error}',
                )
            )
        objective_labels.append(label)

    positions: list[list[float | None]] = [
        [None] * len(objectives) for _record in records
    ]
    display_values: list[list[str | None]] = [
        [None] * len(objectives) for _record in records
    ]

    for objective_index, objective in enumerate(objectives):
        usable: list[tuple[ObjectiveValue, int]] = []
        for record_index, record in enumerate(records):
            evaluation = record.evaluation_record.application_evaluation
            if evaluation is None or evaluation.objective_values is None:
                continue
            values = evaluation.objective_values
            if len(values) != len(objectives):
                continue
            value = values[objective_index]
            usable.append((value, record_index))
            try:
                formatted = objective.format(value)
                if not isinstance(formatted, str) or not formatted:
                    raise ValueError('formatted value must be nonempty text')
            except Exception as error:
                formatted = '[unformatted]'
                diagnostics.append(
                    ReportDiagnostic(
                        ReportDiagnosticKind.OBJECTIVE_VALUE_FORMAT_FAILURE,
                        objective.component_id,
                        record.solution_id,
                        objective.objective_id,
                        f'ObjectiveFunction.format failed: {error}',
                    )
                )
            display_values[record_index][objective_index] = formatted

        def compare_entries(
            left: tuple[ObjectiveValue, int], right: tuple[ObjectiveValue, int]
        ) -> int:
            return _comparison_sign(objective.compare(left[0], right[0]))

        ordered = sorted(usable, key=cmp_to_key(compare_entries))
        groups: list[list[int]] = []
        previous_value: ObjectiveValue | None = None
        for value, record_index in ordered:
            if (
                not groups
                or _comparison_sign(objective.compare(previous_value, value)) != 0
            ):
                groups.append([])
            groups[-1].append(record_index)
            previous_value = value
        group_count = len(groups)
        for rank, group in enumerate(groups):
            position = 0.5 if group_count == 1 else rank / (group_count - 1)
            for record_index in group:
                positions[record_index][objective_index] = position

    objective_records = [
        {
            'objective_id': identifier_text(objective.objective_id),
            'label': objective_labels[index],
            'direction': 'minimize',
        }
        for index, objective in enumerate(objectives)
    ]
    solution_records: list[dict[str, object]] = []
    for record_index, record in enumerate(records):
        evaluation_record = record.evaluation_record
        evaluation = evaluation_record.application_evaluation
        description: str | None = None
        if solution_formatter is not None:
            try:
                description = solution_formatter.describe(record.solution)
                if not isinstance(description, str) or not description:
                    raise ValueError('description must be nonempty text')
            except Exception as error:
                diagnostics.append(
                    ReportDiagnostic(
                        ReportDiagnosticKind.SOLUTION_DESCRIPTION_FAILURE,
                        solution_formatter.component_id,
                        record.solution_id,
                        None,
                        f'SolutionFormatter.describe failed: {error}',
                    )
                )
        value_records = [
            {
                'objective_id': identifier_text(objective.objective_id),
                'display_value': display_values[record_index][index],
                'normalized_position': positions[record_index][index],
            }
            for index, objective in enumerate(objectives)
        ]
        solution_records.append(
            {
                'solution_id': record.solution_id.to_log_text(),
                'processing_status': evaluation_record.processing_status.value,
                'assessment': (
                    evaluation_record.assessment.value
                    if evaluation_record.assessment is not None
                    else None
                ),
                'archive_status': _archive_status_text(record.archive_status),
                'objective_values': value_records,
                'description': description,
            }
        )

    return (
        {
            'format': VIEW_FORMAT,
            'format_version': VIEW_FORMAT_VERSION,
            'invocation_id': invocation_id.to_log_text(),
            'problem_instance_id': instance_text,
            'record_scope': scope,
            'commit_sequence': commit_sequence,
            'is_final': is_final,
            'objectives': objective_records,
            'solutions': solution_records,
        },
        tuple(diagnostics),
    )


def write_view_export(path: Path, document: dict[str, object]) -> None:
    """Atomically publish a JSON viewer document at ``path``.

    :param path: Destination file in the invocation log directory.
    :param document: JSON-compatible viewer schema.
    :raises OSError: If serialization or durable publication fails.
    """
    if not isinstance(path, Path):
        raise TypeError('path must be Path')
    serialized = json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        separators=(',', ':'),
        sort_keys=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(serialized)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _comparison_sign(comparison: ObjectiveComparison) -> int:
    if comparison is ObjectiveComparison.LESS:
        return -1
    if comparison is ObjectiveComparison.EQUAL:
        return 0
    if comparison is ObjectiveComparison.GREATER:
        return 1
    raise TypeError('ObjectiveFunction.compare must return ObjectiveComparison')


def _archive_status_text(status: object) -> str | None:
    if status is None:
        return None
    value = getattr(status, 'value', None)
    mapping = {
        'Pareto optimal': 'pareto_optimal',
        'Dominated': 'dominated',
        'Objective-vector duplicate': 'objective_vector_duplicate',
        'Rejected': 'rejected',
    }
    if value not in mapping:
        raise ValueError('unknown ArchiveStatus in SearchState')
    return mapping[value]


__all__ = [
    'VIEW_FORMAT',
    'VIEW_FORMAT_VERSION',
    'ViewerExportScope',
    'build_view_export',
    'write_view_export',
]
