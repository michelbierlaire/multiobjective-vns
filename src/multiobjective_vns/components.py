"""Structural identity contracts for registered component objects.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from typing import Protocol

from multiobjective_vns.identifiers import ComponentConfigurationId, ComponentId


class ComponentIdentity(Protocol):
    """Identity required for failure attribution and event provenance."""

    @property
    def component_id(self) -> ComponentId:
        """Return this registered component's stable identifier."""


class CheckpointComponent(ComponentIdentity, Protocol):
    """Identity contract for a component whose setup affects continuation."""

    @property
    def configuration_id(self) -> ComponentConfigurationId:
        """Return the stable identifier for continuation-relevant settings."""
