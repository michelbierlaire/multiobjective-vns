"""Run an overnight Solomon C101-25 search from its saved Pareto archive.

Created: 2026-11-12T23:25:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import csv
import hashlib
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
from multiobjective_vns.multiday_vrp.operators.catalog import VrpOperatorId
from multiobjective_vns.pareto_snapshots import read_pareto_snapshot

EXPERIMENT_DIRECTORY = Path(__file__).resolve().parent
INSTANCE_MANIFEST = EXPERIMENT_DIRECTORY / 'input' / 'instance.toml'
SOURCE_LOG_DIRECTORY = (
    EXPERIMENT_DIRECTORY / 'run-logs-6h-warmstart-20261112-0842-seed-0'
)
LOG_DIRECTORY = (
    EXPERIMENT_DIRECTORY / 'run-logs-overnight-symmetry-operators-20261112-seed-0'
)
SOURCE_PROMOTED_CATALOG_PATH = (
    EXPERIMENT_DIRECTORY / 'multiobjective-vns-promoted-compositions.json'
)
PROMOTED_CATALOG_PATH = LOG_DIRECTORY / 'multiobjective-vns-promoted-compositions.json'
PARETO_CSV_PATH = LOG_DIRECTORY / 'pareto-solutions-live.csv'
PARETO_ADMISSIONS_PATH = LOG_DIRECTORY / 'pareto-admissions.jsonl'
RUN_CONFIG_PATH = LOG_DIRECTORY / 'run-config.json'
OPTIMUM_RECORD_PATH = LOG_DIRECTORY / 'benchmark-optimum-found.json'
MONITOR_STATUS_PATH = LOG_DIRECTORY / 'monitor-status.json'
LOCAL_TIMEZONE = ZoneInfo('Europe/Zurich')
OPTIMAL_DISTANCE = 191.3
RUN_DEADLINE = datetime(2026, 11, 13, 7, 0, tzinfo=LOCAL_TIMEZONE)
FINALIZATION_RESERVE_SECONDS = 90
TOUR_OPERATOR_SPECS = frozenset(
    {
        'vrp.service_windows.short_tour_regret_insertion',
        'vrp.service_windows.backtracking_tour_insertion',
        'vrp.service_windows.window_feasible_delivery_edge_exchange',
    }
)


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


def _wall_time_budget_until_deadline() -> int:
    remaining = (RUN_DEADLINE - datetime.now(LOCAL_TIMEZONE)).total_seconds()
    budget = math.floor(remaining - FINALIZATION_RESERVE_SECONDS)
    if budget <= 0:
        raise RuntimeError('not enough time remains to start the overnight run')
    return budget


def _promotion_count() -> int:
    if not PROMOTED_CATALOG_PATH.exists():
        return 0
    document = json.loads(PROMOTED_CATALOG_PATH.read_text(encoding='utf-8'))
    record = document.get('catalogs', {}).get('solomon-c101-25-three-vehicles-v2', {})
    return len(record.get('promotions', ()))


def _payload_key(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _admission_log_count() -> int:
    if not PARETO_ADMISSIONS_PATH.exists():
        return 0
    with PARETO_ADMISSIONS_PATH.open(encoding='utf-8') as stream:
        return sum(1 for _ in stream)


def _write_catalog_candidate(
    root: dict[str, object],
    problem_instance_id: str,
    compatibility: dict[str, object],
    promotions: list[dict[str, object]],
) -> None:
    catalogs = dict(root['catalogs'])
    catalogs[problem_instance_id] = {
        'compatibility': compatibility,
        'promotions': promotions,
    }
    _atomic_write(
        PROMOTED_CATALOG_PATH,
        json.dumps(
            {'format_version': root['format_version'], 'catalogs': catalogs},
            indent=2,
        )
        + '\n',
    )


def _migrate_promoted_catalog(search: MultiobjectiveVNS, application: object):
    """Preserve old promotions and validate the compatible ones for this run."""
    original_text = PROMOTED_CATALOG_PATH.read_text(encoding='utf-8')
    root = json.loads(original_text)
    problem_id = str(application.problem_instance_id)
    old_record = root.get('catalogs', {}).get(problem_id)
    if old_record is None:
        search._prepare_state(None)
        return {
            'source_count': 0,
            'loaded_count': 0,
            'skipped_count': 0,
            'legacy_record_key': None,
            'skipped_reasons': [],
        }

    expected = search._promotion_catalog_compatibility()
    if old_record.get('compatibility') == expected:
        search._prepare_state(None)
        count = len(old_record.get('promotions', ()))
        return {
            'source_count': count,
            'loaded_count': count,
            'skipped_count': 0,
            'legacy_record_key': None,
            'skipped_reasons': [],
        }

    backup_path = (
        LOG_DIRECTORY / 'multiobjective-vns-promoted-compositions.before-migration.json'
    )
    _atomic_write(backup_path, original_text)
    catalogs = dict(root['catalogs'])
    stamp = datetime.now(LOCAL_TIMEZONE).strftime('%Y%m%d-%H%M%S')
    legacy_key = f'{problem_id}::legacy::{stamp}'
    while legacy_key in catalogs:
        legacy_key += '-copy'
    catalogs[legacy_key] = old_record
    migration_root: dict[str, object] = {
        'format_version': root['format_version'],
        'catalogs': catalogs,
    }

    expected_by_id = {item['operator_id']: item for item in expected['operators']}
    available = set(expected_by_id)
    compatible_promotions: list[dict[str, object]] = []
    skipped_reasons: list[str] = []
    old_promotions = old_record.get('promotions', [])
    if not isinstance(old_promotions, list):
        raise ValueError('saved promoted catalog promotions field must be an array')

    try:
        _write_catalog_candidate(
            migration_root, problem_id, expected, compatible_promotions
        )
        search._prepare_state(None)
        for index, promotion in enumerate(old_promotions, start=1):
            component_ids = promotion.get('component_operator_ids', [])
            component_configurations = promotion.get('component_configuration_ids', [])
            if len(component_ids) != 2 or len(component_configurations) != 2:
                skipped_reasons.append(
                    f'promotion {index}: malformed component metadata'
                )
                continue
            unavailable = [item for item in component_ids if item not in available]
            if unavailable:
                skipped_reasons.append(
                    f'promotion {index}: component operator no longer registered'
                )
                continue
            changed_configuration = any(
                component_id in expected_by_id
                and expected_by_id[component_id]['configuration_id'] != configuration_id
                for component_id, configuration_id in zip(
                    component_ids, component_configurations, strict=True
                )
            )
            if changed_configuration:
                skipped_reasons.append(
                    f'promotion {index}: component configuration changed'
                )
                continue
            candidate_promotions = [*compatible_promotions, promotion]
            _write_catalog_candidate(
                migration_root, problem_id, expected, candidate_promotions
            )
            try:
                search._prepare_state(None)
            except Exception as error:
                skipped_reasons.append(
                    f'promotion {index}: {type(error).__name__}: {error}'
                )
                _write_catalog_candidate(
                    migration_root, problem_id, expected, compatible_promotions
                )
                search._prepare_state(None)
                continue
            compatible_promotions.append(promotion)
            available.add(promotion['operator_id'])
    except BaseException:
        _atomic_write(PROMOTED_CATALOG_PATH, original_text)
        raise

    _write_catalog_candidate(
        migration_root, problem_id, expected, compatible_promotions
    )
    search._prepare_state(None)
    return {
        'source_count': len(old_promotions),
        'loaded_count': len(compatible_promotions),
        'skipped_count': len(old_promotions) - len(compatible_promotions),
        'legacy_record_key': legacy_key,
        'backup_file': str(backup_path),
        'skipped_reasons': skipped_reasons,
    }


def _objective_ids(application: object) -> tuple[str, ...]:
    return tuple(
        str(objective.objective_id)
        for objective in application.objective_declaration.objectives
    )


def _read_warm_start(
    application: object,
) -> tuple[tuple[object, ...], set[str], Path]:
    snapshots = tuple(SOURCE_LOG_DIRECTORY.glob('*.pareto.json'))
    if not snapshots:
        raise FileNotFoundError(f'no saved Pareto snapshot in {SOURCE_LOG_DIRECTORY}')
    source_path = max(snapshots, key=lambda path: path.stat().st_mtime_ns)
    snapshot = read_pareto_snapshot(source_path)
    if snapshot.problem_instance_id != str(application.problem_instance_id):
        raise ValueError('saved Pareto archive belongs to a different problem instance')
    if snapshot.objective_ids != _objective_ids(application):
        raise ValueError('saved Pareto archive objectives do not match this instance')
    solutions = tuple(
        application.solution_codec.restore(payload)
        for payload in snapshot.solution_payloads
    )
    if not solutions:
        raise ValueError('saved Pareto archive is empty')
    baseline_keys = {
        _payload_key(application.solution_codec.capture(solution))
        for solution in solutions
    }
    return solutions, baseline_keys, source_path


def _evaluate_payloads(application: object, payloads: tuple[bytes, ...]):
    solutions = tuple(
        application.solution_codec.restore(payload) for payload in payloads
    )
    evaluations = tuple(
        application.application_evaluator.evaluate(
            solution, application.objective_declaration
        )
        for solution in solutions
    )
    return solutions, evaluations


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
            f'{route.vehicle_id}: '
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
                        _local_iso(snapshot_mtime)
                        if snapshot_mtime is not None
                        else None
                    ),
                    'time_zone': 'Europe/Zurich',
                },
                indent=2,
            )
            + '\n',
        )
        return


def _append_admission(
    application: object,
    payload: bytes,
    snapshot_path: Path,
    sequence: int,
) -> dict[str, object] | None:
    solution = application.solution_codec.restore(payload)
    evaluation = application.application_evaluator.evaluate(
        solution, application.objective_declaration
    )
    values = evaluation.objective_values
    if not evaluation.representation_valid or values is None:
        return None
    objective_ids = _objective_ids(application)
    record: dict[str, object] = {
        'admission_number': sequence,
        'observed_at': _local_iso(),
        'time_zone': 'Europe/Zurich',
        'objective_ids': list(objective_ids),
        'objective_values': list(values),
        'objectives': dict(zip(objective_ids, values, strict=True)),
        'route_count': len(solution.routes),
        'solution_payload_sha256': _payload_key(payload),
        'snapshot_file': snapshot_path.name,
    }
    with PARETO_ADMISSIONS_PATH.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({'pareto_admission': record}), flush=True)
    return record


def _monitor_snapshots(
    application: object,
    stop: threading.Event,
    seen_payload_keys: set[str],
    baseline_count: int,
) -> None:
    last_snapshot_signature: tuple[str, int, int] | None = None
    last_status_write = 0.0
    last_csv_write = 0.0
    admission_count = 0
    latest_snapshot_name: str | None = None
    latest_pareto_size = baseline_count
    objective_ids = _objective_ids(application)

    while True:
        snapshots = tuple(LOG_DIRECTORY.glob('*.pareto.json'))
        changed = False
        snapshot_mtime: float | None = None
        if snapshots:
            snapshot_path = max(snapshots, key=lambda path: path.stat().st_mtime_ns)
            stat = snapshot_path.stat()
            signature = (snapshot_path.name, stat.st_mtime_ns, stat.st_size)
            latest_snapshot_name = snapshot_path.name
            snapshot_mtime = stat.st_mtime
            if signature != last_snapshot_signature:
                try:
                    snapshot = read_pareto_snapshot(snapshot_path)
                    if snapshot.problem_instance_id != str(
                        application.problem_instance_id
                    ):
                        raise ValueError(
                            'new Pareto snapshot has a different problem instance'
                        )
                    latest_pareto_size = len(snapshot.solution_payloads)
                    fresh_payloads = []
                    for payload in snapshot.solution_payloads:
                        key = _payload_key(payload)
                        if key not in seen_payload_keys:
                            seen_payload_keys.add(key)
                            fresh_payloads.append(payload)
                    for payload in fresh_payloads:
                        admission_count += 1
                        _append_admission(
                            application, payload, snapshot_path, admission_count
                        )
                    changed = True
                    last_snapshot_signature = signature
                except (OSError, ValueError, TypeError):
                    # Retry malformed or transient reads at the next interval.
                    pass

        now = time.monotonic()
        if changed and now - last_csv_write >= 60.0:
            try:
                if snapshots:
                    current_snapshot = read_pareto_snapshot(snapshot_path)
                    solutions, evaluations = _evaluate_payloads(
                        application, current_snapshot.solution_payloads
                    )
                    _write_pareto_csv(
                        PARETO_CSV_PATH, solutions, evaluations, objective_ids
                    )
                    _record_optimum_if_present(
                        solutions,
                        evaluations,
                        objective_ids,
                        _local_iso(),
                        snapshot_mtime,
                    )
                    last_csv_write = now
            except (OSError, ValueError, TypeError):
                pass

        if now - last_status_write >= 30.0:
            status = {
                'state': 'running',
                'updated_at': _local_iso(),
                'pid': os.getpid(),
                'latest_pareto_snapshot': latest_snapshot_name,
                'pareto_size': latest_pareto_size,
                'warm_start_size': baseline_count,
                'new_pareto_admissions_observed': admission_count,
                'promoted_operator_count': _promotion_count(),
                'benchmark_optimum_found': OPTIMUM_RECORD_PATH.exists(),
                'pareto_admissions_file': str(PARETO_ADMISSIONS_PATH),
            }
            _atomic_write(MONITOR_STATUS_PATH, json.dumps(status, indent=2) + '\n')
            last_status_write = now
        if stop.wait(0.5):
            break


def main() -> None:
    if LOG_DIRECTORY.exists():
        raise FileExistsError(f'experiment output already exists: {LOG_DIRECTORY}')
    LOG_DIRECTORY.mkdir(parents=True)
    _atomic_write(
        PROMOTED_CATALOG_PATH,
        SOURCE_PROMOTED_CATALOG_PATH.read_text(encoding='utf-8'),
    )
    started_at = _local_iso()
    application = load_vrp_application(INSTANCE_MANIFEST)
    (
        warm_start_solutions,
        warm_start_payload_keys,
        source_snapshot_path,
    ) = _read_warm_start(application)
    warm_start_size = len(warm_start_solutions)
    initial_evaluations = tuple(
        application.application_evaluator.evaluate(
            solution, application.objective_declaration
        )
        for solution in warm_start_solutions
    )
    if any(
        not evaluation.representation_valid or evaluation.objective_values is None
        for evaluation in initial_evaluations
    ):
        raise ValueError(
            'saved Pareto archive contains a solution that no longer evaluates'
        )
    wall_time_budget_seconds = _wall_time_budget_until_deadline()
    _record_optimum_if_present(
        warm_start_solutions,
        initial_evaluations,
        _objective_ids(application),
        started_at,
        source_snapshot_path.stat().st_mtime,
    )

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
            # Promoted operators are standalone choices. A zero dynamic-pair
            # probability keeps selection within that pool, including promotions.
            composition_selection_probability=0.0,
            feedback_mu=0.0,
            budget_limits=BudgetLimits(
                max_wall_time_seconds=wall_time_budget_seconds,
                max_candidate_attempts=10_000_000,
                max_evaluation_requests=100_000_000,
            ),
            random_seed=0,
        ),
        promoted_composition_catalog_path=PROMOTED_CATALOG_PATH,
    )
    promotion_migration = _migrate_promoted_catalog(search, application)
    _atomic_write(
        RUN_CONFIG_PATH,
        json.dumps(
            {
                'instance': 'Solomon C101-25, three vehicles',
                'started_at': started_at,
                'time_zone': 'Europe/Zurich',
                'run_deadline_local_time': RUN_DEADLINE.isoformat(),
                'wall_time_budget_seconds': wall_time_budget_seconds,
                'finalization_reserve_seconds': FINALIZATION_RESERVE_SECONDS,
                'warm_start_snapshot': str(source_snapshot_path),
                'warm_start_solution_count': warm_start_size,
                'solution_identity_configuration_id': (
                    application.solution_identity.configuration_id
                ),
                'pickup_symmetry_groups': [
                    {
                        'group_id': group.group_id,
                        'package_ids': list(group.package_ids),
                    }
                    for group in application.instance.pickup_symmetry_groups
                ],
                'tour_search_operator_ids': [
                    registration.operator_id.to_log_text()
                    for registration in application.operators
                    if isinstance(registration.operator_id, VrpOperatorId)
                    and registration.operator_id.operator_spec_id in TOUR_OPERATOR_SPECS
                ],
                'promoted_composition_catalog': str(PROMOTED_CATALOG_PATH),
                'promoted_operator_count_in_source_catalog': promotion_migration[
                    'source_count'
                ],
                'promoted_operator_count_loaded': promotion_migration['loaded_count'],
                'promoted_operator_count_skipped': promotion_migration['skipped_count'],
                'promoted_catalog_legacy_record_key': promotion_migration[
                    'legacy_record_key'
                ],
                'promoted_catalog_backup': promotion_migration.get('backup_file'),
                'promoted_catalog_skipped_reasons': promotion_migration[
                    'skipped_reasons'
                ],
                'composition_selection_probability': 0.0,
                'feedback_mu': 0.0,
                'random_seed': 0,
                'objectives': list(_objective_ids(application)),
            },
            indent=2,
        )
        + '\n',
    )
    stop_monitor = threading.Event()
    monitor = threading.Thread(
        target=_monitor_snapshots,
        args=(application, stop_monitor, warm_start_payload_keys, warm_start_size),
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
                'warm_start_archive': str(source_snapshot_path),
                'warm_start_size': warm_start_size,
                'promoted_operator_count': _promotion_count(),
                'composition_selection_probability': 0.0,
                'benchmark_optimum_found': OPTIMUM_RECORD_PATH.exists(),
                'pareto_admissions_file': str(PARETO_ADMISSIONS_PATH),
            },
            indent=2,
        )
        + '\n',
    )
    monitor.start()
    try:
        result = search.run(
            initial_solutions=warm_start_solutions,
            log_directory=LOG_DIRECTORY,
            progress_report_interval_seconds=60.0,
        )
        stop_monitor.set()
        monitor.join()
        pareto_records = result.search_state.pareto_solutions()
        final_records = tuple(
            (record.solution, record.evaluation_record.application_evaluation)
            for record in pareto_records
            if record.evaluation_record.application_evaluation is not None
        )
        final_solutions = tuple(solution for solution, _ in final_records)
        final_evaluations = tuple(evaluation for _, evaluation in final_records)
        objective_ids = _objective_ids(application)
        if result.pareto_snapshot_path is not None:
            admission_count = _admission_log_count()
            for solution in final_solutions:
                payload = application.solution_codec.capture(solution)
                payload_key = _payload_key(payload)
                if payload_key in warm_start_payload_keys:
                    continue
                warm_start_payload_keys.add(payload_key)
                admission_count += 1
                _append_admission(
                    application,
                    payload,
                    result.pareto_snapshot_path,
                    admission_count,
                )
        _write_pareto_csv(
            LOG_DIRECTORY / 'pareto-solutions.csv',
            final_solutions,
            final_evaluations,
            objective_ids,
        )
        _record_optimum_if_present(
            final_solutions, final_evaluations, objective_ids, _local_iso(), None
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
            'warm_start_size': warm_start_size,
            'new_pareto_admissions_observed': (_admission_log_count()),
            'promoted_operator_count': _promotion_count(),
            'composition_selection_probability': 0.0,
            'benchmark_optimum_found': OPTIMUM_RECORD_PATH.exists(),
            'event_log': str(result.event_log_path),
            'summary': str(result.summary_path),
            'pareto_snapshot': (
                str(result.pareto_snapshot_path)
                if result.pareto_snapshot_path is not None
                else None
            ),
            'pareto_admissions_file': str(PARETO_ADMISSIONS_PATH),
        }
        _atomic_write(MONITOR_STATUS_PATH, json.dumps(end_status, indent=2) + '\n')
        print(json.dumps(end_status, indent=2), flush=True)
    except BaseException as error:
        stop_monitor.set()
        monitor.join()
        _atomic_write(
            MONITOR_STATUS_PATH,
            json.dumps(
                {
                    'state': 'failed',
                    'started_at': started_at,
                    'updated_at': _local_iso(),
                    'error': f'{type(error).__name__}: {error}',
                    'warm_start_size': warm_start_size,
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
