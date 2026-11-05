"""Tests for the VRP application extension registry.

Created: 2026-11-03T16:11:47+01:00
Author: Michel Bierlaire, EPFL
"""

import pytest

from multiobjective_vns.multiday_vrp.extensions import (
    BaseMeasureRegistration,
    VrpExtensionRegistry,
    VrpExtensionRegistryError,
)


def base_measure(extension_id: str, component_id: str) -> BaseMeasureRegistration:
    return BaseMeasureRegistration(
        extension_id=extension_id,
        component_id=component_id,
        configuration_id=f'{component_id}-v1',
        validate_parameters=lambda request: None,
        evaluate=lambda request: 0.0,
    )


def test_extension_registry_is_immutable_and_keeps_registration_order() -> None:
    first = base_measure('measure-a', 'app:measure-a')
    second = base_measure('measure-b', 'app:measure-b')
    registry = VrpExtensionRegistry(
        registry_id='app.extensions',
        configuration_id='app-extensions-v1',
        base_measures=(first, second),
    )

    assert registry.base_measures == (first, second)
    with pytest.raises(AttributeError):
        registry.registry_id = 'changed'  # type: ignore[misc]


@pytest.mark.parametrize(
    'registrations',
    [
        (
            base_measure('measure-a', 'component-a'),
            base_measure('measure-a', 'component-b'),
        ),
        (base_measure('measure-a', 'shared-component'),),
    ],
)
def test_extension_registry_rejects_duplicate_family_or_component_ids(
    registrations: tuple[BaseMeasureRegistration, ...],
) -> None:
    values = registrations
    if len(values) == 1:
        values = (values[0], base_measure('measure-b', 'shared-component'))

    with pytest.raises(VrpExtensionRegistryError):
        VrpExtensionRegistry(
            registry_id='app.extensions',
            configuration_id='app-extensions-v1',
            base_measures=values,
        )


def test_extension_registry_requires_nonempty_stable_identifiers() -> None:
    with pytest.raises(VrpExtensionRegistryError):
        VrpExtensionRegistry(registry_id='', configuration_id='v1')


def test_extension_registry_rejects_missing_callback() -> None:
    registration = BaseMeasureRegistration(
        extension_id='metric',
        component_id='app:metric',
        configuration_id='metric-v1',
        validate_parameters=lambda request: None,
        evaluate=None,  # type: ignore[arg-type]
    )

    with pytest.raises(VrpExtensionRegistryError):
        VrpExtensionRegistry(
            registry_id='app.extensions',
            configuration_id='v1',
            base_measures=(registration,),
        )
