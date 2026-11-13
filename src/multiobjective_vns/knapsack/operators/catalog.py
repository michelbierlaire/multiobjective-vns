"""Build stable core registrations for the knapsack Operator catalog.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass

from multiobjective_vns.callbacks import Operator
from multiobjective_vns.candidate_batch import CandidateBatchAdapter
from multiobjective_vns.knapsack.instance import KnapsackInstance
from multiobjective_vns.knapsack.operators.basic import (
    AddItemsOperator,
    FlipItemsOperator,
    RemoveItemsOperator,
)
from multiobjective_vns.knapsack.operators.exact_flip import ExactFlipOperator
from multiobjective_vns.knapsack.operators.exchange import ExchangeItemsOperator
from multiobjective_vns.knapsack.operators.profile import (
    PreferenceProfile,
    ProfileCoreAddOperator,
    ProfileCoreExchangeOperator,
    ProfileCoreFlipOperator,
    ProfileCoreRemoveOperator,
    ProfiledCapacityRepairOperator,
)
from multiobjective_vns.knapsack.operators.repair import CapacityRepairOperator
from multiobjective_vns.registrations import OperatorGroup, OperatorRegistration


@dataclass(frozen=True, slots=True)
class ProfileRegistration:
    """Stable name and fixed configuration for one preference profile."""

    profile_id: str
    profile: PreferenceProfile

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError('profile_id must be a nonempty string')
        if not isinstance(self.profile, PreferenceProfile):
            raise TypeError('profile must be a PreferenceProfile')


@dataclass(frozen=True, slots=True)
class KnapsackOperatorCatalog:
    """Registered Operators and caller-provided soft composition groups."""

    operators: tuple[OperatorRegistration, ...]
    operator_groups: tuple[OperatorGroup, ...]


def _registration(
    operator_id: str,
    configuration_id: str,
    operator: Operator,
    max_size: int,
    *,
    feasibility_restoration: bool = False,
    modification_count_definition: str,
) -> OperatorRegistration:
    return OperatorRegistration(
        operator_id=operator_id,
        configuration_id=f'knapsack-candidate-batch-v2:{configuration_id}',
        operator=CandidateBatchAdapter(operator),
        initial_state=None,
        max_neighborhood_size=max_size,
        feasibility_restoration=feasibility_restoration,
        modification_count_definition=modification_count_definition,
    )


def _profile_configuration_id(name: str, profile: PreferenceProfile) -> str:
    weights = ','.join(value.hex() for value in profile.objective_weights)
    prices = ','.join(value.hex() for value in profile.resource_prices)
    return (
        f'knapsack-{name}-profile-v1:'
        f'width={profile.core_width}:weights={weights}:prices={prices}'
    )


def build_operator_catalog(
    instance: KnapsackInstance,
    *,
    profiles: tuple[ProfileRegistration, ...] = (),
    operator_groups: tuple[OperatorGroup, ...] = (),
) -> KnapsackOperatorCatalog:
    """Build registrations for all general and configured profile Operators.

    :param instance: Validated problem data that fixes vector width and capacities.
    :param profiles: Optional named profiles for the profile-guided Operators.
    :param operator_groups: Caller-provided soft hints for composition selection.
    :returns: Ordered Operator registrations and validated group hints.
    :raises TypeError: If profile or group collections are not tuples.
    :raises ValueError: If profile names or group membership are inconsistent.
    """
    if not isinstance(profiles, tuple) or any(
        not isinstance(profile, ProfileRegistration) for profile in profiles
    ):
        raise TypeError('profiles must be a tuple of ProfileRegistration values')
    if not isinstance(operator_groups, tuple) or any(
        not isinstance(group, OperatorGroup) for group in operator_groups
    ):
        raise TypeError('operator_groups must be a tuple of OperatorGroup values')
    profile_ids = tuple(profile.profile_id for profile in profiles)
    if len(set(profile_ids)) != len(profile_ids):
        raise ValueError('profile_id values must be unique')

    item_count = instance.n_items
    registrations = [
        _registration(
            'flip_items',
            'knapsack-flip-items-v1',
            FlipItemsOperator(instance),
            item_count,
            modification_count_definition='number of item decisions flipped',
        ),
        _registration(
            'add_items',
            'knapsack-add-items-v1',
            AddItemsOperator(instance),
            item_count,
            modification_count_definition='number of items added',
        ),
        _registration(
            'remove_items',
            'knapsack-remove-items-v1',
            RemoveItemsOperator(instance),
            item_count,
            modification_count_definition='number of items removed',
        ),
        _registration(
            'capacity_repair',
            'knapsack-capacity-repair-v1',
            CapacityRepairOperator(instance),
            item_count,
            feasibility_restoration=True,
            modification_count_definition='number of items removed',
        ),
        _registration(
            'exact_flip',
            'knapsack-exact-flip-v1',
            ExactFlipOperator(instance),
            item_count,
            modification_count_definition=(
                'number of item decisions flipped; m equals k for a candidate'
            ),
        ),
    ]
    exchange_size = item_count // 2
    if exchange_size > 0:
        registrations.insert(
            3,
            _registration(
                'exchange_items',
                'knapsack-exchange-items-v1',
                ExchangeItemsOperator(instance),
                exchange_size,
                modification_count_definition=(
                    'number of one-for-one exchanges applied'
                ),
            ),
        )

    for named_profile in profiles:
        profile_id = named_profile.profile_id
        profile = named_profile.profile
        boundary_size = min(item_count, 2 * profile.core_width)
        profile_operators: list[tuple[str, Operator, int, bool, str]] = [
            (
                'profile_core_flip',
                ProfileCoreFlipOperator(instance, profile),
                boundary_size,
                False,
                'number of boundary item decisions flipped',
            ),
            (
                'profile_core_add',
                ProfileCoreAddOperator(instance, profile),
                boundary_size,
                False,
                'number of boundary items added',
            ),
            (
                'profile_core_remove',
                ProfileCoreRemoveOperator(instance, profile),
                boundary_size,
                False,
                'number of boundary items removed',
            ),
        ]
        exchange_size = boundary_size // 2
        if exchange_size > 0:
            profile_operators.append(
                (
                    'profile_core_exchange',
                    ProfileCoreExchangeOperator(instance, profile),
                    exchange_size,
                    False,
                    'number of boundary one-for-one exchanges applied',
                )
            )
        profile_operators.append(
            (
                'profiled_capacity_repair',
                ProfiledCapacityRepairOperator(instance, profile),
                profile.core_width,
                True,
                'number of boundary items removed',
            )
        )
        for (
            name,
            operator,
            max_size,
            restoration,
            count_definition,
        ) in profile_operators:
            registrations.append(
                _registration(
                    f'{profile_id}:{name}',
                    _profile_configuration_id(name, profile),
                    operator,
                    max_size,
                    feasibility_restoration=restoration,
                    modification_count_definition=count_definition,
                )
            )

    operator_ids = tuple(registration.operator_id for registration in registrations)
    if len(set(operator_ids)) != len(operator_ids):
        raise ValueError('generated Operator IDs must be unique')
    registered_ids = set(operator_ids)
    group_ids = tuple(group.group_id for group in operator_groups)
    if len(set(group_ids)) != len(group_ids):
        raise ValueError('OperatorGroup IDs must be unique')
    for group in operator_groups:
        if not group.operator_ids <= registered_ids:
            raise ValueError('OperatorGroup members must be registered Operators')

    return KnapsackOperatorCatalog(tuple(registrations), operator_groups)
