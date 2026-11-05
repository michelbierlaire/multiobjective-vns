"""Immutable records for a multiobjective knapsack problem instance.

Created: 2026-10-29T17:43:12+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass

from multiobjective_vns.values import finite_real


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')
    return value


@dataclass(frozen=True, slots=True)
class KnapsackResource:
    """One capacity constraint and its per-item consumptions.

    :param resource_id: Stable nonempty identifier for this resource.
    :param capacity: Nonnegative finite capacity.
    :param consumption: Nonnegative finite use for each item, in item order.
    """

    resource_id: str
    capacity: float
    consumption: tuple[float, ...]

    def __post_init__(self) -> None:
        _require_text(self.resource_id, name='resource_id')
        capacity = finite_real(self.capacity, name='capacity')
        if capacity < 0:
            raise ValueError('capacity must be nonnegative')
        if not isinstance(self.consumption, tuple):
            raise TypeError('consumption must be a tuple')
        normalized = tuple(
            finite_real(value, name=f'consumption[{index}]')
            for index, value in enumerate(self.consumption)
        )
        if any(value < 0 for value in normalized):
            raise ValueError('resource consumptions must be nonnegative')
        object.__setattr__(self, 'capacity', capacity)
        object.__setattr__(self, 'consumption', normalized)


@dataclass(frozen=True, slots=True)
class KnapsackObjective:
    """One maximized objective contribution for each item.

    :param objective_id: Stable nonempty identifier for this objective.
    :param name: Nonempty display name.
    :param contribution: Finite contribution for each item, in item order.
    """

    objective_id: str
    name: str
    contribution: tuple[float, ...]

    def __post_init__(self) -> None:
        _require_text(self.objective_id, name='objective_id')
        _require_text(self.name, name='name')
        if not isinstance(self.contribution, tuple):
            raise TypeError('contribution must be a tuple')
        normalized = tuple(
            finite_real(value, name=f'contribution[{index}]')
            for index, value in enumerate(self.contribution)
        )
        object.__setattr__(self, 'contribution', normalized)


@dataclass(frozen=True, slots=True)
class KnapsackInstance:
    """Ordered item data, resource constraints, and objective definitions.

    :param problem_instance_id: Stable application-chosen problem identity.
    :param items: Unique item identifiers; tuple position defines vector order.
    :param resources: At least one ordered capacity constraint.
    :param objectives: At least two ordered maximization objectives.
    :raises TypeError: If collections are not immutable tuples.
    :raises ValueError: If identifiers, collection sizes, or dimensions are invalid.
    """

    problem_instance_id: str
    items: tuple[str, ...]
    resources: tuple[KnapsackResource, ...]
    objectives: tuple[KnapsackObjective, ...]

    def __post_init__(self) -> None:
        _require_text(self.problem_instance_id, name='problem_instance_id')
        if not isinstance(self.items, tuple):
            raise TypeError('items must be a tuple')
        if not self.items:
            raise ValueError('at least one item is required')
        for index, item in enumerate(self.items):
            _require_text(item, name=f'items[{index}]')
        if len(set(self.items)) != len(self.items):
            raise ValueError('item identifiers must be unique')

        if not isinstance(self.resources, tuple):
            raise TypeError('resources must be a tuple')
        if not self.resources:
            raise ValueError('at least one resource is required')
        if any(
            not isinstance(resource, KnapsackResource) for resource in self.resources
        ):
            raise TypeError('resources must contain KnapsackResource values')
        resource_ids = tuple(resource.resource_id for resource in self.resources)
        if len(set(resource_ids)) != len(resource_ids):
            raise ValueError('resource identifiers must be unique')
        if any(
            len(resource.consumption) != len(self.items) for resource in self.resources
        ):
            raise ValueError('each resource vector must match the item count')

        if not isinstance(self.objectives, tuple):
            raise TypeError('objectives must be a tuple')
        if len(self.objectives) < 2:
            raise ValueError('at least two objectives are required')
        if any(
            not isinstance(objective, KnapsackObjective)
            for objective in self.objectives
        ):
            raise TypeError('objectives must contain KnapsackObjective values')
        objective_ids = tuple(objective.objective_id for objective in self.objectives)
        if len(set(objective_ids)) != len(objective_ids):
            raise ValueError('objective identifiers must be unique')
        if any(
            len(objective.contribution) != len(self.items)
            for objective in self.objectives
        ):
            raise ValueError('each objective vector must match the item count')

    @property
    def n_items(self) -> int:
        """Return the number of items in the declared order."""
        return len(self.items)

    @property
    def n_resources(self) -> int:
        """Return the number of capacity constraints."""
        return len(self.resources)

    @property
    def n_objectives(self) -> int:
        """Return the number of maximized objectives."""
        return len(self.objectives)
