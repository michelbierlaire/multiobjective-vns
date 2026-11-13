"""Read and validate the public Pareto-view JSON schema.

Created: 2026-11-12T12:44:55+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

VIEW_FORMAT = 'multiobjective-vns-pareto-view'
VIEW_FORMAT_VERSION = 2
RecordScope = Literal['registry', 'pareto']
ProcessingStatusText = Literal['completed', 'failed']
AssessmentText = Literal['valid', 'invalid', 'infeasible']
ArchiveStatusText = Literal[
    'pareto_optimal', 'dominated', 'objective_vector_duplicate', 'rejected'
]


@dataclass(frozen=True, slots=True)
class ObjectiveView:
    """Display label and identity for one ordered objective."""

    objective_id: str
    label: str
    direction: Literal['minimize']


@dataclass(frozen=True, slots=True)
class ObjectiveValueView:
    """Formatted value and normalized rank position for one objective."""

    objective_id: str
    display_value: str | None
    normalized_position: float | None


@dataclass(frozen=True, slots=True)
class SolutionView:
    """One registry or archive record prepared for viewer display."""

    solution_id: str
    processing_status: ProcessingStatusText
    assessment: AssessmentText | None
    archive_status: ArchiveStatusText | None
    objective_values: tuple[ObjectiveValueView, ...]
    description: str | None


@dataclass(frozen=True, slots=True)
class ViewExport:
    """Validated immutable representation of one viewer export."""

    invocation_id: str
    problem_instance_id: str
    record_scope: RecordScope
    commit_sequence: int
    is_final: bool
    objectives: tuple[ObjectiveView, ...]
    solutions: tuple[SolutionView, ...]


def read_view_export(path: Path) -> ViewExport:
    """Read and validate a versioned viewer export.

    :param path: UTF-8 JSON viewer-export path.
    :returns: Immutable data ready for plotting.
    :raises OSError: If the path cannot be read.
    :raises ValueError: If JSON or any schema field is malformed.
    """
    if not isinstance(path, Path):
        raise TypeError('path must be Path')
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ValueError(f'invalid viewer JSON: {error}') from error
    top = {
        'format',
        'format_version',
        'invocation_id',
        'problem_instance_id',
        'record_scope',
        'commit_sequence',
        'is_final',
        'objectives',
        'solutions',
    }
    if not isinstance(document, dict) or set(document) != top:
        raise ValueError('viewer export fields do not match a supported version')
    if document['format'] != VIEW_FORMAT:
        raise ValueError('unsupported viewer export format')
    if type(document['format_version']) is not int or document[
        'format_version'
    ] not in (1, VIEW_FORMAT_VERSION):
        raise ValueError('unsupported viewer export version')
    version = document['format_version']
    invocation_id = _nonempty_text(document['invocation_id'], 'invocation_id')
    problem_instance_id = _nonempty_text(
        document['problem_instance_id'], 'problem_instance_id'
    )
    scope = document['record_scope']
    if scope not in ('registry', 'pareto'):
        raise ValueError("record_scope must be 'registry' or 'pareto'")
    commit_sequence = document['commit_sequence']
    if type(commit_sequence) is not int or commit_sequence < 0:
        raise ValueError('commit_sequence must be a nonnegative integer')
    is_final = document['is_final']
    if not isinstance(is_final, bool):
        raise ValueError('is_final must be Boolean')
    objective_values = document['objectives']
    if not isinstance(objective_values, list) or not objective_values:
        raise ValueError('objectives must be a nonempty array')
    objectives = tuple(_read_objective(item) for item in objective_values)
    objective_ids = tuple(objective.objective_id for objective in objectives)
    if len(set(objective_ids)) != len(objective_ids):
        raise ValueError('objective IDs must be unique')
    solution_values = document['solutions']
    if not isinstance(solution_values, list):
        raise ValueError('solutions must be an array')
    solutions = tuple(
        _read_solution(item, objectives, cast(RecordScope, scope), version)
        for item in solution_values
    )
    solution_ids = tuple(solution.solution_id for solution in solutions)
    if len(set(solution_ids)) != len(solution_ids):
        raise ValueError('solution IDs must be unique')
    return ViewExport(
        invocation_id,
        problem_instance_id,
        cast(RecordScope, scope),
        commit_sequence,
        is_final,
        objectives,
        solutions,
    )


def _read_objective(value: object) -> ObjectiveView:
    fields = {'objective_id', 'label', 'direction'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('objective record fields do not match version 1')
    direction = value['direction']
    if direction != 'minimize':
        raise ValueError("objective direction must be 'minimize'")
    return ObjectiveView(
        _nonempty_text(value['objective_id'], 'objective_id'),
        _nonempty_text(value['label'], 'objective label'),
        'minimize',
    )


def _read_solution(
    value: object,
    objectives: tuple[ObjectiveView, ...],
    scope: RecordScope,
    version: int,
) -> SolutionView:
    fields = {
        'solution_id',
        'processing_status',
        'assessment',
        'archive_status',
        'objective_values',
        'description',
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('solution record fields do not match version 1')
    solution_id = _nonempty_text(value['solution_id'], 'solution_id')
    processing = value['processing_status']
    if processing not in ('completed', 'failed'):
        raise ValueError('processing_status must be completed or failed')
    assessment = value['assessment']
    if assessment is not None and assessment not in ('valid', 'invalid', 'infeasible'):
        raise ValueError('assessment has an unknown value')
    if (processing == 'completed') != (assessment is not None):
        raise ValueError('processing status and assessment are inconsistent')
    archive = value['archive_status']
    archive_statuses = {'pareto_optimal', 'dominated', 'rejected'}
    if version >= 2:
        archive_statuses.add('objective_vector_duplicate')
    if archive is not None and archive not in archive_statuses:
        raise ValueError('archive_status has an unknown value')
    coordinates = value['objective_values']
    if not isinstance(coordinates, list) or len(coordinates) != len(objectives):
        raise ValueError('objective_values length must match objective order')
    objective_values = tuple(
        _read_objective_value(item, objective.objective_id)
        for item, objective in zip(coordinates, objectives, strict=True)
    )
    description = value['description']
    if description is not None and not isinstance(description, str):
        raise ValueError('description must be text or null')
    if scope == 'pareto' and (
        archive != 'pareto_optimal'
        or processing != 'completed'
        or assessment != 'valid'
        or any(item.normalized_position is None for item in objective_values)
    ):
        raise ValueError('Pareto-scope records require completed valid Pareto values')
    if archive is not None and (
        processing != 'completed'
        or assessment != 'valid'
        or any(item.normalized_position is None for item in objective_values)
    ):
        raise ValueError('archive records require completed valid objective values')
    return SolutionView(
        solution_id,
        cast(ProcessingStatusText, processing),
        cast(AssessmentText | None, assessment),
        cast(ArchiveStatusText | None, archive),
        objective_values,
        description,
    )


def _read_objective_value(value: object, expected_id: str) -> ObjectiveValueView:
    fields = {'objective_id', 'display_value', 'normalized_position'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('objective value fields do not match version 1')
    objective_id = _nonempty_text(value['objective_id'], 'objective value ID')
    if objective_id != expected_id:
        raise ValueError('objective order does not match the declaration')
    display = value['display_value']
    if display is not None and (not isinstance(display, str) or not display):
        raise ValueError('display_value must be nonempty text or null')
    position = value['normalized_position']
    if position is not None:
        if (
            not isinstance(position, (int, float))
            or isinstance(position, bool)
            or not math.isfinite(float(position))
            or not 0.0 <= float(position) <= 1.0
        ):
            raise ValueError('normalized_position must be finite and in [0, 1]')
        position = float(position)
    if (display is None) != (position is None):
        raise ValueError('display and normalized position must be available together')
    return ObjectiveValueView(objective_id, display, cast(float | None, position))


def _nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be nonempty text')
    return value


__all__ = [
    'VIEW_FORMAT',
    'VIEW_FORMAT_VERSION',
    'ObjectiveValueView',
    'ObjectiveView',
    'SolutionView',
    'ViewExport',
    'read_view_export',
]
