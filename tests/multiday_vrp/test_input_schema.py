"""Tests for the version-one VRP input schema.

Created: 2026-11-03T17:15:00+01:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.multiday_vrp.input_schema import (
    ALLOWED_FILE_KEYS,
    BASE_FILE_KEYS,
    FILE_COLUMNS,
    FILE_NAMES,
    MANIFEST_OPTIONAL,
    MANIFEST_REQUIRED,
)


def test_manifest_and_file_key_sets_are_consistent() -> None:
    assert MANIFEST_REQUIRED.isdisjoint(MANIFEST_OPTIONAL)
    assert 'exact_active_vehicle_count' in MANIFEST_OPTIONAL
    assert 'node_coordinate_system_id' in MANIFEST_OPTIONAL
    assert 'node_coordinate_unit' in MANIFEST_OPTIONAL
    assert BASE_FILE_KEYS - {'objectives'} <= FILE_COLUMNS.keys()
    assert 'objectives' in BASE_FILE_KEYS
    assert ALLOWED_FILE_KEYS == FILE_COLUMNS.keys() | {'objectives'}


def test_csv_schemas_have_unique_nonempty_column_names() -> None:
    for table_name, columns in FILE_COLUMNS.items():
        assert table_name
        assert columns
        assert all(column for column in columns)
        assert len(columns) == len(set(columns))


def test_optional_file_name_overrides_reference_known_tables() -> None:
    assert FILE_NAMES
    assert FILE_NAMES.keys() <= FILE_COLUMNS.keys()
    for table_name, filename in FILE_NAMES.items():
        assert table_name
        assert filename.endswith('.csv')


def test_node_coordinate_csv_schema_is_registered() -> None:
    assert FILE_COLUMNS['node_coordinates'] == ('node_id', 'x', 'y')
    assert FILE_NAMES['node_coordinates'] == 'node-coordinates.csv'


@pytest.mark.parametrize('key', ['nodes', 'arcs', 'depots', 'vehicles', 'personnel'])
def test_core_catalogs_are_required_files(key: str) -> None:
    assert key in BASE_FILE_KEYS
    assert key in ALLOWED_FILE_KEYS
