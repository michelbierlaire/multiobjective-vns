"""Tests for optional VRP node-coordinate input.

Created: 2026-11-12T16:00:00+01:00
Author: Michel Bierlaire, EPFL
"""

from pathlib import Path

import pytest

from multiobjective_vns.multiday_vrp import VrpInputError, load_vrp_instance

from .input_fixtures import write_minimal_vrp_bundle


def _write_coordinate_file(
    root: Path,
    rows: tuple[str, ...],
    *,
    metadata: bool = True,
) -> Path:
    manifest = write_minimal_vrp_bundle(root)
    source = manifest.read_text(encoding='utf-8')
    if metadata:
        source = source.replace(
            "hos_regime_version = 'none'\n\n[files]",
            "hos_regime_version = 'none'\n"
            "node_coordinate_system_id = 'test-local-grid'\n"
            "node_coordinate_unit = 'km'\n\n[files]",
        )
    source = source.replace(
        "nodes = 'nodes.csv'\n",
        "nodes = 'nodes.csv'\nnode_coordinates = 'node-coordinates.csv'\n",
    )
    manifest.write_text(source, encoding='utf-8')
    (manifest.parent / 'node-coordinates.csv').write_text(
        'node_id,x,y\n' + ''.join(f'{row}\n' for row in rows),
        encoding='utf-8',
    )
    return manifest


def test_loader_reads_spatial_metadata_and_coordinates(tmp_path: Path) -> None:
    manifest = _write_coordinate_file(tmp_path, ('node-a,1.25,-3.5',))

    instance = load_vrp_instance(manifest)

    assert instance.spatial_data.coordinate_system_id == 'test-local-grid'
    assert instance.spatial_data.coordinate_unit == 'km'
    assert instance.spatial_data.node_coordinates == (('node-a', 1.25, -3.5),)
    assert 'planar_node_coordinates' in instance.feature_profile.active_features


def test_loader_uses_empty_spatial_data_when_file_is_absent(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)

    instance = load_vrp_instance(manifest)

    assert instance.spatial_data.coordinate_system_id is None
    assert instance.spatial_data.coordinate_unit is None
    assert instance.spatial_data.node_coordinates == ()
    assert 'planar_node_coordinates' not in instance.feature_profile.active_features


def test_coordinate_file_requires_both_manifest_metadata_values(
    tmp_path: Path,
) -> None:
    manifest = _write_coordinate_file(
        tmp_path,
        ('node-a,0.0,0.0',),
        metadata=False,
    )

    with pytest.raises(VrpInputError):
        load_vrp_instance(manifest)


def test_coordinate_metadata_requires_a_coordinate_file(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    source = manifest.read_text(encoding='utf-8').replace(
        "hos_regime_version = 'none'\n",
        "hos_regime_version = 'none'\n"
        "node_coordinate_system_id = 'test-local-grid'\n"
        "node_coordinate_unit = 'km'\n",
    )
    manifest.write_text(source, encoding='utf-8')

    with pytest.raises(VrpInputError):
        load_vrp_instance(manifest)


@pytest.mark.parametrize(
    'rows',
    (
        (),
        ('unknown-node,0.0,0.0',),
        ('node-a,0.0,0.0', 'node-a,1.0,1.0'),
        ('node-a,nan,0.0',),
    ),
)
def test_loader_rejects_incomplete_unknown_duplicate_or_nonfinite_coordinates(
    tmp_path: Path,
    rows: tuple[str, ...],
) -> None:
    manifest = _write_coordinate_file(tmp_path, rows)

    with pytest.raises(VrpInputError):
        load_vrp_instance(manifest)
