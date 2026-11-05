"""Errors raised by standalone scenario generation.

Created: 2026-11-03T17:10:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    'ScenarioGenerationError',
    'ScenarioInputError',
    'ScenarioReductionError',
]


class ScenarioInputError(ValueError):
    """Invalid generation input, option, registration, or initial destination."""

    def __init__(
        self,
        *,
        reason: str,
        file: str | None = None,
        row: int | None = None,
        column: str | None = None,
    ) -> None:
        self.file = file
        self.row = row
        self.column = column
        self.reason = reason
        location = ''
        if file is not None:
            location = file
            if row is not None:
                location += f':{row}'
            if column is not None:
                location += f' ({column})'
            location += ': '
        super().__init__(f'{location}{reason}')


class ScenarioGenerationError(RuntimeError):
    """Generation, extension, generated-profile, or publication failure."""

    def __init__(
        self,
        *,
        reason: str,
        operation: str | None = None,
        path: Path | None = None,
        extension_id: str | None = None,
        configuration_id: str | None = None,
        method: str | None = None,
    ) -> None:
        self.reason = reason
        self.operation = operation
        self.path = path
        self.extension_id = extension_id
        self.configuration_id = configuration_id
        self.method = method
        details = []
        if operation is not None:
            details.append(f'operation={operation}')
        if path is not None:
            details.append(f'path={path}')
        if extension_id is not None:
            details.append(f'extension={extension_id}')
        if configuration_id is not None:
            details.append(f'configuration={configuration_id}')
        if method is not None:
            details.append(f'method={method}')
        prefix = f'{" ".join(details)}: ' if details else ''
        super().__init__(f'{prefix}{reason}')


class ScenarioReductionError(ScenarioGenerationError):
    """The requested scenario count cannot satisfy reduction constraints."""
