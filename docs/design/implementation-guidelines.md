# Python Implementation Guidelines

**Author:** Michel Bierlaire, EPFL  
**Created:** 2026-10-29T12:55:36Z

## Purpose and authority

These guidelines govern implementation of the `multiobjective_vns` Python package. The architecture document defines object responsibilities and public contracts; the algorithm document defines search behavior and defaults. Implementations should follow those documents. When implementation work exposes a gap or contradiction, update the relevant design document before changing the contract in code.

This guide describes coding and project conventions. It does not replace the architecture or algorithm specifications.

## Python conventions

- Follow PEP 8 naming: `snake_case` for modules, functions, methods, and variables; `CapWords` for classes; `UPPER_SNAKE_CASE` for constants.
- Use type annotations on public functions, methods, callback protocols, and records. Prefer specific types over `Any`; use `object` where a value is intentionally opaque.
- The project requires Python 3.12 or newer. Keep implementation syntax and dependencies compatible with Python 3.12; set the Ruff target to py312. Change the minimum only through an explicit project decision and update the package metadata and lock file together.
- Use single quotes for ordinary Python string literals. Use triple-double-quoted strings for Python docstrings, following PEP 257.
- Use American English in comments, docstrings, documentation, examples, and project metadata.
- Avoid import-time work, global mutable state, and printing from library code. Use the package event and reporting interfaces for user-visible search information.
- Keep imports explicit and avoid circular dependencies. Use `TYPE_CHECKING` imports only where they simplify a genuine dependency cycle.

### Python file headers and docstrings

Every Python module starts with a module docstring that states the module's responsibility, creation time, and author. Use an ISO 8601 timestamp with a timezone offset. Preserve the original creation timestamp when editing the file.

    """Candidate processing and submission rules.

    Created: YYYY-MM-DDTHH:MM:SS±HH:MM
    Author: Michel Bierlaire, EPFL
    """

The module docstring is the first statement in the file. A shebang or encoding marker is unnecessary for ordinary package modules. Class and public callable docstrings explain the behavior promised by that interface.

## Package and file organization

Use the `src` layout. Keep one cohesive concept or responsibility in each file. A file should be short enough to review easily; split it when it begins to own distinct contracts or responsibilities, rather than enforcing an arbitrary line limit. Use subpackages for families of related modules, and avoid a directory that contains only one small module.

A suitable structure as the implementation grows is:

    src/
      multiobjective_vns/
        __init__.py
        api.py
        config.py
        identifiers.py
        exceptions.py
        objectives.py
        records/
          __init__.py
          candidate.py
          evaluation.py
          archive.py
          results.py
        solution/
          __init__.py
          identity.py
          codec.py
        search/
          __init__.py
          engine.py
          candidate_processing.py
          loop.py
          state.py
          operators.py
          compositions.py
        policies/
          __init__.py
          archive.py
          exploration.py
          base_selection.py
          feedback.py
          budgets.py
        services/
          __init__.py
          executor.py
          random_source.py
          checkpoint_store.py
          run_logger.py
        events/
          __init__.py
          records.py
          schemas.py
        py.typed

`search/engine.py` contains the `MultiobjectiveVNS` coordinator. Keep it focused on lifecycle and orchestration; delegate candidate submission and the search loop to their own modules. `api.py` contains the convenience function. The package-level `__init__.py` deliberately re-exports the supported public API. Internal modules remain importable by package code but are not presented as stable user entry points.

The tree is a growth plan, not a requirement to create empty modules in advance. Add a file when it has a clear responsibility and useful contents.

## Design and implementation boundaries

- Give each object one primary responsibility. Keep application evaluation, solution identity and encoding, archive decisions, pool membership, feedback, budgets, event delivery, and persistence at the boundaries assigned by the design.
- Use immutable public records for outcomes, events, decisions, and snapshots where the contracts call for stable values. Treat application solutions and metadata as read-only while the package retains or exposes them.
- Keep policy decision calls read-only. Stage state changes through the documented commit hooks and publish them with the corresponding search transition.
- Keep candidate processing and search transitions sequential in version one. A composition is one selected choice and one `CandidateAttempt`; it may return an ordered list of final candidate proposals, all processed in order under the algorithm contract.
- Use the single configured `RandomSource` for package, policy, and Operator draws. Do not introduce independent module-level random generators.
- Each application's `ApplicationEvaluator` constructs the complete objective
  vector, including application-defined costs and penalties. The generic VNS
  applies the corresponding `ObjectiveFunction` comparisons to maintain the
  Pareto archive. In the multi-day VRP, early- and late-window penalties belong
  to a dedicated time-window-violation objective, as defined in
  `multiday_vrp.md` §1.8 and `multiday_vrp_input_format.md` §10.1.
- Preserve checkpoint and resume contracts. Require `SolutionCodec.capture` to produce application-defined bytes, and store those bytes without interpreting or re-serializing their contents. The default Pareto snapshot wraps them in base64 within its versioned JSON envelope. Keep component `StatePayload` values opaque to the generic package and preserve them through the configured `CheckpointStore`.
- Catch component exceptions at the boundaries defined by the design. Convert them to the specified diagnostic and follow the algorithm's recovery or stopping rule. Do not swallow exceptions or continue from partially updated state.
- Keep version-one implementation scoped to the agreed contracts. Parallel-stream coordination remains a future extension.

## Formatting, linting, and typing

Use Ruff as the single formatter and linter. Configure it in `pyproject.toml` with target-version = 'py312', an 88-character line length, import sorting, and single-quote formatting. Keep the formatter and linter configuration in the repository rather than relying on editor defaults.

Typical checks are:

    uv run ruff check .
    uv run ruff format --check .

Apply formatting with:

    uv run ruff format .

Use **mypy** as the project's sole static type checker. Add it as a development dependency and configure it in `pyproject.toml` with Python 3.12 as the target and strict checking enabled for `src/` and `tests/`. Run `uv run mypy` locally and in continuous integration, and require clean results for changed code. Pin the selected mypy version through `uv.lock`. Avoid broad suppressions; when a suppression is necessary, use the narrowest applicable form and explain the specific limitation next to it.

## Tests and examples

Use a test-first sequence for every production Python file: before implementing or changing the file, write or update its corresponding pytest based on the applicable design and algorithm contracts. After completing the implementation, run that test. Then run the relevant broader test set when the change can affect neighboring components or shared behavior.

Add focused tests alongside each implementation area:

- Unit tests cover record validation, identity behavior, comparison rules, policy decisions, and failure normalization.
- Integration tests cover initial processing, candidate attempts, compositions, budgets, active-pool changes, logging, checkpoints, and resume.
- Use a controlled `RandomSource` in tests so selection and failure cases are reproducible. Include tests for rollback and for state restoration after a failed transition.
- Test public behavior and documented contracts rather than private helper call sequences.
- Keep worked application examples in `examples/` or the design documentation; keep reusable package behavior in `src/`.

Tests should not depend on wall-clock timing or statistical expectations when a deterministic test can establish the rule.

## Sphinx documentation

Use Sphinx with autodoc for the public API. Write docstrings in reStructuredText field-list style so Sphinx can render parameter, return, and exception documentation consistently:

    :param solution: Application solution to evaluate.
    :returns: The application's evaluation findings.
    :raises ValueError: If the solution violates the callback's input contract.

Document public behavior, input constraints, return values, raised exceptions, and short examples. Avoid repeating full architecture or algorithm sections in API docstrings; link to the authoritative design pages instead.

Keep Sphinx sources under `docs/source/` and retain the architecture and algorithm documents under `docs/design/`. Build HTML documentation with warnings treated as errors in continuous integration. Ensure examples in documentation remain consistent with the shared contracts.

## Packaging and dependencies

- Keep runtime dependencies minimal. The current project has none; add a runtime dependency only when it materially improves the package and the need is documented.
- Put formatters, type checkers, Sphinx, and test tools in development dependency groups, not runtime dependencies.
- Manage environments and dependency resolution with `uv`. Commit the lock file when dependencies are added, and have continuous integration install from the locked project configuration.
- Keep the `py.typed` marker in the distributed package so downstream type checkers can use its annotations.
- Update package metadata, user documentation, and examples together when a supported Python version or public API changes.

## Completion checklist

A change is ready to merge when:

1. Its pytest was written from the documented object and algorithm contracts before production implementation began.
2. The corresponding pytest was run after implementation was completed, and relevant broader tests pass.
3. Public interfaces have type annotations and Sphinx-compatible docstrings.
4. Ruff formatting and linting and the selected type checker pass.
5. Documentation and examples match the implementation.
6. No unintended public API, dependency, or checkpoint-compatibility change is introduced.
