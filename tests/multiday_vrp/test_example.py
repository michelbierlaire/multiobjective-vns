"""Smoke test the runnable minimal multi-day VRP example.

Created: 2026-11-03T23:58:35+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from multiobjective_vns.records.results import RunResult
from multiobjective_vns.records.status import ApplicationAssessment


def test_minimal_vrp_example_runs_and_writes_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = (
        Path(__file__).parents[2] / 'examples' / 'multiday_vrp' / 'minimal' / 'run.py'
    )
    namespace = runpy.run_path(str(script), run_name='vrp_example_under_test')
    run_example = cast(Callable[..., RunResult], namespace['run_example'])

    monkeypatch.chdir(tmp_path)
    result = run_example(log_directory=tmp_path / 'logs')

    assert result.search_state.pareto_solutions()
    assert len(result.search_state.pareto_solutions()) == 2
    assert len(result.candidate_outcomes) == 2
    assert result.counters.candidate_attempts == 1
    assert result.counters.evaluation_requests == 1
    assert result.candidate_outcomes[0].assessment is ApplicationAssessment.VALID
    assert result.candidate_outcomes[1].assessment is ApplicationAssessment.VALID
    assert result.diagnostics == ()
    assert result.event_log_path is not None and result.event_log_path.exists()
    assert result.summary_path is not None and result.summary_path.exists()
