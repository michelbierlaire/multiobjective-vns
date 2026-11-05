"""Tests for knapsack Operator registration and group assembly.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.knapsack.instance import (
    KnapsackInstance,
    KnapsackObjective,
    KnapsackResource,
)
from multiobjective_vns.knapsack.operators.catalog import (
    ProfileRegistration,
    build_operator_catalog,
)
from multiobjective_vns.knapsack.operators.profile import PreferenceProfile
from multiobjective_vns.registrations import OperatorGroup


def make_instance(item_count: int = 4) -> KnapsackInstance:
    return KnapsackInstance(
        problem_instance_id='catalog-v1',
        items=tuple(f'item-{index}' for index in range(item_count)),
        resources=(
            KnapsackResource('resource-1', 4, tuple(1 for _ in range(item_count))),
        ),
        objectives=(
            KnapsackObjective(
                'profit-1', 'Profit 1', tuple(2 for _ in range(item_count))
            ),
            KnapsackObjective(
                'profit-2', 'Profit 2', tuple(1 for _ in range(item_count))
            ),
        ),
    )


def make_profile(width: int = 1) -> PreferenceProfile:
    return PreferenceProfile((0.5, 0.5), (0.0,), width)


def test_default_catalog_registers_six_general_operators() -> None:
    catalog = build_operator_catalog(make_instance())
    registrations = {
        registration.operator_id: registration for registration in catalog.operators
    }

    assert set(registrations) == {
        'flip_items',
        'add_items',
        'remove_items',
        'exchange_items',
        'capacity_repair',
        'exact_flip',
    }
    assert registrations['flip_items'].max_neighborhood_size == 4
    assert registrations['exchange_items'].max_neighborhood_size == 2
    assert registrations['capacity_repair'].feasibility_restoration is True
    assert catalog.operator_groups == ()


def test_profile_catalog_sizes_and_restoration_flags() -> None:
    catalog = build_operator_catalog(
        make_instance(),
        profiles=(
            ProfileRegistration('balanced', make_profile()),
            ProfileRegistration('wide', make_profile(2)),
        ),
    )
    registrations = {
        registration.operator_id: registration for registration in catalog.operators
    }

    assert registrations['balanced:profile_core_flip'].max_neighborhood_size == 2
    assert registrations['balanced:profile_core_exchange'].max_neighborhood_size == 1
    assert registrations['balanced:profiled_capacity_repair'].max_neighborhood_size == 1
    assert registrations['balanced:profiled_capacity_repair'].feasibility_restoration
    assert (
        registrations['balanced:profile_core_flip'].configuration_id
        != registrations['wide:profile_core_flip'].configuration_id
    )


def test_profile_exchange_is_omitted_when_its_size_range_is_empty() -> None:
    catalog = build_operator_catalog(
        make_instance(item_count=1),
        profiles=(ProfileRegistration('narrow', make_profile()),),
    )

    assert all(
        registration.operator_id != 'narrow:profile_core_exchange'
        for registration in catalog.operators
    )
    assert all(
        registration.operator_id != 'exchange_items'
        for registration in catalog.operators
    )


def test_catalog_accepts_groups_of_registered_operators() -> None:
    group = OperatorGroup(
        'repair-hint',
        frozenset({'capacity_repair', 'remove_items'}),
    )

    catalog = build_operator_catalog(make_instance(), operator_groups=(group,))

    assert catalog.operator_groups == (group,)


def test_catalog_rejects_unknown_group_members_and_duplicate_profiles() -> None:
    with pytest.raises(ValueError, match='registered'):
        build_operator_catalog(
            make_instance(),
            operator_groups=(OperatorGroup('unknown', frozenset({'a', 'b'})),),
        )
    with pytest.raises(ValueError, match='unique'):
        build_operator_catalog(
            make_instance(),
            profiles=(
                ProfileRegistration('same', make_profile()),
                ProfileRegistration('same', make_profile(2)),
            ),
        )


def test_catalog_rejects_profile_dimension_mismatch() -> None:
    profile = PreferenceProfile((0.5, 0.5), (0.0, 0.0), 1)

    with pytest.raises(ValueError, match='resource prices'):
        build_operator_catalog(
            make_instance(), profiles=(ProfileRegistration('bad', profile),)
        )
