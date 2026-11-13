"""Tests for directed route-path search primitives.

Created: 2026-11-03T19:15:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.multiday_vrp.input_loader import load_vrp_instance
from multiobjective_vns.multiday_vrp.input_records import (
    ArcRecord,
    LoadedVrpInstance,
    NodeRecord,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    build_path_search_index,
    rebuild_changed_routes,
    route_node_sequence,
    sample_loopless_paths,
)
from multiobjective_vns.multiday_vrp.solution import (
    DepotOnlyStop,
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.random_source import RandomSource

from .input_fixtures import write_minimal_vrp_bundle


class _SequenceRandomSource:
    def __init__(self, values: tuple[float, ...] = ()) -> None:
        self._values: Iterator[float] = iter(values)

    def random(self) -> float:
        return next(self._values)


def _random_source(*values: float) -> RandomSource:
    return cast(RandomSource, _SequenceRandomSource(values))


def _instance_with_graph(
    root: Path, arcs: tuple[ArcRecord, ...], nodes: tuple[str, ...]
) -> LoadedVrpInstance:
    manifest = write_minimal_vrp_bundle(root)
    instance = load_vrp_instance(manifest)
    network = instance.network_data._replace(
        nodes=tuple(NodeRecord(node_id) for node_id in nodes),
        arcs=arcs,
    )
    return instance._replace(network_data=network)


def _branching_instance(root: Path) -> LoadedVrpInstance:
    return _instance_with_graph(
        root,
        (
            ArcRecord('ab', 'a', 'b'),
            ArcRecord('ac', 'a', 'c'),
            ArcRecord('ba', 'b', 'a'),
            ArcRecord('bd', 'b', 'd'),
            ArcRecord('cd', 'c', 'd'),
        ),
        ('a', 'b', 'c', 'd'),
    )


def test_path_search_returns_all_bounded_loopless_directed_paths(
    tmp_path: Path,
) -> None:
    instance = _branching_instance(tmp_path)

    paths = sample_loopless_paths(
        instance,
        'a',
        'd',
        random_source=_random_source(0.0, 0.0, 0.0, 0.0),
        max_paths=8,
        max_arc_expansion_steps=100,
    )

    assert paths == (('ab', 'bd'), ('ac', 'cd'))
    assert all(
        len(set(_nodes_for_path(instance, 'a', path))) == len(path) + 1
        for path in paths
    )


def test_path_search_index_preserves_sampled_paths(tmp_path: Path) -> None:
    instance = _branching_instance(tmp_path)
    limits = {'max_paths': 8, 'max_arc_expansion_steps': 100}

    without_index = sample_loopless_paths(
        instance,
        'a',
        'd',
        random_source=_random_source(*((0.0,) * 100)),
        **limits,
    )
    with_index = sample_loopless_paths(
        instance,
        'a',
        'd',
        random_source=_random_source(*((0.0,) * 100)),
        path_search_index=build_path_search_index(instance),
        **limits,
    )

    assert with_index == without_index


def test_path_search_keeps_parallel_arcs_as_distinct_paths(tmp_path: Path) -> None:
    instance = _instance_with_graph(
        tmp_path,
        (
            ArcRecord('a-1', 'a', 'b'),
            ArcRecord('a-2', 'a', 'b'),
            ArcRecord('b-d', 'b', 'd'),
        ),
        ('a', 'b', 'd'),
    )

    paths = sample_loopless_paths(
        instance,
        'a',
        'd',
        random_source=_random_source(0.0, 0.0),
        max_paths=4,
        max_arc_expansion_steps=20,
    )

    assert paths == (('a-1', 'b-d'), ('a-2', 'b-d'))


def test_path_search_handles_identity_unreachable_and_bounded_results(
    tmp_path: Path,
) -> None:
    instance = _branching_instance(tmp_path)

    assert sample_loopless_paths(
        instance,
        'a',
        'a',
        random_source=_random_source(),
        max_paths=3,
        max_arc_expansion_steps=10,
    ) == ((),)
    assert (
        sample_loopless_paths(
            instance,
            'd',
            'a',
            random_source=_random_source(),
            max_paths=3,
            max_arc_expansion_steps=10,
        )
        == ()
    )
    assert (
        len(
            sample_loopless_paths(
                instance,
                'a',
                'd',
                random_source=_random_source(0.0, 0.0, 0.0, 0.0),
                max_paths=1,
                max_arc_expansion_steps=100,
            )
        )
        == 1
    )


def test_path_search_counts_expansion_before_loop_filtering(tmp_path: Path) -> None:
    instance = _branching_instance(tmp_path)

    paths = sample_loopless_paths(
        instance,
        'a',
        'd',
        random_source=_random_source(0.0),
        max_paths=4,
        max_arc_expansion_steps=1,
    )

    assert paths == ()


@pytest.mark.parametrize(('max_paths', 'steps'), [(0, 1), (1, 0)])
def test_path_search_requires_positive_limits(
    tmp_path: Path, max_paths: int, steps: int
) -> None:
    instance = _branching_instance(tmp_path)

    with pytest.raises(ValueError):
        sample_loopless_paths(
            instance,
            'a',
            'd',
            random_source=_random_source(0.0, 0.0, 0.0, 0.0),
            max_paths=max_paths,
            max_arc_expansion_steps=steps,
        )


def _nodes_for_path(
    instance: LoadedVrpInstance, origin: str, path: tuple[str, ...]
) -> tuple[str, ...]:
    arc_by_id = {arc.arc_id: arc for arc in instance.network_data.arcs}
    nodes = [origin]
    for arc_id in path:
        nodes.append(arc_by_id[arc_id].head_node_id)
    return tuple(nodes)


def test_route_node_sequence_carries_the_previous_overnight_depot(
    tmp_path: Path,
) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    instance_dir = manifest.parent
    (instance_dir / 'depots.csv').write_text(
        'depot_id,node_id\ndepot-1,node-a\ndepot-2,node-b\n', encoding='utf-8'
    )
    (instance_dir / 'vehicle-overnight-depots.csv').write_text(
        'vehicle_id,depot_id\nvehicle-1,depot-1\nvehicle-1,depot-2\n',
        encoding='utf-8',
    )
    instance = load_vrp_instance(manifest)
    first = VehicleDayRoute(
        'vehicle-1', 1, 'depot-2', 0.0, 'driver-1', (), (RouteLeg(()),)
    )
    second = VehicleDayRoute(
        'vehicle-1', 2, 'depot-1', 0.0, 'driver-1', (), (RouteLeg(()),)
    )
    solution = VrpSolution((first, second), ())

    assert route_node_sequence(instance, solution, first) == ('node-a', 'node-b')
    assert route_node_sequence(instance, solution, second) == ('node-b', 'node-a')


def test_route_node_sequence_includes_stop_locations(tmp_path: Path) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    (manifest.parent / 'depots.csv').write_text(
        'depot_id,node_id\ndepot-1,node-a\ndepot-2,node-b\n', encoding='utf-8'
    )
    instance = load_vrp_instance(manifest)
    route = VehicleDayRoute(
        'vehicle-1',
        1,
        'depot-1',
        0.0,
        'driver-1',
        (DepotOnlyStop('node-b', 0.0, 0.0, 0.0, False),),
        (RouteLeg(()), RouteLeg(())),
    )
    solution = VrpSolution((route,), ())

    assert route_node_sequence(instance, solution, route) == (
        'node-a',
        'node-b',
        'node-a',
    )


def test_rebuild_changed_routes_samples_complete_path_assignments(
    tmp_path: Path,
) -> None:
    manifest = write_minimal_vrp_bundle(tmp_path)
    instance_dir = manifest.parent
    (instance_dir / 'depots.csv').write_text(
        'depot_id,node_id\ndepot-1,node-a\ndepot-2,node-b\n', encoding='utf-8'
    )
    (instance_dir / 'vehicle-overnight-depots.csv').write_text(
        'vehicle_id,depot_id\nvehicle-1,depot-1\nvehicle-1,depot-2\n',
        encoding='utf-8',
    )
    instance = load_vrp_instance(manifest)
    instance = instance._replace(
        network_data=instance.network_data._replace(
            nodes=tuple(
                NodeRecord(node_id) for node_id in ('node-a', 'node-b', 'node-c')
            ),
            arcs=(
                ArcRecord('ab', 'node-a', 'node-b'),
                ArcRecord('ac', 'node-a', 'node-c'),
                ArcRecord('bc', 'node-b', 'node-c'),
                ArcRecord('cb', 'node-c', 'node-b'),
            ),
        )
    )
    route = VehicleDayRoute(
        'vehicle-1',
        1,
        'depot-2',
        0.0,
        'driver-1',
        (DepotOnlyStop('node-b', 0.0, 0.0, 0.0, False),),
        (RouteLeg(('ab',)), RouteLeg(())),
    )
    solution = VrpSolution((route,), ())

    candidates = rebuild_changed_routes(
        instance,
        solution,
        {0: (DepotOnlyStop('node-c', 0.0, 0.0, 0.0, False),)},
        random_source=_random_source(0.0, 0.0, 0.0, 0.0),
        max_paths_per_leg=4,
        max_arc_expansion_steps=20,
        max_candidate_alternatives=4,
    )

    assert tuple(candidate.routes[0].legs for candidate in candidates) == (
        (RouteLeg(('ab', 'bc')), RouteLeg(('cb',))),
        (RouteLeg(('ac',)), RouteLeg(('cb',))),
    )
