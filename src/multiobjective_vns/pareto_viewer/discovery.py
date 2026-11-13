"""Discover invocation-specific viewer exports and choose their best scope.

Created: 2026-11-12T12:44:55+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from multiobjective_vns.pareto_viewer.data import RecordScope, read_view_export


@dataclass(frozen=True, slots=True)
class ViewSource:
    """Preferred available viewer file and its sibling scopes."""

    invocation_id: str
    path: Path
    record_scope: RecordScope
    registry_path: Path | None
    pareto_path: Path | None


def discover_view_sources(directory: Path) -> tuple[ViewSource, ...]:
    """Find one preferred export source for each invocation in a directory.

    :param directory: Directory scanned for the package's two view filenames.
    :returns: Sources ordered by invocation ID.
    :raises OSError: If a matching export cannot be read.
    :raises ValueError: If a matching export is malformed.
    """
    if not isinstance(directory, Path):
        raise TypeError('directory must be Path')
    grouped: dict[str, dict[str, Path]] = {}
    pattern_scopes = (
        ('*.registry-view.json', 'registry'),
        ('*.pareto-view.json', 'pareto'),
    )
    for pattern, pattern_scope in pattern_scopes:
        for path in sorted(directory.glob(pattern)):
            export = read_view_export(path)
            if export.record_scope != pattern_scope:
                raise ValueError(f'{path} filename scope disagrees with its content')
            grouped.setdefault(export.invocation_id, {})[pattern_scope] = path
    sources: list[ViewSource] = []
    for invocation_id, paths in sorted(grouped.items()):
        registry_path = paths.get('registry')
        pareto_path = paths.get('pareto')
        selected = registry_path or pareto_path
        if selected is None:
            continue
        scope: RecordScope = 'registry' if registry_path is not None else 'pareto'
        sources.append(
            ViewSource(
                invocation_id,
                selected,
                scope,
                registry_path,
                pareto_path,
            )
        )
    return tuple(sources)


def preferred_view_path(path: Path) -> Path:
    """Choose registry scope for the invocation when its sibling is present.

    :param path: Explicitly selected viewer export.
    :returns: Registry sibling if available, otherwise ``path``.
    :raises OSError: If the selected file cannot be read.
    :raises ValueError: If the selected file is malformed.
    """
    if not isinstance(path, Path):
        raise TypeError('path must be Path')
    export = read_view_export(path)
    registry_path = path.parent / f'{export.invocation_id}.registry-view.json'
    if registry_path.exists():
        registry = read_view_export(registry_path)
        if registry.record_scope != 'registry':
            raise ValueError('registry filename contains a non-registry export')
        if registry.invocation_id != export.invocation_id:
            raise ValueError('sibling registry export belongs to another invocation')
        return registry_path
    return path


def refresh_view_source(source: ViewSource) -> ViewSource:
    """Refresh a source and switch to registry scope when it becomes available."""
    if not isinstance(source, ViewSource):
        raise TypeError('source must be ViewSource')
    current = preferred_view_path(source.path)
    export = read_view_export(current)
    registry_path = source.registry_path
    pareto_path = source.pareto_path
    if current.name.endswith('.registry-view.json'):
        registry_path = current
    elif current.name.endswith('.pareto-view.json'):
        pareto_path = current
    expected_registry = current.parent / f'{source.invocation_id}.registry-view.json'
    if expected_registry.exists():
        registry_path = expected_registry
    scope: RecordScope = 'registry' if registry_path is not None else 'pareto'
    chosen = registry_path if registry_path is not None else pareto_path
    if chosen is None:
        raise ValueError('viewer source has no available export')
    if export.invocation_id != source.invocation_id:
        raise ValueError('refreshed export belongs to another invocation')
    return ViewSource(source.invocation_id, chosen, scope, registry_path, pareto_path)


__all__ = [
    'ViewSource',
    'discover_view_sources',
    'preferred_view_path',
    'refresh_view_source',
]
