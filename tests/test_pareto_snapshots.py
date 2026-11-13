"""Tests for versioned Pareto snapshot files.

Created: 2026-11-10T19:31:34+01:00
Author: Michel Bierlaire, EPFL
"""

import json
from pathlib import Path

import pytest

from multiobjective_vns.pareto_snapshots import (
    ParetoSnapshotData,
    read_pareto_snapshot,
    write_pareto_snapshot,
)


def make_snapshot() -> ParetoSnapshotData:
    return ParetoSnapshotData(
        problem_instance_id='problem-v1',
        objective_ids=('distance', 'waiting'),
        objective_configuration_ids=('distance-v1', 'waiting-v1'),
        solution_codec_component_id='solution-codec',
        solution_codec_configuration_id='codec-v1',
        solution_payloads=(b'first\x00payload', b'second payload'),
    )


def test_pareto_snapshot_file_round_trips_opaque_solution_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'run.pareto.json'
    snapshot = make_snapshot()

    write_pareto_snapshot(path, snapshot)

    assert read_pareto_snapshot(path) == snapshot
    document = json.loads(path.read_text(encoding='utf-8'))
    assert document['format'] == 'multiobjective-vns-pareto-snapshot'
    assert document['format_version'] == 1
    assert document['objective_ids'] == ['distance', 'waiting']
    assert document['solution_payloads'] != [
        'first\u0000payload',
        'second payload',
    ]


def test_pareto_snapshot_reader_rejects_invalid_base64(tmp_path: Path) -> None:
    path = tmp_path / 'bad.pareto.json'
    document = {
        'format': 'multiobjective-vns-pareto-snapshot',
        'format_version': 1,
        'problem_instance_id': 'problem-v1',
        'objective_ids': ['distance'],
        'objective_configuration_ids': ['distance-v1'],
        'solution_codec_component_id': 'solution-codec',
        'solution_codec_configuration_id': 'codec-v1',
        'solution_payloads': ['not base64!'],
    }
    path.write_text(json.dumps(document), encoding='utf-8')

    with pytest.raises(ValueError, match='base64'):
        read_pareto_snapshot(path)


def test_pareto_snapshot_reader_rejects_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / 'future.pareto.json'
    document = {
        'format': 'multiobjective-vns-pareto-snapshot',
        'format_version': 2,
        'problem_instance_id': 'problem-v1',
        'objective_ids': ['distance'],
        'objective_configuration_ids': ['distance-v1'],
        'solution_codec_component_id': 'solution-codec',
        'solution_codec_configuration_id': 'codec-v1',
        'solution_payloads': [],
    }
    path.write_text(json.dumps(document), encoding='utf-8')

    with pytest.raises(ValueError, match='version'):
        read_pareto_snapshot(path)


def test_pareto_snapshot_requires_matching_objective_metadata() -> None:
    with pytest.raises(ValueError, match='same length'):
        ParetoSnapshotData(
            problem_instance_id='problem-v1',
            objective_ids=('distance',),
            objective_configuration_ids=(),
            solution_codec_component_id='solution-codec',
            solution_codec_configuration_id='codec-v1',
            solution_payloads=(b'payload',),
        )
