"""Strict shared parsing helpers for multi-day VRP input bundles.

Created: 2026-11-03T16:30:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import csv
import math
import re
import tomllib
from datetime import date, datetime
from pathlib import Path
from typing import Any

from multiobjective_vns.multiday_vrp.input_errors import VrpInputError

type CsvRow = dict[str, str]

_INTEGER_PATTERN = re.compile(r'^[+-]?[0-9]+$')
_FLOAT_PATTERN = re.compile(
    r'^[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))(?:[eE][+-]?[0-9]+)?$'
)
_OFFSET_DATETIME_PATTERN = re.compile(
    r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2}$'
)

__all__ = [
    'CsvRow',
    'parse_bool',
    'parse_date',
    'parse_finite_float',
    'parse_identifier',
    'parse_integer',
    'parse_offset_datetime',
    'read_csv_table',
    'read_toml_table',
]


def read_toml_table(path: Path) -> dict[str, Any]:
    """Parse a UTF-8 TOML file and require a top-level table.

    :param path: TOML file to read.
    :returns: Parsed mutable dictionary for normalization by the caller.
    :raises VrpInputError: If the file cannot be read or parsed.
    """
    try:
        with path.open('rb') as file:
            value = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise VrpInputError(file=path.name, reason=str(error)) from error
    if type(value) is not dict:
        raise VrpInputError(file=path.name, reason='top-level value must be a table')
    _require_finite_toml_values(value, path.name)
    return value


def read_csv_table(path: Path, columns: tuple[str, ...]) -> tuple[CsvRow, ...]:
    """Read a strict UTF-8 CSV table with the specified ordered columns.

    :param path: CSV file to read.
    :param columns: Exact header names in required order.
    :returns: Rows as text mappings in source order.
    :raises VrpInputError: If the file encoding, header, or row shape is invalid.
    """
    try:
        with path.open('r', encoding='utf-8', newline='') as file:
            reader = csv.reader(file, strict=True)
            try:
                header = next(reader)
            except StopIteration as error:
                raise VrpInputError(
                    file=path.name, row=1, reason='missing header'
                ) from error
            if len(header) != len(set(header)):
                duplicate = next(value for value in header if header.count(value) > 1)
                raise VrpInputError(
                    file=path.name, row=1, column=duplicate, reason='duplicate header'
                )
            if tuple(header) != columns:
                column = next(
                    (
                        expected
                        for index, expected in enumerate(columns)
                        if index >= len(header) or header[index] != expected
                    ),
                    header[-1] if header else None,
                )
                raise VrpInputError(
                    file=path.name,
                    row=1,
                    column=column,
                    reason=f'header must be exactly {", ".join(columns)}',
                )
            rows: list[CsvRow] = []
            for row_number, values in enumerate(reader, start=2):
                if len(values) != len(columns):
                    raise VrpInputError(
                        file=path.name,
                        row=row_number,
                        reason=f'expected {len(columns)} fields, found {len(values)}',
                    )
                rows.append(dict(zip(columns, values, strict=True)))
    except VrpInputError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise VrpInputError(file=path.name, reason=str(error)) from error
    return tuple(rows)


def parse_identifier(
    value: str,
    *,
    file: str,
    row: int,
    column: str,
) -> str:
    """Validate one exact, nonempty UTF-8 identifier field."""
    if not value or value.strip() != value:
        raise VrpInputError(
            file=file,
            row=row,
            column=column,
            reason='identifier must be nonempty and have no surrounding whitespace',
        )
    return value


def parse_integer(
    value: str,
    *,
    file: str,
    row: int,
    column: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Parse a base-10 integer field and enforce optional bounds."""
    if not _INTEGER_PATTERN.fullmatch(value):
        raise VrpInputError(
            file=file,
            row=row,
            column=column,
            reason='must use base-10 integer notation',
        )
    parsed = int(value, 10)
    if minimum is not None and parsed < minimum:
        raise VrpInputError(
            file=file, row=row, column=column, reason=f'must be at least {minimum}'
        )
    if maximum is not None and parsed > maximum:
        raise VrpInputError(
            file=file, row=row, column=column, reason=f'must be at most {maximum}'
        )
    return parsed


def parse_finite_float(
    value: str,
    *,
    file: str,
    row: int,
    column: str,
    minimum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    """Parse a finite decimal number into IEEE 754 binary64."""
    if not _FLOAT_PATTERN.fullmatch(value):
        raise VrpInputError(
            file=file, row=row, column=column, reason='must use finite decimal notation'
        )
    try:
        parsed = float(value)
    except ValueError as error:
        raise VrpInputError(
            file=file, row=row, column=column, reason='must be a decimal number'
        ) from error
    if not math.isfinite(parsed):
        raise VrpInputError(
            file=file, row=row, column=column, reason='must be finite after conversion'
        )
    if minimum is not None:
        invalid = parsed <= minimum if strict_minimum else parsed < minimum
        if invalid:
            relation = 'greater than' if strict_minimum else 'at least'
            raise VrpInputError(
                file=file,
                row=row,
                column=column,
                reason=f'must be {relation} {minimum}',
            )
    return parsed


def parse_bool(value: str, *, file: str, row: int, column: str) -> bool:
    """Parse the exact CSV Boolean spellings ``true`` and ``false``."""
    if value == 'true':
        return True
    if value == 'false':
        return False
    raise VrpInputError(
        file=file, row=row, column=column, reason="must be exactly 'true' or 'false'"
    )


def parse_date(value: str, *, file: str, row: int, column: str) -> date:
    """Parse an ISO calendar date."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise VrpInputError(
            file=file, row=row, column=column, reason='must be an ISO date'
        ) from error
    if parsed.isoformat() != value:
        raise VrpInputError(
            file=file, row=row, column=column, reason='must use YYYY-MM-DD format'
        )
    return parsed


def parse_offset_datetime(
    value: str,
    *,
    file: str,
    row: int,
    column: str,
) -> datetime:
    """Parse an ISO timestamp containing a numeric UTC offset."""
    if not _OFFSET_DATETIME_PATTERN.fullmatch(value):
        raise VrpInputError(
            file=file,
            row=row,
            column=column,
            reason='must be an ISO timestamp with a numeric UTC offset',
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise VrpInputError(
            file=file, row=row, column=column, reason='must be an ISO timestamp'
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise VrpInputError(
            file=file,
            row=row,
            column=column,
            reason='timestamp must include a numeric UTC offset',
        )
    return parsed


def _require_finite_toml_values(
    value: object, filename: str, location: str = ''
) -> None:
    if type(value) is float and not math.isfinite(value):
        raise VrpInputError(
            file=filename,
            column=location or None,
            reason='TOML real values must be finite',
        )
    if type(value) is dict:
        for key, item in value.items():
            child = f'{location}.{key}' if location else str(key)
            _require_finite_toml_values(item, filename, child)
    elif type(value) is list:
        for index, item in enumerate(value):
            _require_finite_toml_values(item, filename, f'{location}[{index}]')
