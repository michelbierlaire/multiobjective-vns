"""Parse and validate optional planar node coordinates.

Created: 2026-11-12T16:00:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path

from multiobjective_vns.multiday_vrp.input_errors import VrpInputError
from multiobjective_vns.multiday_vrp.input_records import (
    NodeCoordinateRecord,
    VrpInterventionData,
    VrpNetworkData,
    VrpPackageData,
    VrpSpatialData,
)
from multiobjective_vns.multiday_vrp.input_utils import CsvRow
from multiobjective_vns.multiday_vrp.input_validation import (
    _float_field,
    _foreign,
    _id_field,
    _table_error,
    _toml_string,
    _unique,
)

__all__ = ['_parse_spatial_data']


def _parse_spatial_data(
    manifest: dict[str, object],
    rows: tuple[CsvRow, ...],
    path: Path | None,
    network: VrpNetworkData,
    packages: VrpPackageData,
    interventions: VrpInterventionData,
) -> VrpSpatialData:
    """Parse optional coordinates and enforce required-site coverage.

    :param manifest: Parsed instance manifest.
    :param rows: Coordinate-file rows, or an empty tuple when omitted.
    :param path: Coordinate-file path, or ``None`` when the file is omitted.
    :param network: Validated road and depot data.
    :param packages: Validated package locations.
    :param interventions: Validated intervention locations.
    :returns: Normalized spatial data, empty when no file is supplied.
    :raises VrpInputError: If metadata, coordinates, references, or coverage
        violate the input contract.
    """
    metadata_keys = ('node_coordinate_system_id', 'node_coordinate_unit')
    if path is None:
        if any(key in manifest for key in metadata_keys):
            raise VrpInputError(
                file='instance.toml',
                column=metadata_keys[0],
                reason='coordinate metadata requires files.node_coordinates',
            )
        return VrpSpatialData(None, None, ())

    coordinate_system_id = _toml_string(
        manifest, 'node_coordinate_system_id', 'instance.toml'
    )
    coordinate_unit = _toml_string(manifest, 'node_coordinate_unit', 'instance.toml')
    for name, value in (
        ('node_coordinate_system_id', coordinate_system_id),
        ('node_coordinate_unit', coordinate_unit),
    ):
        if not value.strip():
            raise VrpInputError(
                file='instance.toml',
                column=name,
                reason='must be a nonempty string',
            )

    records = tuple(
        NodeCoordinateRecord(
            node_id=_id_field(row, 'node_id', path, row_number),
            x=_float_field(row, 'x', path, row_number),
            y=_float_field(row, 'y', path, row_number),
        )
        for row_number, row in enumerate(rows, start=2)
    )
    _unique((item.node_id for item in records), path, 'node_id')
    known_nodes = {item.node_id for item in network.nodes}
    _foreign((item.node_id for item in records), known_nodes, path, 'node_id')

    required_nodes = {item.node_id for item in network.depots}
    required_nodes.update(
        node_id
        for package in packages.packages
        for node_id in (package.pickup_node_id, package.delivery_node_id)
    )
    required_nodes.update(item.node_id for item in interventions.interventions)
    required_nodes.update(item.node_id for item in network.transfer_facilities)
    coordinate_nodes = {item.node_id for item in records}
    missing_nodes = required_nodes - coordinate_nodes
    if missing_nodes:
        missing = min(missing_nodes)
        raise _table_error(path, f'missing coordinates for required node {missing!r}')

    return VrpSpatialData(coordinate_system_id, coordinate_unit, records)
