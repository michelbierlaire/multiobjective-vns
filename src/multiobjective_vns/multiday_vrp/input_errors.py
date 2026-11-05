"""Structured errors raised while loading multi-day VRP input files.

Created: 2026-11-03T16:30:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

__all__ = ['VrpInputError']


class VrpInputError(ValueError):
    """Invalid input with source-file and optional row/column location.

    :param file: Input filename relative to its bundle root when available.
    :param reason: Human-readable description of the violated contract.
    :param row: One-based CSV row number, including the header, when applicable.
    :param column: Input column or manifest key when applicable.
    """

    def __init__(
        self,
        *,
        file: str,
        reason: str,
        row: int | None = None,
        column: str | None = None,
    ) -> None:
        self.file = file
        self.row = row
        self.column = column
        self.reason = reason
        location = file
        if row is not None:
            location += f':{row}'
        if column is not None:
            location += f':{column}'
        super().__init__(f'{location}: {reason}')
