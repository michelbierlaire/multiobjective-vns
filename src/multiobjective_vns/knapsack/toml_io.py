"""Read and validate version-one multiobjective knapsack TOML files.

Created: 2026-10-29T17:43:12+00:00
Author: Michel Bierlaire, EPFL
"""

import tomllib
from pathlib import Path
from typing import cast

from multiobjective_vns.knapsack.instance import (
    KnapsackInstance,
    KnapsackObjective,
    KnapsackResource,
)
from multiobjective_vns.values import finite_real


class KnapsackInputError(ValueError):
    """Invalid or unsupported multiobjective knapsack input file."""


def _mapping(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise KnapsackInputError(f'{name} must be a TOML table')
    return cast(dict[str, object], value)


def _fields(value: dict[str, object], *, required: set[str], name: str) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required
    if missing:
        raise KnapsackInputError(
            f'{name} is missing required fields: {sorted(missing)}'
        )
    if unknown:
        raise KnapsackInputError(f'{name} has unknown fields: {sorted(unknown)}')


def _string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnapsackInputError(f'{name} must be a nonempty string')
    return value


def _string_list(value: object, *, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise KnapsackInputError(f'{name} must be an array of strings')
    return tuple(
        _string(item, name=f'{name}[{index}]') for index, item in enumerate(value)
    )


def _number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KnapsackInputError(f'{name} must be an integer or floating-point number')
    try:
        return finite_real(value, name=name)
    except (TypeError, ValueError) as error:
        raise KnapsackInputError(f'{name} must be finite: {error}') from error


def _number_list(value: object, *, name: str) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise KnapsackInputError(f'{name} must be an array of numbers')
    return tuple(
        _number(item, name=f'{name}[{index}]') for index, item in enumerate(value)
    )


def load_instance(path: str | Path) -> KnapsackInstance:
    """Load a fixed problem instance from a version-one TOML file.

    :param path: Path to a UTF-8 TOML problem file.
    :returns: Validated immutable instance data in file order.
    :raises KnapsackInputError: If encoding, TOML syntax, schema, or instance data are invalid.
    :raises OSError: If the file cannot be opened or read.
    """
    source = Path(path)
    try:
        with source.open('rb') as file:
            raw = tomllib.load(file)
    except UnicodeDecodeError as error:
        raise KnapsackInputError(f'input must be UTF-8: {source}') from error
    except tomllib.TOMLDecodeError as error:
        raise KnapsackInputError(f'invalid TOML in {source}: {error}') from error

    root = _mapping(raw, name='document')
    _fields(
        root,
        required={
            'format_version',
            'problem_instance_id',
            'items',
            'resources',
            'objectives',
        },
        name='document',
    )

    version = root['format_version']
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise KnapsackInputError('format_version must be the integer 1')

    resources_data = root['resources']
    if not isinstance(resources_data, list):
        raise KnapsackInputError('resources must be an array of tables')
    resources: list[KnapsackResource] = []
    for index, raw_resource in enumerate(resources_data):
        name = f'resources[{index}]'
        resource = _mapping(raw_resource, name=name)
        _fields(
            resource,
            required={'id', 'capacity', 'consumption'},
            name=name,
        )
        try:
            resources.append(
                KnapsackResource(
                    resource_id=_string(resource['id'], name=f'{name}.id'),
                    capacity=_number(resource['capacity'], name=f'{name}.capacity'),
                    consumption=_number_list(
                        resource['consumption'], name=f'{name}.consumption'
                    ),
                )
            )
        except (TypeError, ValueError) as error:
            raise KnapsackInputError(f'invalid {name}: {error}') from error

    objectives_data = root['objectives']
    if not isinstance(objectives_data, list):
        raise KnapsackInputError('objectives must be an array of tables')
    objectives: list[KnapsackObjective] = []
    for index, raw_objective in enumerate(objectives_data):
        name = f'objectives[{index}]'
        objective = _mapping(raw_objective, name=name)
        _fields(
            objective,
            required={'id', 'name', 'contribution'},
            name=name,
        )
        try:
            objectives.append(
                KnapsackObjective(
                    objective_id=_string(objective['id'], name=f'{name}.id'),
                    name=_string(objective['name'], name=f'{name}.name'),
                    contribution=_number_list(
                        objective['contribution'],
                        name=f'{name}.contribution',
                    ),
                )
            )
        except (TypeError, ValueError) as error:
            raise KnapsackInputError(f'invalid {name}: {error}') from error

    try:
        return KnapsackInstance(
            problem_instance_id=_string(
                root['problem_instance_id'], name='problem_instance_id'
            ),
            items=_string_list(root['items'], name='items'),
            resources=tuple(resources),
            objectives=tuple(objectives),
        )
    except (TypeError, ValueError) as error:
        raise KnapsackInputError(f'invalid knapsack instance: {error}') from error
