"""Persist generated composition Operators between independent searches.

Created: 2026-11-10T08:25:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from multiobjective_vns.records.failures import FailureKind
from multiobjective_vns.values import require_integer


class PromotedCompositionCatalogError(RuntimeError):
    """Invalid or inaccessible promoted-composition catalog file."""

    def __init__(self, message: str, kind: FailureKind) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class PromotedCatalogEntry:
    """Persisted definition of one generated composition Operator."""

    operator_id_text: str
    component_operator_id_texts: tuple[str, str]
    component_configuration_id_texts: tuple[str, str]
    max_neighborhood_size: int
    feasibility_restoration: bool
    inherited_group_id_texts: tuple[str, ...]
    modification_count_definition: str

    def __post_init__(self) -> None:
        _require_text(self.operator_id_text, 'operator_id_text')
        _require_text_pair(
            self.component_operator_id_texts, 'component_operator_id_texts'
        )
        _require_text_pair(
            self.component_configuration_id_texts,
            'component_configuration_id_texts',
        )
        require_integer(
            self.max_neighborhood_size,
            name='max_neighborhood_size',
            minimum=1,
        )
        if not isinstance(self.feasibility_restoration, bool):
            raise TypeError('feasibility_restoration must be bool')
        if not isinstance(self.inherited_group_id_texts, tuple):
            raise TypeError('inherited_group_id_texts must be a tuple')
        for group_id_text in self.inherited_group_id_texts:
            _require_text(group_id_text, 'inherited group ID text')
        if tuple(sorted(set(self.inherited_group_id_texts))) != (
            self.inherited_group_id_texts
        ):
            raise ValueError('inherited group ID texts must be unique and sorted')
        _require_text(self.modification_count_definition, 'modification count')


class PromotedCompositionCatalog:
    """Read and atomically update a versioned JSON catalog file."""

    component_id = 'multiobjective-vns:promoted-composition-catalog'

    def __init__(self, path: Path | None) -> None:
        if path is not None and not isinstance(path, Path):
            raise TypeError('path must be a Path or None')
        self.path = path

    def load(
        self,
        problem_instance_text: str,
        compatibility: Mapping[str, object],
        *,
        configuration_id_aliases: Mapping[str, str] | None = None,
    ) -> tuple[PromotedCatalogEntry, ...]:
        """Load one instance catalog after checking its compatibility record.

        :param problem_instance_text: Stable text for the problem instance ID.
        :param compatibility: JSON-compatible objective and Operator metadata.
        :param configuration_id_aliases: Optional exact configuration-ID
            renames accepted when reading older catalog compatibility records.
        :returns: Promotion entries in the order in which they were created.
        :raises PromotedCompositionCatalogError: If the file is malformed or
            its compatibility declaration differs.
        """
        _require_text(problem_instance_text, 'problem_instance_text')
        expected = _normalize_compatibility(compatibility)
        aliases = _normalize_configuration_id_aliases(configuration_id_aliases)
        if self.path is None:
            return ()
        root = self._read_root()
        catalogs = cast(dict[str, object], root['catalogs'])
        raw_record = catalogs.get(problem_instance_text)
        if raw_record is None:
            return ()
        record = _require_record(raw_record, 'problem catalog')
        if set(record) != {'compatibility', 'promotions'}:
            raise _catalog_contract_error('problem catalog fields are invalid')
        stored_compatibility = _rewrite_configuration_ids(
            record['compatibility'], aliases
        )
        if stored_compatibility != expected:
            raise _catalog_contract_error(
                'promoted composition catalog compatibility differs'
            )
        raw_entries = record['promotions']
        if not isinstance(raw_entries, list):
            raise _catalog_contract_error('promotions must be a JSON array')
        entries = tuple(
            _rewrite_entry_configuration_ids(_decode_entry(raw), aliases)
            for raw in raw_entries
        )
        _validate_entry_sequence(entries)
        return entries

    def save(
        self,
        problem_instance_text: str,
        compatibility: Mapping[str, object],
        entries: tuple[PromotedCatalogEntry, ...],
    ) -> None:
        """Replace this instance's catalog while retaining other instances.

        :param problem_instance_text: Stable text for the problem instance ID.
        :param compatibility: JSON-compatible objective and Operator metadata.
        :param entries: Complete promoted catalog in promotion order.
        :raises PromotedCompositionCatalogError: If the existing file conflicts
            with the record or cannot be replaced atomically.
        """
        _require_text(problem_instance_text, 'problem_instance_text')
        if not isinstance(entries, tuple) or any(
            not isinstance(entry, PromotedCatalogEntry) for entry in entries
        ):
            raise TypeError('entries must be a tuple of PromotedCatalogEntry')
        _validate_entry_sequence(entries)
        expected = _normalize_compatibility(compatibility)
        if self.path is None:
            return
        root = self._read_root()
        catalogs = cast(dict[str, object], root['catalogs'])
        existing = catalogs.get(problem_instance_text)
        if existing is not None:
            existing_record = _require_record(existing, 'problem catalog')
            if existing_record.get('compatibility') != expected:
                raise _catalog_contract_error(
                    'promoted composition catalog compatibility differs'
                )
        catalogs[problem_instance_text] = {
            'compatibility': expected,
            'promotions': [_encode_entry(entry) for entry in entries],
        }
        self._write_root(root)

    def _read_root(self) -> dict[str, object]:
        if self.path is None or not self.path.exists():
            return {'format_version': 1, 'catalogs': {}}
        try:
            contents = self.path.read_text(encoding='utf-8')
        except OSError as error:
            raise PromotedCompositionCatalogError(
                f'promoted composition catalog could not be read: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            ) from error
        try:
            decoded: object = json.loads(contents)
        except json.JSONDecodeError as error:
            raise _catalog_contract_error(
                f'promoted composition catalog is not valid JSON: {error}'
            ) from error
        if not isinstance(decoded, dict) or not all(
            isinstance(key, str) for key in decoded
        ):
            raise _catalog_contract_error('catalog root must be a JSON object')
        root = cast(dict[str, object], decoded)
        if set(root) != {'format_version', 'catalogs'}:
            raise _catalog_contract_error('catalog root fields are invalid')
        version = root['format_version']
        if type(version) is not int or version != 1:
            raise _catalog_contract_error('catalog format_version must be 1')
        catalogs = root['catalogs']
        if not isinstance(catalogs, dict) or not all(
            isinstance(key, str) for key in catalogs
        ):
            raise _catalog_contract_error('catalogs must be a JSON object')
        for record in catalogs.values():
            if not isinstance(record, dict):
                raise _catalog_contract_error(
                    'each problem catalog must be a JSON object'
                )
        return root

    def _write_root(self, root: dict[str, object]) -> None:
        assert self.path is not None
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                dir=self.path.parent,
                prefix=f'.{self.path.name}.',
                suffix='.tmp',
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump(root, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        except (OSError, TypeError, ValueError) as error:
            raise PromotedCompositionCatalogError(
                f'promoted composition catalog could not be written: {error}',
                FailureKind.INFRASTRUCTURE_FAILURE,
            ) from error
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def _normalize_compatibility(
    compatibility: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(compatibility, Mapping):
        raise TypeError('compatibility must be a mapping')
    try:
        serialized = json.dumps(
            dict(compatibility),
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
            allow_nan=False,
        )
        normalized: object = json.loads(serialized)
    except (TypeError, ValueError) as error:
        raise _catalog_contract_error(
            f'compatibility must contain JSON values: {error}'
        ) from error
    if not isinstance(normalized, dict) or not all(
        isinstance(key, str) for key in normalized
    ):
        raise _catalog_contract_error('compatibility must be a JSON object')
    return cast(dict[str, object], normalized)


def _normalize_configuration_id_aliases(
    aliases: Mapping[str, str] | None,
) -> dict[str, str]:
    if aliases is None:
        return {}
    if not isinstance(aliases, Mapping):
        raise TypeError('configuration_id_aliases must be a mapping or None')
    normalized: dict[str, str] = {}
    for old_id, new_id in aliases.items():
        _require_text(old_id, 'old configuration ID')
        _require_text(new_id, 'new configuration ID')
        if old_id == new_id:
            raise ValueError('configuration ID aliases must rename their value')
        normalized[old_id] = new_id
    return normalized


def _rewrite_configuration_ids(
    value: object,
    aliases: Mapping[str, str],
) -> object:
    if isinstance(value, str):
        for old_id, new_id in aliases.items():
            value = value.replace(old_id, new_id)
        return value
    if isinstance(value, list):
        return [_rewrite_configuration_ids(item, aliases) for item in value]
    if isinstance(value, dict):
        return {
            key: _rewrite_configuration_ids(item, aliases)
            for key, item in value.items()
        }
    return value


def _rewrite_entry_configuration_ids(
    entry: PromotedCatalogEntry,
    aliases: Mapping[str, str],
) -> PromotedCatalogEntry:
    if not aliases:
        return entry
    return replace(
        entry,
        component_configuration_id_texts=cast(
            tuple[str, str],
            tuple(
                cast(str, _rewrite_configuration_ids(value, aliases))
                for value in entry.component_configuration_id_texts
            ),
        ),
    )


def _decode_entry(value: object) -> PromotedCatalogEntry:
    record = _require_record(value, 'promotion entry')
    required_fields = {
        'operator_id',
        'component_operator_ids',
        'component_configuration_ids',
        'max_neighborhood_size',
        'feasibility_restoration',
        'inherited_group_ids',
        'modification_count_definition',
    }
    if set(record) != required_fields:
        raise _catalog_contract_error('promotion entry fields are invalid')
    try:
        component_ids = _require_text_array(record['component_operator_ids'], 2)
        configuration_ids = _require_text_array(
            record['component_configuration_ids'], 2
        )
        group_ids = _require_text_array(record['inherited_group_ids'])
        max_neighborhood_size = record['max_neighborhood_size']
        if not isinstance(max_neighborhood_size, int) or isinstance(
            max_neighborhood_size, bool
        ):
            raise TypeError('max_neighborhood_size must be an integer')
        feasibility_restoration = record['feasibility_restoration']
        if not isinstance(feasibility_restoration, bool):
            raise TypeError('feasibility_restoration must be bool')
        return PromotedCatalogEntry(
            operator_id_text=_require_text(record['operator_id'], 'operator_id'),
            component_operator_id_texts=cast(tuple[str, str], component_ids),
            component_configuration_id_texts=cast(tuple[str, str], configuration_ids),
            max_neighborhood_size=max_neighborhood_size,
            feasibility_restoration=feasibility_restoration,
            inherited_group_id_texts=group_ids,
            modification_count_definition=_require_text(
                record['modification_count_definition'],
                'modification_count_definition',
            ),
        )
    except (TypeError, ValueError) as error:
        raise _catalog_contract_error(f'promotion entry is invalid: {error}') from error


def _encode_entry(entry: PromotedCatalogEntry) -> dict[str, object]:
    return {
        'operator_id': entry.operator_id_text,
        'component_operator_ids': list(entry.component_operator_id_texts),
        'component_configuration_ids': list(entry.component_configuration_id_texts),
        'max_neighborhood_size': entry.max_neighborhood_size,
        'feasibility_restoration': entry.feasibility_restoration,
        'inherited_group_ids': list(entry.inherited_group_id_texts),
        'modification_count_definition': entry.modification_count_definition,
    }


def _validate_entry_sequence(entries: tuple[PromotedCatalogEntry, ...]) -> None:
    operator_ids: set[str] = set()
    compositions: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, PromotedCatalogEntry):
            raise TypeError('entries must contain PromotedCatalogEntry values')
        pair = entry.component_operator_id_texts
        if entry.operator_id_text in operator_ids:
            raise ValueError('promoted Operator IDs must be unique')
        if pair in compositions:
            raise ValueError('promoted composition pairs must be unique')
        operator_ids.add(entry.operator_id_text)
        compositions.add(pair)


def _require_record(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _catalog_contract_error(f'{name} must be a JSON object')
    return cast(dict[str, object], value)


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise _catalog_contract_error(f'{name} must be a nonempty string')
    return value


def _require_text_pair(value: object, name: str) -> tuple[str, str]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise TypeError(f'{name} must be a pair')
    return (
        _require_text(value[0], name),
        _require_text(value[1], name),
    )


def _require_text_array(value: object, length: int | None = None) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _catalog_contract_error('identifier fields must be JSON arrays')
    result = tuple(_require_text(item, 'identifier text') for item in value)
    if length is not None and len(result) != length:
        raise _catalog_contract_error('component identifier arrays must have length 2')
    return result


def _catalog_contract_error(message: str) -> PromotedCompositionCatalogError:
    return PromotedCompositionCatalogError(message, FailureKind.CONTRACT_VIOLATION)
