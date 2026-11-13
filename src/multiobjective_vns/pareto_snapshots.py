"""Read and atomically publish encoded Pareto-set snapshots.

Created: 2026-11-10T19:31:34+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ParetoSnapshotData:
    """Versioned metadata and application-encoded Pareto solutions."""

    problem_instance_id: str
    objective_ids: tuple[str, ...]
    objective_configuration_ids: tuple[str, ...]
    solution_codec_component_id: str
    solution_codec_configuration_id: str
    solution_payloads: tuple[bytes, ...]

    def __post_init__(self) -> None:
        for name in (
            'problem_instance_id',
            'solution_codec_component_id',
            'solution_codec_configuration_id',
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f'{name} must be a nonempty string')
        if not isinstance(self.objective_ids, tuple) or not self.objective_ids:
            raise ValueError('objective_ids must be a nonempty tuple')
        if not isinstance(self.objective_configuration_ids, tuple):
            raise TypeError('objective_configuration_ids must be a tuple')
        if len(self.objective_ids) != len(self.objective_configuration_ids):
            raise ValueError(
                'objective ID and configuration tuples must have same length'
            )
        for name, values in (
            ('objective_ids', self.objective_ids),
            ('objective_configuration_ids', self.objective_configuration_ids),
        ):
            if any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f'{name} must contain nonempty strings')
        if not isinstance(self.solution_payloads, tuple) or not self.solution_payloads:
            raise ValueError('solution_payloads must be a nonempty tuple')
        if any(type(payload) is not bytes for payload in self.solution_payloads):
            raise TypeError('solution_payloads must contain only bytes')


_FORMAT = 'multiobjective-vns-pareto-snapshot'
_FORMAT_VERSION = 1
_EXPECTED_FIELDS = frozenset(
    {
        'format',
        'format_version',
        'problem_instance_id',
        'objective_ids',
        'objective_configuration_ids',
        'solution_codec_component_id',
        'solution_codec_configuration_id',
        'solution_payloads',
    }
)


def write_pareto_snapshot(path: Path, snapshot: ParetoSnapshotData) -> None:
    """Atomically write one UTF-8 JSON Pareto snapshot.

    :param path: Destination file path.
    :param snapshot: Metadata and application-encoded solutions to publish.
    :raises OSError: If the destination cannot be written or replaced.
    """
    if not isinstance(path, Path):
        raise TypeError('path must be a Path')
    if not isinstance(snapshot, ParetoSnapshotData):
        raise TypeError('snapshot must be ParetoSnapshotData')
    document = {
        'format': _FORMAT,
        'format_version': _FORMAT_VERSION,
        'problem_instance_id': snapshot.problem_instance_id,
        'objective_ids': list(snapshot.objective_ids),
        'objective_configuration_ids': list(snapshot.objective_configuration_ids),
        'solution_codec_component_id': snapshot.solution_codec_component_id,
        'solution_codec_configuration_id': snapshot.solution_codec_configuration_id,
        'solution_payloads': [
            base64.b64encode(payload).decode('ascii')
            for payload in snapshot.solution_payloads
        ],
    }
    serialized = json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        separators=(',', ':'),
        sort_keys=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f'.{path.name}.',
        suffix='.tmp',
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(serialized)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def read_pareto_snapshot(path: Path) -> ParetoSnapshotData:
    """Read and validate one versioned Pareto snapshot.

    :param path: Snapshot file path.
    :returns: Its metadata and decoded application payload bytes.
    :raises OSError: If the file cannot be read.
    :raises ValueError: If its JSON structure or version is invalid.
    """
    if not isinstance(path, Path):
        raise TypeError('path must be a Path')
    document = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or set(document) != _EXPECTED_FIELDS:
        raise ValueError('Pareto snapshot fields do not match the version-1 format')
    if document['format'] != _FORMAT:
        raise ValueError('unsupported Pareto snapshot format')
    if type(document['format_version']) is not int or document['format_version'] != 1:
        raise ValueError('unsupported Pareto snapshot version')
    objective_ids = document['objective_ids']
    objective_configurations = document['objective_configuration_ids']
    encoded_payloads = document['solution_payloads']
    if not isinstance(objective_ids, list) or not isinstance(
        objective_configurations, list
    ):
        raise ValueError('Pareto snapshot objective fields must be arrays')
    if not isinstance(encoded_payloads, list):
        raise ValueError('Pareto snapshot solution_payloads must be an array')
    try:
        payloads = tuple(
            base64.b64decode(value, validate=True)
            for value in encoded_payloads
            if isinstance(value, str)
        )
    except (binascii.Error, ValueError) as error:
        raise ValueError(
            'Pareto snapshot contains invalid base64 payload data'
        ) from error
    if len(payloads) != len(encoded_payloads):
        raise ValueError('Pareto snapshot payload entries must be strings')
    return ParetoSnapshotData(
        problem_instance_id=document['problem_instance_id'],
        objective_ids=tuple(objective_ids),
        objective_configuration_ids=tuple(objective_configurations),
        solution_codec_component_id=document['solution_codec_component_id'],
        solution_codec_configuration_id=document['solution_codec_configuration_id'],
        solution_payloads=payloads,
    )


__all__ = [
    'ParetoSnapshotData',
    'read_pareto_snapshot',
    'write_pareto_snapshot',
]
