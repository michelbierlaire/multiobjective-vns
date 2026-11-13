"""Objective comparison and ordered objective-vector declaration contracts.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeAlias

from multiobjective_vns.exceptions import ComponentRegistrationError
from multiobjective_vns.identifiers import (
    ComponentConfigurationId,
    ComponentId,
    ObjectiveId,
    identifier_text,
)

ObjectiveValue: TypeAlias = object


class ObjectiveComparison(StrEnum):
    """Total-order result for comparing two values of one objective."""

    LESS = 'less'
    EQUAL = 'equal'
    GREATER = 'greater'


class ObjectiveFunction(Protocol):
    """Application contract for one objective's ordering and display."""

    @property
    def objective_id(self) -> ObjectiveId:
        """Stable identifier for this objective's position."""

    @property
    def component_id(self) -> ComponentId:
        """Stable failure-attribution identifier for this component."""

    @property
    def configuration_id(self) -> ComponentConfigurationId:
        """Stable identifier for comparison and display configuration."""

    def compare(
        self, left: ObjectiveValue, right: ObjectiveValue
    ) -> ObjectiveComparison:
        """Compare values, where ``less`` is preferred by minimization."""

    def format(self, value: ObjectiveValue) -> str:
        """Format one objective value for human-readable reporting."""

    def __str__(self) -> str:
        """Return the objective's nonempty human-readable name."""


@dataclass(frozen=True, slots=True)
class ObjectiveDeclaration:
    """Ordered objective functions defining objective-vector positions.

    :param objectives: At least one distinct objective in declaration order.
    :param upper_bounds: One optional maximum per objective, in matching order.
        ``None`` expands to an unbounded entry for every objective.
    :raises ComponentRegistrationError: If the objective list or bound tuple is
        malformed, empty, has duplicate objective IDs, or has a different length.
    """

    objectives: tuple[ObjectiveFunction, ...]
    upper_bounds: tuple[ObjectiveValue | None, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.objectives, tuple):
            raise ComponentRegistrationError('objectives must be a tuple')
        if not self.objectives:
            raise ComponentRegistrationError('at least one objective is required')
        if self.upper_bounds is None:
            object.__setattr__(self, 'upper_bounds', (None,) * len(self.objectives))
        elif not isinstance(self.upper_bounds, tuple):
            raise ComponentRegistrationError('upper_bounds must be a tuple or None')
        elif len(self.upper_bounds) != len(self.objectives):
            raise ComponentRegistrationError(
                'upper_bounds length must match the objective count'
            )

        seen_ids: set[ObjectiveId] = set()
        seen_text: set[str] = set()
        try:
            for objective in self.objectives:
                if not callable(getattr(objective, 'compare', None)):
                    raise TypeError('each objective must provide compare()')
                if not callable(getattr(objective, 'format', None)):
                    raise TypeError('each objective must provide format()')
                objective_id = objective.objective_id
                text = identifier_text(objective_id)
                if objective_id in seen_ids or text in seen_text:
                    raise ValueError(
                        'objective IDs and their text forms must be unique'
                    )
                seen_ids.add(objective_id)
                seen_text.add(text)
        except Exception as error:
            raise ComponentRegistrationError(str(error)) from error
