"""Tests for cross-run persistence of promoted composition definitions.

Created: 2026-11-10T00:00:00+01:00
Author: Michel Bierlaire, EPFL
"""

import json
from pathlib import Path

import pytest

from multiobjective_vns.promoted_catalog import (
    PromotedCatalogEntry,
    PromotedCompositionCatalog,
    PromotedCompositionCatalogError,
)

COMPATIBILITY: dict[str, object] = {
    'objectives': [
        {'objective_id': 'cost', 'configuration_id': 'cost-v1'},
    ],
    'operators': [
        {
            'catalog_kind': 'ordinary',
            'operator_id': 'remove',
            'configuration_id': 'remove-v1',
            'component_operator_ids': None,
            'max_neighborhood_size': 3,
            'feasibility_restoration': False,
            'modification_count_definition': 'removed decisions',
        },
    ],
    'operator_groups': [],
}

ENTRY = PromotedCatalogEntry(
    operator_id_text='multiobjective-vns:promoted-operator:["[\\"remove\\",\\"repair\\"]",0]',
    component_operator_id_texts=('remove', 'repair'),
    component_configuration_id_texts=('remove-v1', 'repair-v1'),
    max_neighborhood_size=3,
    feasibility_restoration=True,
    inherited_group_id_texts=('destroy-repair',),
    modification_count_definition='minimum of component counts',
)


def test_missing_file_loads_empty_and_save_round_trips(tmp_path: Path) -> None:
    path = tmp_path / 'promotions.json'
    catalog = PromotedCompositionCatalog(path)

    assert catalog.load('instance-a', COMPATIBILITY) == ()

    catalog.save('instance-a', COMPATIBILITY, (ENTRY,))

    assert catalog.load('instance-a', COMPATIBILITY) == (ENTRY,)
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['format_version'] == 1
    assert tuple(saved['catalogs']) == ('instance-a',)


def test_save_preserves_catalogs_for_other_problem_instances(tmp_path: Path) -> None:
    catalog = PromotedCompositionCatalog(tmp_path / 'promotions.json')
    catalog.save('instance-a', COMPATIBILITY, (ENTRY,))
    catalog.save('instance-b', COMPATIBILITY, ())

    assert catalog.load('instance-a', COMPATIBILITY) == (ENTRY,)
    assert catalog.load('instance-b', COMPATIBILITY) == ()


def test_load_rejects_compatibility_mismatch(tmp_path: Path) -> None:
    catalog = PromotedCompositionCatalog(tmp_path / 'promotions.json')
    catalog.save('instance-a', COMPATIBILITY, (ENTRY,))

    with pytest.raises(PromotedCompositionCatalogError, match='compatibility'):
        catalog.load('instance-a', {**COMPATIBILITY, 'operator_groups': [['changed']]})


@pytest.mark.parametrize(
    ('contents', 'message'),
    (
        ('not JSON', 'valid JSON'),
        ('{"format_version": 99, "catalogs": {}}', 'format_version'),
        ('{"format_version": 1, "catalogs": []}', 'catalogs'),
    ),
)
def test_load_rejects_malformed_or_unsupported_files(
    tmp_path: Path,
    contents: str,
    message: str,
) -> None:
    path = tmp_path / 'promotions.json'
    path.write_text(contents, encoding='utf-8')

    with pytest.raises(PromotedCompositionCatalogError, match=message):
        PromotedCompositionCatalog(path).load('instance-a', COMPATIBILITY)


def test_none_path_disables_file_persistence() -> None:
    catalog = PromotedCompositionCatalog(None)

    assert catalog.load('instance-a', COMPATIBILITY) == ()
    catalog.save('instance-a', COMPATIBILITY, (ENTRY,))
