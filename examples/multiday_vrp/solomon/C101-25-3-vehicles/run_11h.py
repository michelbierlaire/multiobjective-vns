"""Run the three-vehicle Solomon C101-25 experiment for eleven hours.

The generic engine atomically writes Pareto snapshots on every archive update
and persists promoted compositions after each promotion. This runner also
maintains a readable Pareto CSV and records the first observation of the
published zero-violation distance optimum.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from multiobjective_vns import BudgetLimits, MultiobjectiveVNS, MultiobjectiveVNSConfig
from multiobjective_vns.multiday_vrp import load_vrp_application
from multiobjective_vns.pareto_snapshots import read_pareto_snapshot
from run_1h import build_initial_solution

EXPERIMENT_DIRECTORY = Path(__file__).resolve().parent
INSTANCE_MANIFEST = EXPERIMENT_DIRECTORY / 'input' / 'instance.toml'
LOG_DIRECTORY = EXPERIMENT_DIRECTORY / 'run-logs-11h-20261110-seed-0'
PROMOTED_CATALOG_PATH = (
    EXPERIMENT_DIRECTORY / 'multiobjective-vns-promoted-compositions.json'
)
PARETO_CSV_PATH = LOG_DIRECTORY / 'pareto-solutions-live.csv'
OPTIMUM_RECORD_PATH = LOG_DIRECTORY / 'benchmark-optimum-found.json'
MONITOR_STATUS_PATH = LOG_DIRECTORY / 'monitor-status.json'
LOCAL_TIMEZONE = ZoneInfo('Europe/Zurich')
OPTIMAL_DISTANCE = 191.3


def _atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _local_iso(timestamp: float | None = None) -> str:
    instant = (
        datetime.now(timezone.utc)
        if timestamp is None
        else datetime.fromtimestamp(timestamp, tz=timezone.utc)
    )
    return instant.astimezone(LOCAL_TIMEZONE).isoformat(timespec='seconds')


def _promotion_count() -> int:
    if not PROMOTED_CATALOG_PATH.exists():
        return 0
    document = json.loads(PROMOTED_CATALOG_PATH.read_text(encoding='utf-8'))
    catalogs = document.get('catalogs', {})
    record = catalogs.get('solomon-c101-25-three-vehicles-v2', {})
    return len(record.get('promotions', ()))


def _evaluate_payloads(application: object, payloads: tuple[bytes, ...]):
    solutions = tuple(application.solution_codec.restore(payload) for payload in payloads)
    evaluations = tuple(
        application.application_evaluator.evaluate(
            solution, application.objective_declaration
        )
        for solution in solutions
    )
    objective_ids = tuple(
        str(objective.objective_id)
        for objective in application.objective_declaration.objectives
    )
    return solutions, evaluations, objective_ids


def _write_pareto_csv(
    path: Path,
    solutions: tuple[object, ...],
    evaluations: tuple[object, ...],
    objective_ids: tuple[str, ...],
) -> None:
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(('archive_index', *objective_ids, 'routes'))
    for index, (solution, evaluation) in enumerate(
        zip(solutions, evaluations, strict=True), start=1
    ):
        objective_values = evaluation.objective_values
        if objective_values is None:
            continue
        routes = ' | '.join(
            f"{route.vehicle_id}: "
            + ' '.join(
                stop.activity_ref.activity_id
                for stop in route.stops
                if getattr(stop, 'activity_ref', None) is not None
                and stop.activity_ref.kind == 'delivery'
            )
            for route in solution.routes
        )
        writer.writerow((index, *objective_values, routes))
    _atomic_write(path, output.getvalue())


def _record_optimum_if_present(
    solutions: tuple[object, ...],
    evaluations: tuple[object, ...],
    objective_ids: tuple[str, ...],
    observed_at: str,
    snapshot_mtime: float | None,
) -> None:
    if OPTIMUM_RECORD_PATH.exists():
        return
    indices = {objective_id: index for index, objective_id in enumerate(objective_ids)}
    distance_index = indices.get('total-distance')
    violation_index = indices.get('time-window-violation')
    if distance_index is None or violation_index is None:
        return
    for solution, evaluation in zip(solutions, evaluations, strict=True):
        values = evaluation.objective_values
        if (
            not evaluation.representation_valid
            or evaluation.feasible is not True
            or values is None
            or len(solution.routes) != 3
            or not math.isclose(
                values[distance_index], OPTIMAL_DISTANCE, rel_tol=0.0, abs_tol=1e-8
            )
            or not math.isclose(values[violation_index], 0.0, rel_tol=0.0, abs_tol=1e-8)
        ):
            continue
        _atomic_write(
            OPTIMUM_RECORD_PATH,
            json.dumps(
                {
                    'benchmark': 'Solomon C101-25, three vehicles',
                    'published_distance_optimum': OPTIMAL_DISTANCE,
                    'objective_ids': list(objective_ids),
                    'objective_values': list(values),
                    'route_count': len(solution.routes),
                    'first_observed_in_persisted_pareto_snapshot_at': observed_at,
                    'snapshot_file_modified_at': (
                        _local_iso(snapshot_mtime) if snapshot_mtime is not None else None
                    ),
                    'time_zone': 'Europe/Zurich',
                },
                indent=2,
            )
            + '\n',
        )
        return


def _monitor_snapshots(application: object, stop: threading.Event) -> None:
    last_snapshot_signature: tuple[str, int, int] | None = None
    last_status_write = 0.0
    while not stop.is_set():
        snapshots = tuple(LOG_DIRECTORY.glob('*.pareto.json'))
        if snapshots:
            snapshot_path = max(snapshots, key=lambda path: path.stat().st_mtime_ns)
            stat = snapshot_path.stat()
            signature = (snapshot_path.name, stat.st_mtime_ns, stat.st_size)
            if signature != last_snapshot_signature:
                try:
                    snapshot = read_pareto_snapshot(snapshot_path)
                    solutions, evaluations, objective_ids = _evaluate_payloads(
                        application, snapshot.solution_payloads
                    )
                    observed_at = _local_iso()
                    _write_pareto_csv(
                        PARETO_CSV_PATH, solutions, evaluations, objective_ids
                    )
                    _record_optimum_if_present(
                        solutions,
                        evaluations,
                        objective_ids,
                        observed_at,
                        stat.st_mtime,
                    )
                    last_snapshot_signature = signature
                except (OSError, ValueError, TypeError):
                    # A new invocation snapshot may appear while being atomically
                    # replaced. Retry it on the next poll.
                    pass

        now = time.monotonic()
        if now - last_status_write >= 60.0:
            optimum_found = OPTIMUM_RECORD_PATH.exists()
            status = {
                'state': 'running',
                'updated_at': _local_iso(),
                'latest_pareto_snapshot': (
                    snapshot_path.name if snapshots else None
                ),
                'pareto_size': (
                    len(snapshot.solution_payloads)
                    if snapshots and 'snapshot' in locals()
                    else None
                ),
                'promoted_operator_count': _promotion_count(),
                'benchmark_optimum_found': optimum_found,
            }
            _atomic_write(MONITOR_STATUS_PATH, json.dumps(status, indent=2) + '\n')
            last_status_write = now
        stop.wait(2.0)


def main() -> None:
    if LOG_DIRECTORY.exists():
        raise FileExistsError(f'experiment output already exists: {LOG_DIRECTORY}')
    LOG_DIRECTORY.mkdir(parents=True)
    started_at = _local_iso()
    application = load_vrp_application(INSTANCE_MANIFEST)
    initial_solution = build_initial_solution(application)
    search = MultiobjectiveVNS(
        solution_codec=application.solution_codec,
        solution_identity=application.solution_identity,
        objective_declaration=application.objective_declaration,
        operators=application.operators,
        pre_registered_compositions=application.pre_registered_compositions,
        application_evaluator=application.application_evaluator,
        problem_instance_id=application.problem_instance_id,
        operator_groups=application.operator_groups,
        config=MultiobjectiveVNSConfig(
            max_candidates_per_attempt=10,
            composition_selection_probability=0.30,
            feedback_mu=0.0,
            budget_limits=BudgetLimits(
                max_wall_time_seconds=11 * 60 * 60,
                max_candidate_attempts=10_000_000,
                max_evaluation_requests=100_000_000,
            ),
            random_seed=0,
        ),
        promoted_composition_catalog_path=PROMOTED_CATALOG_PATH,
    )
    stop_monitor = threading.Event()
    monitor = threading.Thread(
        target=_monitor_snapshots,
        args=(application, stop_monitor),
        name='pareto-snapshot-monitor',
        daemon=True,
    )
    _atomic_write(
        MONITOR_STATUS_PATH,
        json.dumps(
            {
                'state': 'starting',
                'started_at': started_at,
                'pid': os.getpid(),
                'promoted_operator_count': _promotion_count(),
                'benchmark_optimum_found': OPTIMUM_RECORD_PATH.exists(),
            },
            indent=2,
        )
        + '\n',
    )
    monitor.start()
    try:
        result = search.run(
            initial_solutions=(initial_solution,),
            log_directory=LOG_DIRECTORY,
            progress_report_interval_seconds=60.0,
        )
        pareto_records = result.search_state.pareto_solutions()
        final_records = tuple(
            (record.solution, record.evaluation_record.application_evaluation)
            for record in pareto_records
            if record.evaluation_record.application_evaluation is not None
        )
        final_solutions = tuple(solution for solution, _ in final_records)
        final_evaluations = tuple(evaluation for _, evaluation in final_records)
        objective_ids = tuple(
            str(objective.objective_id)
            for objective in application.objective_declaration.objectives
        )
        _write_pareto_csv(
            LOG_DIRECTORY / 'pareto-solutions.csv',
            final_solutions,
            final_evaluations,
            objective_ids,
        )
        _record_optimum_if_present(
            final_solutions,
            final_evaluations,
            objective_ids,
            _local_iso(),
            None,
        )
        end_status = {
            'state': 'complete',
            'started_at': started_at,
            'finished_at': _local_iso(),
            'termination': result.termination_reason.value,
            'elapsed_seconds': result.executive_summary.elapsed_seconds,
            'candidate_attempts': result.counters.candidate_attempts,
            'evaluation_requests': result.counters.evaluation_requests,
            'pareto_size': len(pareto_records),
            'promoted_operator_count': _promotion_count(),
            'benchmark_optimum_found': OPTIMUM_RECORD_PATH.exists(),
            'event_log': str(result.event_log_path),
            'summary': str(result.summary_path),
            'pareto_snapshot': (
                str(result.pareto_snapshot_path)
                if result.pareto_snapshot_path is not None
                else None
            ),
        }
        _atomic_write(MONITOR_STATUS_PATH, json.dumps(end_status, indent=2) + '\n')
        print(json.dumps(end_status, indent=2), flush=True)
    except BaseException as error:
        _atomic_write(
            MONITOR_STATUS_PATH,
            json.dumps(
                {
                    'state': 'failed',
                    'started_at': started_at,
                    'updated_at': _local_iso(),
                    'error': f'{type(error).__name__}: {error}',
                    'promoted_operator_count': _promotion_count(),
                    'benchmark_optimum_found': OPTIMUM_RECORD_PATH.exists(),
                },
                indent=2,
            )
            + '\n',
        )
        raise
    finally:
        stop_monitor.set()
        monitor.join(timeout=5.0)


if __name__ == '__main__':
    main()
