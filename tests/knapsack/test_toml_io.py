"""Tests for the version-one knapsack TOML input contract.

Created: 2026-10-29T17:42:44+00:00
Author: Michel Bierlaire, EPFL
"""

from pathlib import Path

import pytest

from multiobjective_vns.knapsack.toml_io import KnapsackInputError, load_instance

VALID_TOML = """\
format_version = 1
problem_instance_id = 'tiny-v1'
items = ['a', 'b']

[[resources]]
id = 'weight'
capacity = 3
consumption = [1, 2]

[[objectives]]
id = 'profit-1'
name = 'Profit 1'
contribution = [2, 5]

[[objectives]]
id = 'profit-2'
name = 'Profit 2'
contribution = [4, 3]
"""


def write_input(tmp_path: Path, contents: str) -> Path:
    path = tmp_path / 'instance.toml'
    path.write_text(contents, encoding='utf-8')
    return path


def test_load_instance_reads_ordered_data(tmp_path: Path) -> None:
    instance = load_instance(write_input(tmp_path, VALID_TOML))

    assert instance.problem_instance_id == 'tiny-v1'
    assert instance.items == ('a', 'b')
    assert instance.resources[0].resource_id == 'weight'
    assert instance.resources[0].capacity == 3.0
    assert instance.objectives[1].objective_id == 'profit-2'
    assert instance.objectives[1].contribution == (4.0, 3.0)


def test_load_p410_literature_example() -> None:
    path = (
        Path(__file__).parents[2] / 'examples' / 'knapsack' / 'p410' / 'instance.toml'
    )

    instance = load_instance(path)

    assert instance.problem_instance_id == 'p410-literature-v1'
    assert instance.n_items == 10
    assert instance.n_resources == 4
    assert instance.n_objectives == 2
    assert instance.resources[0].capacity == 246.0
    assert instance.objectives[0].contribution[1] == 87.0


@pytest.mark.parametrize(
    'replacement',
    [
        ('format_version = 1', 'format_version = 2'),
        ('format_version = 1', 'format_version = true'),
        ("problem_instance_id = 'tiny-v1'", "problem_instance_id = ''"),
        ("items = ['a', 'b']", "items = ['a', 'a']"),
        ('capacity = 3', 'capacity = true'),
        ('capacity = 3', 'capacity = -1'),
        ('capacity = 3', 'capacity = inf'),
        ('consumption = [1, 2]', 'consumption = [1]'),
        ('contribution = [2, 5]', 'contribution = [2]'),
        ("name = 'Profit 1'", "name = ''"),
        ("id = 'profit-2'", "id = 'profit-1'"),
    ],
)
def test_load_instance_rejects_invalid_data(
    tmp_path: Path, replacement: tuple[str, str]
) -> None:
    original, changed = replacement
    contents = VALID_TOML.replace(original, changed, 1)

    with pytest.raises(KnapsackInputError):
        load_instance(write_input(tmp_path, contents))


def test_load_instance_rejects_unknown_fields(tmp_path: Path) -> None:
    contents = VALID_TOML.replace(
        "problem_instance_id = 'tiny-v1'",
        "problem_instance_id = 'tiny-v1'\nextra = 'typo'",
    )

    with pytest.raises(KnapsackInputError, match='unknown'):
        load_instance(write_input(tmp_path, contents))


def test_load_instance_rejects_malformed_toml(tmp_path: Path) -> None:
    path = write_input(tmp_path, 'format_version = 1\nitems = [\n')

    with pytest.raises(KnapsackInputError, match='TOML'):
        load_instance(path)


def test_load_instance_rejects_non_utf8_input(tmp_path: Path) -> None:
    path = tmp_path / 'instance.toml'
    path.write_bytes(b'format_version = 1\nitems = [\xff]\n')

    with pytest.raises(KnapsackInputError, match='UTF-8'):
        load_instance(path)
