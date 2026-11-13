"""Preference profiles and profile-guided knapsack Operators.

Created: 2026-10-29T17:47:20+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass
from math import fsum, isfinite

from multiobjective_vns.knapsack.evaluation import knapsack_is_feasible
from multiobjective_vns.knapsack.instance import KnapsackInstance
from multiobjective_vns.knapsack.operators.basic import (
    AddItemsOperator,
    FlipItemsOperator,
    RemoveItemsOperator,
)
from multiobjective_vns.knapsack.operators.common import (
    randomized_sample,
    require_base,
    validate_positions,
)
from multiobjective_vns.knapsack.operators.exchange import ExchangeItemsOperator
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.values import finite_real, require_integer


@dataclass(frozen=True, slots=True)
class PreferenceProfile:
    """Fixed objective weights, resource prices, and boundary width.

    :param objective_weights: Nonnegative weights summing to one.
    :param resource_prices: Nonnegative heuristic price for each resource.
    :param core_width: Positive number of boundary items taken from each side.
    """

    objective_weights: tuple[float, ...]
    resource_prices: tuple[float, ...]
    core_width: int

    def __post_init__(self) -> None:
        if not isinstance(self.objective_weights, tuple):
            raise TypeError('objective_weights must be a tuple')
        if not isinstance(self.resource_prices, tuple):
            raise TypeError('resource_prices must be a tuple')
        weights = tuple(
            finite_real(value, name=f'objective_weights[{index}]')
            for index, value in enumerate(self.objective_weights)
        )
        prices = tuple(
            finite_real(value, name=f'resource_prices[{index}]')
            for index, value in enumerate(self.resource_prices)
        )
        if not weights:
            raise ValueError('at least one objective weight is required')
        if any(value < 0 for value in weights):
            raise ValueError('objective weights must be nonnegative')
        if fsum(weights) != 1.0:
            raise ValueError('objective weights must sum to one')
        if any(value < 0 for value in prices):
            raise ValueError('resource prices must be nonnegative')
        require_integer(self.core_width, name='core_width', minimum=1)
        object.__setattr__(self, 'objective_weights', weights)
        object.__setattr__(self, 'resource_prices', prices)


class _ProfileNeighborhood:
    """Compute profile scores, a greedy reference, and boundary positions."""

    __slots__ = ('scores', 'boundary_positions')

    def __init__(self, instance: KnapsackInstance, profile: PreferenceProfile) -> None:
        if len(profile.objective_weights) != instance.n_objectives:
            raise ValueError('profile objective weights must match objective count')
        if len(profile.resource_prices) != instance.n_resources:
            raise ValueError('profile resource prices must match resource count')

        self.scores = tuple(
            self._score(instance, profile, item) for item in range(instance.n_items)
        )
        score_order = sorted(
            range(instance.n_items), key=lambda item: (-self.scores[item], item)
        )
        reference = [0] * instance.n_items
        for item in score_order:
            if self.scores[item] <= 0:
                continue
            reference[item] = 1
            if not knapsack_is_feasible(instance, tuple(reference)):
                reference[item] = 0

        selected = sorted(
            (item for item, value in enumerate(reference) if value == 1),
            key=lambda item: (self.scores[item], item),
        )[: profile.core_width]
        excluded = sorted(
            (item for item, value in enumerate(reference) if value == 0),
            key=lambda item: (-self.scores[item], item),
        )[: profile.core_width]
        self.boundary_positions = tuple(sorted((*selected, *excluded)))

    @staticmethod
    def _score(
        instance: KnapsackInstance, profile: PreferenceProfile, item: int
    ) -> float:
        objective_score = fsum(
            weight * objective.contribution[item]
            for weight, objective in zip(
                profile.objective_weights, instance.objectives, strict=True
            )
        )
        resource_cost = fsum(
            price * resource.consumption[item]
            for price, resource in zip(
                profile.resource_prices, instance.resources, strict=True
            )
        )
        score = objective_score - resource_cost
        if not isfinite(score):
            raise ValueError('profile score must be finite')
        return score


def _profile_positions(
    instance: KnapsackInstance, profile: PreferenceProfile
) -> tuple[_ProfileNeighborhood, tuple[int, ...]]:
    neighborhood = _ProfileNeighborhood(instance, profile)
    positions = validate_positions(instance, neighborhood.boundary_positions)
    return neighborhood, positions


class ProfileCoreFlipOperator:
    """Apply the flip rule to profile-boundary item positions."""

    def __init__(self, instance: KnapsackInstance, profile: PreferenceProfile) -> None:
        _, positions = _profile_positions(instance, profile)
        self._delegate = FlipItemsOperator(instance, eligible_positions=positions)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply the shared flip contract over the profile boundary."""
        return self._delegate.call(context)


class ProfileCoreAddOperator:
    """Apply the add rule to unselected profile-boundary positions."""

    def __init__(self, instance: KnapsackInstance, profile: PreferenceProfile) -> None:
        _, positions = _profile_positions(instance, profile)
        self._delegate = AddItemsOperator(instance, eligible_positions=positions)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply the shared add contract over the profile boundary."""
        return self._delegate.call(context)


class ProfileCoreRemoveOperator:
    """Apply the remove rule to selected profile-boundary positions."""

    def __init__(self, instance: KnapsackInstance, profile: PreferenceProfile) -> None:
        _, positions = _profile_positions(instance, profile)
        self._delegate = RemoveItemsOperator(instance, eligible_positions=positions)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply the shared remove contract over the profile boundary."""
        return self._delegate.call(context)


class ProfileCoreExchangeOperator:
    """Exchange selected and unselected items within the profile boundary."""

    def __init__(self, instance: KnapsackInstance, profile: PreferenceProfile) -> None:
        _, positions = _profile_positions(instance, profile)
        self._delegate = ExchangeItemsOperator(instance, eligible_positions=positions)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply the shared exchange contract over the profile boundary."""
        return self._delegate.call(context)


class ProfiledCapacityRepairOperator:
    """Remove low-scoring selected boundary items to restore capacity."""

    def __init__(self, instance: KnapsackInstance, profile: PreferenceProfile) -> None:
        self._instance = instance
        self._neighborhood, self._boundary_positions = _profile_positions(
            instance, profile
        )
        self._core_width = profile.core_width

    def call(self, context: CandidateContext) -> OperatorResult:
        """Randomize and remove the ``k`` lowest-scoring selected boundary items.

        For an infeasible base, stop at the first feasible intermediate vector.
        The final result follows the shared ``ensure_feasible`` contract.
        """
        base = require_base(context, self._instance)
        base_feasible = knapsack_is_feasible(self._instance, base)
        ranked = sorted(
            (item for item in self._boundary_positions if base[item] == 1),
            key=lambda item: (self._neighborhood.scores[item], item),
        )
        chosen = ranked[: min(context.k, self._core_width)]
        removal_order = randomized_sample(chosen, len(chosen), context)

        working = list(base)
        modifications = 0
        for item in removal_order:
            working[item] = 0
            modifications += 1
            if not base_feasible and knapsack_is_feasible(
                self._instance, tuple(working)
            ):
                break

        if modifications == 0:
            return OperatorResult(candidates=())
        result = tuple(working)
        if context.ensure_feasible and not knapsack_is_feasible(self._instance, result):
            return OperatorResult(candidates=())
        return OperatorResult(
            candidates=(CandidateProposal(Candidate(result), modifications),)
        )
