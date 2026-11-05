"""Package-provided JSON Lines and human-readable run logger.

Created: 2026-10-29T14:26:25+00:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, cast

from multiobjective_vns.config import FeedbackSelectionMode, MultiobjectiveVNSConfig
from multiobjective_vns.events import AlgorithmEvent
from multiobjective_vns.identifiers import (
    ChoiceId,
    ComponentId,
    InvocationId,
    SolutionId,
    identifier_text,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.failures import ComponentFailure, FailureKind
from multiobjective_vns.records.policies import ChoiceDescriptor, InvocationCounters
from multiobjective_vns.records.reporting import (
    AssessmentCount,
    ChoiceStatistics,
    DispositionCount,
    ExcursionFrequency,
    ExecutiveSummary,
    FailureCount,
    ModificationCountFrequency,
    ParetoSolutionReport,
    PromotedCompositionReport,
    ReportDiagnostic,
    ReportDiagnosticKind,
    RunProvenance,
)
from multiobjective_vns.records.results import LogFinalizationResult, RunLog
from multiobjective_vns.records.state import SearchState
from multiobjective_vns.records.status import (
    ApplicationAssessment,
    SearchDisposition,
    TerminationReason,
)
from multiobjective_vns.services import SolutionFormatter


def _json_value(value: object) -> object:
    """Convert public event values to their stable JSON representation."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, ChoiceId):
        return value.to_json_value()
    to_log_text = getattr(value, 'to_log_text', None)
    if callable(to_log_text):
        return identifier_text(value)  # type: ignore[arg-type]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        items = [_json_value(item) for item in value]
        items.sort(
            key=lambda item: json.dumps(
                item,
                ensure_ascii=True,
                allow_nan=False,
                separators=(',', ':'),
                sort_keys=True,
            )
        )
        return items
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    raise TypeError(f'{type(value).__name__} has no event JSON representation')


class DefaultRunLogger:
    """Write each invocation's event stream and executive summary files."""

    component_id: ComponentId = 'multiobjective-vns:run-logger'

    def __init__(self) -> None:
        self._invocation_id: InvocationId | None = None
        self._event_path: Path | None = None
        self._summary_path: Path | None = None
        self._events: tuple[AlgorithmEvent, ...] = ()

    def begin(self, invocation_id: InvocationId, log_directory: Path) -> None:
        """Reserve unique event and summary files for one invocation."""
        directory = log_directory.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        stem = invocation_id.to_log_text()
        event_path = directory / f'{stem}.events.jsonl'
        summary_path = directory / f'{stem}.summary.txt'
        with event_path.open('x', encoding='utf-8'):
            pass
        try:
            with summary_path.open('x', encoding='utf-8'):
                pass
        except Exception:
            event_path.unlink(missing_ok=True)
            raise
        self._invocation_id = invocation_id
        self._event_path = event_path
        self._summary_path = summary_path
        self._events = ()

    def append(self, events: tuple[AlgorithmEvent, ...]) -> None:
        """Atomically replace the structured event file with the appended log."""
        if self._event_path is None:
            raise RuntimeError('begin() must run before append()')
        if not events or any(not isinstance(event, AlgorithmEvent) for event in events):
            raise TypeError('events must be a nonempty tuple of AlgorithmEvent values')
        next_events = (*self._events, *events)
        lines = '\n'.join(
            json.dumps(
                self._event_record(event),
                ensure_ascii=True,
                allow_nan=False,
                separators=(',', ':'),
            )
            for event in next_events
        )
        self._atomic_write(self._event_path, f'{lines}\n')
        self._events = next_events

    def finalize(
        self,
        run_log: RunLog,
        initial_archive_ids: tuple[object, ...],
        final_state: SearchState,
        objective_declaration: ObjectiveDeclaration,
        termination_reason: TerminationReason,
        counters: InvocationCounters,
        feedback_selection_mode: FeedbackSelectionMode | None,
        promoted_compositions: tuple[PromotedCompositionReport, ...],
        solution_formatter: SolutionFormatter | None,
    ) -> LogFinalizationResult:
        """Aggregate the event stream, write the readable summary, and return it."""
        if self._summary_path is None or self._event_path is None:
            raise RuntimeError('begin() must run before finalize()')
        start = next(
            event
            for event in run_log.events
            if event.event_type == 'invocation_started'
        )
        stopped = next(
            event
            for event in reversed(run_log.events)
            if event.event_type == 'invocation_stopped'
        )
        provenance = cast(RunProvenance, start.data['provenance'])
        configuration = start.data.get('config')
        if not isinstance(configuration, MultiobjectiveVNSConfig):
            raise ValueError('invocation_started has no validated configuration')
        invocation_mode = start.data.get('mode')
        if invocation_mode not in ('initial', 'resume', 'maintenance'):
            raise ValueError('invocation_started has an unknown mode')
        invocation_kind: Literal['search', 'maintenance'] = (
            'maintenance' if invocation_mode == 'maintenance' else 'search'
        )
        timing = stopped.data
        choice_statistics = self._choice_statistics(run_log)
        assessments = Counter(
            cast(ApplicationAssessment, event.data['assessment'])
            for event in run_log.events
            if event.event_type == 'candidate_processed'
            and event.data.get('assessment') is not None
        )
        dispositions = Counter(
            cast(SearchDisposition, event.data['disposition'])
            for event in run_log.events
            if event.event_type == 'candidate_processed'
            and event.data.get('disposition') is not None
        )
        failures: Counter[tuple[ComponentId, FailureKind, str]] = Counter()
        for event in run_log.events:
            if event.event_type == 'component_failure':
                failure_value = event.data.get('failure')
                if isinstance(failure_value, ComponentFailure):
                    failure = failure_value
                    failures[(failure.source, failure.kind, failure.phase)] += 1
        reports: list[ParetoSolutionReport] = []
        diagnostics: list[ReportDiagnostic] = []
        for record in final_state.pareto_solutions():
            evaluation = record.evaluation_record.application_evaluation
            if evaluation is None or evaluation.objective_values is None:
                continue
            description: str | None = None
            if solution_formatter is not None:
                try:
                    description = solution_formatter.describe(record.solution)
                    if not isinstance(description, str) or not description:
                        raise ValueError('description must be a nonempty string')
                except Exception as error:
                    diagnostics.append(
                        ReportDiagnostic(
                            ReportDiagnosticKind.SOLUTION_DESCRIPTION_FAILURE,
                            solution_formatter.component_id,
                            record.solution_id,
                            None,
                            f'SolutionFormatter.describe failed: {error}',
                        )
                    )
                    description = None
            reports.append(
                ParetoSolutionReport(
                    record.solution_id,
                    evaluation.objective_values,
                    description,
                )
            )
        excursions = self._excursion_frequencies(run_log)
        summary = ExecutiveSummary(
            invocation_id=run_log.invocation_id,
            invocation_kind=invocation_kind,
            termination_reason=termination_reason,
            elapsed_seconds=cast(float, timing['elapsed_seconds']),
            wall_time_limit_seconds=cast(float, timing['wall_time_limit_seconds']),
            remaining_seconds=cast(float, timing['remaining_seconds']),
            initial_pareto_count=len(initial_archive_ids),
            final_pareto_solutions=tuple(reports),
            counters=counters,
            provenance=provenance,
            choice_statistics=choice_statistics,
            promoted_compositions=promoted_compositions,
            assessment_counts=tuple(
                AssessmentCount(category, count)
                for category, count in assessments.items()
            ),
            failure_counts=tuple(
                FailureCount(source, kind, phase, count)
                for (source, kind, phase), count in failures.items()
            ),
            disposition_counts=tuple(
                DispositionCount(category, count)
                for category, count in dispositions.items()
            ),
            excursion_frequencies=excursions,
            composition_selection_probability=(
                configuration.composition_selection_probability
            ),
            feedback_selection_mode=feedback_selection_mode,
        )
        readable = self._render_summary(summary, objective_declaration, diagnostics)
        self._atomic_write(self._summary_path, readable)
        self._atomic_write(
            self._event_path,
            '\n'.join(
                json.dumps(
                    self._event_record(event),
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(',', ':'),
                )
                for event in run_log.events
            )
            + '\n',
        )
        return LogFinalizationResult(
            summary,
            self._event_path,
            self._summary_path,
            tuple(diagnostics),
        )

    @staticmethod
    def _event_record(event: AlgorithmEvent) -> dict[str, object]:
        """Return an event envelope in the stable JSON Lines shape."""
        return cast(
            dict[str, object],
            _json_value(
                {
                    'schema_version': event.schema_version,
                    'invocation_id': event.invocation_id,
                    'sequence_number': event.sequence_number,
                    'commit_sequence': event.commit_sequence,
                    'event_type': event.event_type,
                    'phase': event.phase,
                    'source_id': event.source_id,
                    'choice_id': event.choice_id,
                    'solution_id': event.solution_id,
                    'data': event.data,
                }
            ),
        )

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Write a complete file through a same-directory temporary path."""
        temporary = path.with_name(f'.{path.name}.tmp')
        with temporary.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    @staticmethod
    def _choice_statistics(run_log: RunLog) -> tuple[ChoiceStatistics, ...]:
        descriptors: dict[tuple[ChoiceId, int], ChoiceDescriptor] = {}
        attempts: Counter[tuple[ChoiceId, int]] = Counter()
        admissions: Counter[tuple[ChoiceId, int]] = Counter()
        reward_totals: defaultdict[tuple[ChoiceId, int], float] = defaultdict(float)
        modifications: Counter[tuple[ChoiceId, int, int | None]] = Counter()
        for event in run_log.events:
            if event.event_type == 'choice_set_available':
                for descriptor in cast(
                    tuple[ChoiceDescriptor, ...], event.data['choices']
                ):
                    for size in range(1, descriptor.max_neighborhood_size + 1):
                        descriptors[(descriptor.choice_id, size)] = descriptor
            elif event.event_type == 'feedback_observed':
                choice_id = cast(ChoiceId, event.choice_id)
                size = cast(int, event.data['k'])
                key = (choice_id, size)
                attempts[key] += 1
                admissions[key] += cast(int, event.data['pareto_admission_count'])
                reward_totals[key] += cast(float, event.data['archive_reward'])
                modifications[(choice_id, size, cast(int | None, event.data['m']))] += 1
        result = []
        for key in sorted(
            descriptors, key=lambda pair: (str(pair[0].to_json_value()), pair[1])
        ):
            descriptor = descriptors[key]
            frequency_values = tuple(
                ModificationCountFrequency(m, count)
                for (choice_id, size, m), count in sorted(
                    modifications.items(),
                    key=lambda item: (
                        str(item[0][0].to_json_value()),
                        item[0][1],
                        -1 if item[0][2] is None else item[0][2],
                    ),
                )
                if (choice_id, size) == key
            )
            result.append(
                ChoiceStatistics(
                    key[0],
                    key[1],
                    descriptor.modification_count_definition,
                    attempts[key],
                    admissions[key],
                    reward_totals[key],
                    frequency_values,
                )
            )
        return tuple(result)

    @staticmethod
    def _excursion_frequencies(run_log: RunLog) -> tuple[ExcursionFrequency, ...]:
        active: dict[SolutionId, int] = {}
        completed: Counter[
            tuple[Literal['returned_to_anchor', 'feasible_exit'], int, bool]
        ] = Counter()
        for event in run_log.events:
            if event.event_type != 'excursion_transition':
                continue
            transition = cast(str, event.data['transition'])
            anchor_id = cast(SolutionId, event.data['anchor_solution_id'])
            step_count = cast(int, event.data['step_count'])
            if transition in ('started', 'advanced'):
                active[anchor_id] = step_count
            else:
                exit_kind: Literal['returned_to_anchor', 'feasible_exit'] = (
                    'returned_to_anchor'
                    if transition == 'returned_to_anchor'
                    else 'feasible_exit'
                )
                candidate_events = tuple(
                    candidate_event
                    for candidate_event in run_log.events
                    if candidate_event.event_type == 'candidate_processed'
                    and candidate_event.solution_id == event.solution_id
                    and candidate_event.commit_sequence == event.commit_sequence
                )
                archive_admitted = any(
                    candidate_event.data.get('disposition')
                    == SearchDisposition.ARCHIVED
                    for candidate_event in candidate_events
                )
                completed[(exit_kind, step_count, archive_admitted)] += 1
                active.pop(anchor_id, None)
        frequencies = [
            ExcursionFrequency('active', steps, None, None, count)
            for steps, count in Counter(active.values()).items()
        ]
        frequencies.extend(
            ExcursionFrequency('completed', steps, exit_kind, admitted, count)
            for (exit_kind, steps, admitted), count in completed.items()
        )
        return tuple(frequencies)

    @staticmethod
    def _render_summary(
        summary: ExecutiveSummary,
        objective_declaration: ObjectiveDeclaration,
        diagnostics: list[ReportDiagnostic],
    ) -> str:
        """Render a compact human-readable run report."""
        feedback_mode = summary.feedback_selection_mode or 'custom'
        lines = [
            'Multiobjective VNS executive summary',
            f'Invocation kind: {summary.invocation_kind}',
            f'Invocation ID: {summary.invocation_id.to_log_text()}',
            f'Termination: {summary.termination_reason.value}',
            f'Elapsed seconds: {summary.elapsed_seconds:.6g}',
            f'Pareto solutions: {summary.initial_pareto_count} -> {len(summary.final_pareto_solutions)}',
            f'Candidate attempts: {summary.counters.candidate_attempts}',
            f'Evaluation requests: {summary.counters.evaluation_requests}',
            'Composition selection probability: '
            f'{summary.composition_selection_probability:.6g}',
            f'Feedback selection mode: {feedback_mode}',
        ]
        if summary.wall_time_limit_seconds is not None:
            lines.extend(
                (
                    f'Wall-time limit seconds: {summary.wall_time_limit_seconds:.6g}',
                    f'Remaining seconds: {summary.remaining_seconds:.6g}',
                )
            )
        lines.extend(
            (
                '',
                'Component configurations:',
            )
        )
        for entry in summary.provenance.components:
            objective = (
                f'; objective={identifier_text(entry.objective_id)}'
                if entry.objective_id is not None
                else ''
            )
            configuration = (
                identifier_text(entry.configuration_id)
                if entry.configuration_id is not None
                else 'none'
            )
            lines.append(
                f'- {entry.component_role.value}{objective}: '
                f'{identifier_text(entry.component_id)}; '
                f'configuration={configuration}'
            )
        lines.extend(
            (
                '',
                'Assessment counts:',
            )
        )
        if summary.assessment_counts:
            lines.extend(
                f'- {item.assessment.value}: {item.count}'
                for item in summary.assessment_counts
            )
        else:
            lines.append('- none')
        lines.append('Disposition counts:')
        if summary.disposition_counts:
            lines.extend(
                f'- {item.disposition.value}: {item.count}'
                for item in summary.disposition_counts
            )
        else:
            lines.append('- none')
        lines.append('Component failures:')
        if summary.failure_counts:
            lines.extend(
                f'- {identifier_text(item.source)} [{item.kind.value}, '
                f'{item.phase}]: {item.count}'
                for item in summary.failure_counts
            )
        else:
            lines.append('- none')
        lines.append('Infeasible excursions:')
        if summary.excursion_frequencies:
            lines.extend(
                f'- {item.status}, steps={item.step_count}, '
                f'exit={item.exit_kind}, archive_admitted={item.archive_admitted}: '
                f'{item.count}'
                for item in summary.excursion_frequencies
            )
        else:
            lines.append('- none')
        lines.extend(('', 'Promoted compositions:'))
        if summary.promoted_compositions:
            statistics_by_choice: dict[ChoiceId, list[ChoiceStatistics]] = defaultdict(
                list
            )
            for statistic in summary.choice_statistics:
                statistics_by_choice[statistic.choice_id].append(statistic)
            for promoted in summary.promoted_compositions:
                source_choice = ChoiceId.for_composition(promoted.source_composition_id)
                operator_choice = ChoiceId.for_operator(promoted.operator_id)
                source_attempts = sum(
                    item.attempt_count
                    for item in statistics_by_choice.get(source_choice, ())
                )
                source_admissions = sum(
                    item.archive_admission_count
                    for item in statistics_by_choice.get(source_choice, ())
                )
                promoted_attempts = sum(
                    item.attempt_count
                    for item in statistics_by_choice.get(operator_choice, ())
                )
                promoted_admissions = sum(
                    item.archive_admission_count
                    for item in statistics_by_choice.get(operator_choice, ())
                )
                component_ids = ', '.join(
                    identifier_text(operator_id)
                    for operator_id in promoted.component_operator_ids
                )
                group_ids = (
                    ', '.join(
                        identifier_text(group_id)
                        for group_id in sorted(
                            promoted.inherited_group_ids, key=identifier_text
                        )
                    )
                    or 'none'
                )
                lines.append(
                    f'- {identifier_text(promoted.operator_id)} from '
                    f'({component_ids}); source attempts/admissions='
                    f'{source_attempts}/{source_admissions}; standalone '
                    f'attempts/admissions={promoted_attempts}/'
                    f'{promoted_admissions}; max k='
                    f'{promoted.max_neighborhood_size}; feasibility '
                    f'restoration={promoted.feasibility_restoration}; '
                    f'groups={group_ids}; promoted this invocation='
                    f'{promoted.promoted_in_current_invocation}'
                )
        else:
            lines.append('- none')
        lines.extend(
            (
                '',
                'Operator catalog:',
            )
        )
        for operator_entry in summary.provenance.operators:
            lines.append(
                f'- {identifier_text(operator_entry.operator_id)}: '
                f'max k={operator_entry.max_neighborhood_size}; '
                f'feasibility restoration={operator_entry.feasibility_restoration}; '
                f'm: {operator_entry.modification_count_definition}'
            )
        lines.append('Operator groups:')
        if summary.provenance.operator_groups:
            for group in summary.provenance.operator_groups:
                operator_ids = ', '.join(
                    identifier_text(operator_id)
                    for operator_id in sorted(group.operator_ids, key=identifier_text)
                )
                lines.append(f'- {identifier_text(group.group_id)}: {operator_ids}')
        else:
            lines.append('- none')
        lines.extend(
            (
                '',
                'Operator-choice statistics:',
            )
        )
        for statistic in summary.choice_statistics:
            rate = (
                'n/a'
                if statistic.admission_rate is None
                else f'{statistic.admission_rate:.1%}'
            )
            reward_rate = (
                'n/a'
                if statistic.archive_reward_rate is None
                else f'{statistic.archive_reward_rate:.3f}'
            )
            modification_counts = (
                ', '.join(
                    (
                        f'm={frequency.m}: {frequency.count}'
                        if frequency.m is not None
                        else f'm=none: {frequency.count}'
                    )
                    for frequency in statistic.modification_count_frequencies
                )
                or 'none'
            )
            lines.append(
                f'- {statistic.choice_id.to_json_value()!r}, k={statistic.k}: '
                f'{statistic.archive_admission_count}/'
                f'{statistic.attempt_count} admissions ({rate}); '
                f'archive reward={statistic.archive_reward_total:.3f} '
                f'(mean {reward_rate}); '
                f'm counts: {modification_counts}; '
                f'm definition: {statistic.modification_count_definition}'
            )
        if summary.final_pareto_solutions:
            lines.extend(('', 'Final Pareto solutions:'))
            for report in summary.final_pareto_solutions:
                rendered = []
                for objective_function, value in zip(
                    objective_declaration.objectives,
                    report.objective_values,
                    strict=True,
                ):
                    try:
                        name = str(objective_function)
                        if not name:
                            raise ValueError('objective name must be nonempty')
                    except Exception as error:
                        diagnostics.append(
                            ReportDiagnostic(
                                ReportDiagnosticKind.OBJECTIVE_NAME_FAILURE,
                                objective_function.component_id,
                                None,
                                objective_function.objective_id,
                                f'ObjectiveFunction.__str__ failed: {error}',
                            )
                        )
                        name = identifier_text(objective_function.objective_id)
                    try:
                        formatted = objective_function.format(value)
                    except Exception as error:
                        diagnostics.append(
                            ReportDiagnostic(
                                ReportDiagnosticKind.OBJECTIVE_VALUE_FORMAT_FAILURE,
                                objective_function.component_id,
                                report.solution_id,
                                objective_function.objective_id,
                                f'ObjectiveFunction.format failed: {error}',
                            )
                        )
                        formatted = '[unformatted]'
                    rendered.append(f'{name}={formatted}')
                solution_id = report.solution_id.to_log_text()
                label = (
                    f'{report.description} [{solution_id}]'
                    if report.description is not None
                    else solution_id
                )
                lines.append(f'- {label}: {", ".join(rendered)}')
        if diagnostics:
            lines.extend(('', 'Reporting diagnostics:'))
            lines.extend(f'- {diagnostic.reason}' for diagnostic in diagnostics)
        return '\n'.join(lines) + '\n'


__all__ = ['DefaultRunLogger']
