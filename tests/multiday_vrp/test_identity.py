"""Tests for canonical multi-day VRP solution identity.

Created: 2026-11-03T23:52:10+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from math import inf, nan
from pathlib import Path

import pytest

from multiobjective_vns.multiday_vrp import VrpSolutionIdentity, load_vrp_instance
from multiobjective_vns.multiday_vrp.solution import (
    ActivityRef,
    ActivityStop,
    RouteLeg,
    VehicleDayRoute,
    VrpSolution,
)

from .input_fixtures import write_minimal_vrp_bundle, write_pickup_symmetry_bundle


def _solution(
    order: tuple[str, str] = ('package-a', 'package-b'),
    *,
    start_time: float = 0.0,
    waits: tuple[float, float] = (0.0, 0.0),
    pre_service_waits: tuple[float, float] = (0.0, 0.0),
    break_durations: tuple[float, float] = (0.0, 0.0),
    team_assignments: tuple[frozenset[str], frozenset[str]] = (
        frozenset(),
        frozenset(),
    ),
    tour_ends: tuple[bool, bool] = (False, False),
    internal_arcs: tuple[str, ...] = (),
    intervention_between: bool = False,
) -> VrpSolution:
    stops = [
        ActivityStop(
            ActivityRef('pickup', package_id),
            team_assignments[index],
            pre_service_waits[index],
            waits[index],
            break_durations[index],
            tour_ends[index],
        )
        for index, package_id in enumerate(order)
    ]
    if intervention_between:
        stops.insert(
            1,
            ActivityStop(
                ActivityRef('intervention', 'service'),
                frozenset(),
                0.0,
                0.0,
                0.0,
                False,
            ),
        )
    route = VehicleDayRoute(
        'vehicle-1',
        1,
        'depot-1',
        start_time,
        'driver-1',
        tuple(stops),
        (
            RouteLeg(('arc-from-depot',)),
            *(
                RouteLeg(internal_arcs if index == 0 else ())
                for index in range(len(stops) - 1)
            ),
            RouteLeg(('arc-to-depot',)),
        ),
    )
    return VrpSolution((route,), ())


def test_declared_pickup_permutations_share_identity(tmp_path: Path) -> None:
    instance = load_vrp_instance(write_pickup_symmetry_bundle(tmp_path))
    identity = VrpSolutionIdentity(instance)

    assert identity.equivalent(_solution(), _solution(('package-b', 'package-a')))


def test_lookup_key_matches_declared_pickup_symmetries_and_distinguishes_routes(
    tmp_path: Path,
) -> None:
    instance = load_vrp_instance(write_pickup_symmetry_bundle(tmp_path))
    identity = VrpSolutionIdentity(instance)

    assert identity.lookup_key(_solution()) == identity.lookup_key(
        _solution(('package-b', 'package-a'))
    )
    assert identity.lookup_key(_solution()) != identity.lookup_key(
        _solution(start_time=1.0)
    )


@pytest.mark.parametrize(
    ('left', 'right', 'expected_equal'),
    [(nan, nan, True), (0.0, -0.0, True), (inf, -inf, False)],
)
def test_lookup_key_matches_identity_float_rules(
    tmp_path: Path,
    left: float,
    right: float,
    expected_equal: bool,
) -> None:
    identity = VrpSolutionIdentity(
        load_vrp_instance(write_pickup_symmetry_bundle(tmp_path))
    )

    same_key = identity.lookup_key(_solution(start_time=left)) == identity.lookup_key(
        _solution(start_time=right)
    )
    assert same_key is expected_equal
    same_stop_key = identity.lookup_key(
        _solution(waits=(left, 0.0))
    ) == identity.lookup_key(_solution(waits=(right, 0.0)))
    assert same_stop_key is expected_equal


def test_no_group_preserves_exact_route_order(tmp_path: Path) -> None:
    instance = load_vrp_instance(write_minimal_vrp_bundle(tmp_path))
    identity = VrpSolutionIdentity(instance)

    assert not identity.equivalent(_solution(), _solution(('package-b', 'package-a')))


def test_pickup_permutation_canonicalizes_complete_records_with_waits_and_breaks(
    tmp_path: Path,
) -> None:
    identity = VrpSolutionIdentity(
        load_vrp_instance(write_pickup_symmetry_bundle(tmp_path))
    )
    first = _solution(
        waits=(1.0, 2.0),
        pre_service_waits=(0.5, 1.5),
        break_durations=(3.0, 4.0),
        team_assignments=(frozenset({'person-a'}), frozenset({'person-b'})),
    )
    second = _solution(
        ('package-b', 'package-a'),
        waits=(2.0, 1.0),
        pre_service_waits=(1.5, 0.5),
        break_durations=(4.0, 3.0),
        team_assignments=(frozenset({'person-b'}), frozenset({'person-a'})),
    )

    assert identity.equivalent(first, second)
    assert identity.lookup_key(first) == identity.lookup_key(second)


def test_pickup_timing_remains_attached_to_its_package(tmp_path: Path) -> None:
    identity = VrpSolutionIdentity(
        load_vrp_instance(write_pickup_symmetry_bundle(tmp_path))
    )
    first = _solution(waits=(1.0, 2.0))
    reversed_order_with_different_package_waits = _solution(
        ('package-b', 'package-a'),
        waits=(1.0, 2.0),
    )

    assert not identity.equivalent(first, reversed_order_with_different_package_waits)


@pytest.mark.parametrize(
    ('internal_arcs', 'intervention_between', 'tour_ends'),
    [
        (('arc-inside',), False, (False, False)),
        ((), True, (False, False)),
        ((), False, (True, False)),
    ],
)
def test_pickup_order_is_preserved_across_route_boundaries(
    tmp_path: Path,
    internal_arcs: tuple[str, ...],
    intervention_between: bool,
    tour_ends: tuple[bool, bool],
) -> None:
    identity = VrpSolutionIdentity(
        load_vrp_instance(write_pickup_symmetry_bundle(tmp_path))
    )
    first = _solution(
        internal_arcs=internal_arcs,
        intervention_between=intervention_between,
        tour_ends=tour_ends,
    )
    second = _solution(
        ('package-b', 'package-a'),
        internal_arcs=internal_arcs,
        intervention_between=intervention_between,
        tour_ends=tour_ends,
    )

    assert not identity.equivalent(first, second)


def test_non_pickup_fields_remain_part_of_identity(tmp_path: Path) -> None:
    instance = load_vrp_instance(write_pickup_symmetry_bundle(tmp_path))
    identity = VrpSolutionIdentity(instance)

    assert not identity.equivalent(_solution(), _solution(start_time=0.5))


@pytest.mark.parametrize(
    ('left', 'right', 'expected'),
    [
        (nan, nan, True),
        (inf, inf, True),
        (inf, -inf, False),
        (0.0, -0.0, True),
    ],
)
def test_identity_has_deterministic_float_special_value_rules(
    tmp_path: Path, left: float, right: float, expected: bool
) -> None:
    instance = load_vrp_instance(write_pickup_symmetry_bundle(tmp_path))
    identity = VrpSolutionIdentity(instance)

    assert (
        identity.equivalent(_solution(start_time=left), _solution(start_time=right))
        is expected
    )


def test_identity_requires_exact_vrp_solution_root_type(tmp_path: Path) -> None:
    instance = load_vrp_instance(write_pickup_symmetry_bundle(tmp_path))

    with pytest.raises(TypeError, match='VrpSolution'):
        VrpSolutionIdentity(instance).equivalent(_solution(), ())


def test_identity_requires_a_loaded_instance(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match='LoadedVrpInstance'):
        VrpSolutionIdentity(object())  # type: ignore[arg-type]


def test_identity_exposes_canonical_checkpoint_configuration(tmp_path: Path) -> None:
    identity = VrpSolutionIdentity(
        load_vrp_instance(write_pickup_symmetry_bundle(tmp_path))
    )

    assert identity.component_id == 'multiobjective-vns:multiday-vrp:solution-identity'
    assert identity.configuration_id == 'multiday-vrp-canonical-pickup-identity-v4'
    assert identity.compatibility_configuration_id_aliases == {
        'multiday-vrp-canonical-pickup-identity-v2': (
            'multiday-vrp-canonical-pickup-identity-v4'
        ),
        'multiday-vrp-canonical-pickup-identity-v3': (
            'multiday-vrp-canonical-pickup-identity-v4'
        ),
    }
