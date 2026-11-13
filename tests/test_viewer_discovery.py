"""Tests for invocation-aware Pareto-view file discovery.

Created: 2026-11-12T12:44:55+00:00
Author: Michel Bierlaire, EPFL
"""

import json
from pathlib import Path

from multiobjective_vns.pareto_viewer.discovery import (
    discover_view_sources,
    preferred_view_path,
)


def _write_view(path: Path, invocation_id: str, scope: str) -> None:
    path.write_text(
        json.dumps(
            {
                'format': 'multiobjective-vns-pareto-view',
                'format_version': 1,
                'invocation_id': invocation_id,
                'problem_instance_id': 'problem',
                'record_scope': scope,
                'commit_sequence': 1,
                'is_final': False,
                'objectives': [
                    {
                        'objective_id': 'cost',
                        'label': 'Cost',
                        'direction': 'minimize',
                    }
                ],
                'solutions': [],
            }
        ),
        encoding='utf-8',
    )


def test_discovery_prefers_registry_per_invocation(tmp_path: Path) -> None:
    registry = tmp_path / 'run-a.registry-view.json'
    pareto = tmp_path / 'run-a.pareto-view.json'
    _write_view(registry, 'run-a', 'registry')
    _write_view(pareto, 'run-a', 'pareto')

    sources = discover_view_sources(tmp_path)

    assert len(sources) == 1
    assert sources[0].path == registry
    assert sources[0].record_scope == 'registry'
    assert preferred_view_path(pareto) == registry


def test_discovery_uses_pareto_when_registry_is_unavailable(tmp_path: Path) -> None:
    pareto = tmp_path / 'run-b.pareto-view.json'
    _write_view(pareto, 'run-b', 'pareto')

    sources = discover_view_sources(tmp_path)

    assert len(sources) == 1
    assert sources[0].path == pareto
