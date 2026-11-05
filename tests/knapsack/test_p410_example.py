"""Tests for the runnable P410 knapsack example.

Created: 2026-10-29T18:38:00+00:00
Author: Michel Bierlaire, EPFL
"""

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.records.results import RunResult


def test_example_writes_logs_under_the_invocation_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = Path(__file__).parents[2] / 'examples' / 'knapsack' / 'p410' / 'run.py'
    namespace = runpy.run_path(str(script))
    run_example = cast(Callable[[], RunResult], namespace['run_example'])
    monkeypatch.chdir(tmp_path)

    result = run_example()

    expected_directory = tmp_path / 'multiobjective-vns-logs'
    assert result.event_log_path is not None
    assert result.summary_path is not None
    assert result.event_log_path.parent == expected_directory
    assert result.summary_path.parent == expected_directory
    assert result.event_log_path.exists()
    assert result.summary_path.exists()
    assert result.counters.candidate_attempts <= 30
