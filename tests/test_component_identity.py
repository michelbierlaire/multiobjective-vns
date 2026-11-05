"""Tests for component identity protocols used by registration contracts.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.components import CheckpointComponent, ComponentIdentity


class IdentifiedComponent:
    component_id = 'component'
    configuration_id = 'configuration-v1'


def test_component_identity_protocols_are_structural() -> None:
    component: ComponentIdentity = IdentifiedComponent()
    checkpoint_component: CheckpointComponent = IdentifiedComponent()

    assert component.component_id == 'component'
    assert checkpoint_component.configuration_id == 'configuration-v1'
