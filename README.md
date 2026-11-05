# multiobjective-vns

Reusable framework for multiobjective variable neighborhood search.

The package is under development. Its architecture and algorithm contracts
are documented in [`docs/design/`](docs/design/).

The multi-day VRP application includes a runnable example in
[`examples/multiday_vrp/minimal/`](examples/multiday_vrp/minimal/README.md).
Run it from that directory with `uv run python run.py`.

## Development setup

Use Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). Install the
development tools with:

```console
uv sync --all-groups
```

Run the project checks with:

```console
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
uv run sphinx-build -W --keep-going -b html docs/source docs/_build/html
```

Apply Ruff formatting with `uv run ruff format .`.
