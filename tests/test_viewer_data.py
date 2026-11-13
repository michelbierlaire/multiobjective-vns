"""Tests for validated Pareto-view exports.

Created: 2026-11-12T12:44:55+00:00
Author: Michel Bierlaire, EPFL
"""

import json
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.pareto_viewer import VIEW_FORMAT as PACKAGE_VIEW_FORMAT
from multiobjective_vns.pareto_viewer.data import (
    VIEW_FORMAT,
    VIEW_FORMAT_VERSION,
    read_view_export,
)


def _document() -> dict[str, object]:
    return {
        'format': VIEW_FORMAT,
        'format_version': VIEW_FORMAT_VERSION,
        'invocation_id': 'invocation-1',
        'problem_instance_id': 'problem-1',
        'record_scope': 'pareto',
        'commit_sequence': 3,
        'is_final': True,
        'objectives': [
            {'objective_id': 'distance', 'label': 'Distance', 'direction': 'minimize'}
        ],
        'solutions': [
            {
                'solution_id': 'solution:0',
                'processing_status': 'completed',
                'assessment': 'valid',
                'archive_status': 'pareto_optimal',
                'objective_values': [
                    {
                        'objective_id': 'distance',
                        'display_value': '10',
                        'normalized_position': 0.5,
                    }
                ],
                'description': None,
            }
        ],
    }


def test_read_view_export_validates_and_returns_immutable_records(
    tmp_path: Path,
) -> None:
    assert PACKAGE_VIEW_FORMAT == VIEW_FORMAT
    path = tmp_path / 'view.json'
    path.write_text(json.dumps(_document()), encoding='utf-8')

    export = read_view_export(path)

    assert export.record_scope == 'pareto'
    assert export.solutions[0].objective_values[0].normalized_position == 0.5


def test_reader_accepts_version_one_exports_and_version_two_tie_status(
    tmp_path: Path,
) -> None:
    old_document = _document()
    old_document['format_version'] = 1
    old_path = tmp_path / 'version-one.json'
    old_path.write_text(json.dumps(old_document), encoding='utf-8')
    assert read_view_export(old_path).solutions[0].archive_status == 'pareto_optimal'

    current_document = _document()
    current_document['record_scope'] = 'registry'
    current_document['solutions'][0]['archive_status'] = 'objective_vector_duplicate'
    current_path = tmp_path / 'version-two.json'
    current_path.write_text(json.dumps(current_document), encoding='utf-8')
    assert read_view_export(current_path).solutions[0].archive_status == (
        'objective_vector_duplicate'
    )


@pytest.mark.parametrize(
    ('change', 'message'),
    [
        (lambda data: data.update(format_version=True), 'version'),
        (
            lambda data: data['solutions'][0]['objective_values'][0].update(
                normalized_position=True
            ),
            'normalized_position',
        ),
        (
            lambda data: data['solutions'][0].update(archive_status='dominated'),
            'Pareto',
        ),
        (
            lambda data: data['solutions'][0]['objective_values'][0].update(
                objective_id='other'
            ),
            'objective order',
        ),
    ],
)
def test_read_view_export_rejects_malformed_schema(
    tmp_path: Path,
    change: object,
    message: str,
) -> None:
    document = _document()
    change(document)  # type: ignore[operator]
    path = tmp_path / 'bad.json'
    path.write_text(json.dumps(document), encoding='utf-8')

    with pytest.raises(ValueError, match=message):
        read_view_export(path)


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('processing_status', 'failed'),
        ('assessment', 'infeasible'),
    ],
)
def test_pareto_scope_requires_completed_valid_assessment(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    document = _document()
    solutions = cast(list[dict[str, object]], document['solutions'])
    solution = solutions[0]
    solution[field] = value
    if field == 'processing_status':
        solution['assessment'] = None
    path = tmp_path / 'bad-pareto.json'
    path.write_text(json.dumps(document), encoding='utf-8')

    with pytest.raises(ValueError, match='Pareto-scope records'):
        read_view_export(path)
