"""Feature-based target selection for personnel repair Operators.

Created: 2026-11-04T09:56:38+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections.abc import Callable

from multiobjective_vns.identifiers import ObjectiveId, identifier_text
from multiobjective_vns.multiday_vrp.evaluation.records import ResourceFinding
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import choose_uniformly
from multiobjective_vns.multiday_vrp.operators.personnel_repair_support import (
    DutyTarget,
    PersonnelRepairMode,
    RepairTarget,
    _availability_exposures,
    _continuity_targets,
    _ContinuityIndex,
    _driver_qualified,
    _finding_matches_duty,
    _person_from_resource_id,
    analysis_of,
    person_duty_targets,
    qualified_drivers,
    restricted_calendar_people,
)
from multiobjective_vns.multiday_vrp.solution import ActivityStop, VrpSolution
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.evaluation import ApplicationEvaluation

_WORK_RULE_KINDS: frozenset[str] = frozenset(
    {'driver_work', 'driving_time', 'break', 'rest', 'hos'}
)


def select_targets(
    instance: LoadedVrpInstance,
    mode: PersonnelRepairMode,
    solution: VrpSolution,
    evaluation: ApplicationEvaluation | None,
    objective_id: ObjectiveId | None,
    *,
    restricted_personnel_ids: frozenset[str] | None = None,
    continuity_index: _ContinuityIndex | None = None,
    random_source: RandomSource | None = None,
) -> tuple[RepairTarget, ...]:
    """Find the mode-specific route assignments eligible for repair."""
    if mode == 'skill_guided':
        return tuple(
            RepairTarget(DutyTarget(index, solution.routes[index].driver_id, 'driver'))
            for index in _skill_target_routes(instance, solution)
        )
    if mode == 'staff_continuity':
        return tuple(
            RepairTarget(duty)
            for duty in _continuity_targets(instance, solution, continuity_index)
        )
    if mode == 'availability':
        return _availability_targets(
            instance,
            solution,
            evaluation,
            objective_id,
            restricted_personnel_ids,
        )
    return _work_rule_targets(
        instance, solution, evaluation, objective_id, random_source
    )


def _availability_targets(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    evaluation: ApplicationEvaluation | None,
    objective_id: ObjectiveId | None,
    restricted_personnel_ids: frozenset[str] | None,
) -> tuple[RepairTarget, ...]:
    restricted_people = (
        restricted_calendar_people(instance)
        if restricted_personnel_ids is None
        else restricted_personnel_ids
    )
    duties = tuple(
        duty
        for duty in person_duty_targets(solution)
        if duty.person_id in restricted_people
    )
    analysis = analysis_of(evaluation)
    if analysis is not None and objective_id is not None:
        objective_text = identifier_text(objective_id)
        contribution_by_person: dict[str, float] = {}
        for finding in analysis.resource_findings:
            if finding.kind != 'personnel_availability':
                continue
            contribution = dict(finding.penalty_by_objective).get(objective_text, 0.0)
            if contribution > 0.0:
                contribution_by_person[finding.resource_id] = max(
                    contribution_by_person.get(finding.resource_id, 0.0),
                    contribution,
                )
        if contribution_by_person:
            maximum = max(contribution_by_person.values())
            selected_people = {
                person_id
                for person_id, contribution in contribution_by_person.items()
                if contribution == maximum
            }
            duties = tuple(duty for duty in duties if duty.person_id in selected_people)
            if not duties:
                return ()
            exposure = _availability_exposures(instance, solution, analysis, duties)
            largest = max(exposure.values())
            duties = tuple(duty for duty in duties if exposure[duty] == largest)
        elif analysis.is_complete:
            return ()
    return tuple(RepairTarget(duty) for duty in duties)


def _work_rule_targets(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    evaluation: ApplicationEvaluation | None,
    objective_id: ObjectiveId | None,
    random_source: RandomSource | None,
) -> tuple[RepairTarget, ...]:
    analysis = analysis_of(evaluation)
    selected: ResourceFinding | None = None
    if analysis is not None:
        if instance.settings.rule_treatment == 'penalty':
            if analysis.is_complete and objective_id is not None:
                objective_text = identifier_text(objective_id)
                scored: list[tuple[ResourceFinding, float]] = []
                for finding in analysis.resource_findings:
                    if finding.kind not in _WORK_RULE_KINDS or finding.measure <= 0.0:
                        continue
                    contribution = dict(finding.penalty_by_objective).get(
                        objective_text, 0.0
                    )
                    if contribution > 0.0:
                        scored.append((finding, contribution))
                selected = _uniform_maximum_scored(tuple(scored), random_source)
        elif (
            analysis.hard_failure is not None
            and analysis.hard_failure.kind == 'compliance_rule'
        ):
            candidates = tuple(
                finding
                for finding in analysis.resource_findings
                if finding.kind in _WORK_RULE_KINDS
                and finding.measure > 0.0
                and _compliance_rule_selected(instance, finding)
            )
            selected = _uniform_compliance_finding(candidates, random_source)

    if selected is not None:
        person_id = _person_from_resource_id(instance, selected.resource_id)
        if person_id is not None:
            duties = _finding_duties(
                selected,
                person_id,
                solution,
            )
            if duties:
                return tuple(RepairTarget(duty, selected) for duty in duties)
    return tuple(RepairTarget(duty) for duty in person_duty_targets(solution))


def _uniform_maximum_finding(
    findings: tuple[ResourceFinding, ...],
    value_of: Callable[[ResourceFinding], float],
    random_source: RandomSource | None,
) -> ResourceFinding | None:
    if not findings:
        return None
    return _uniform_maximum_scored(
        tuple((item, value_of(item)) for item in findings), random_source
    )


def _uniform_maximum_scored(
    findings: tuple[tuple[ResourceFinding, float], ...],
    random_source: RandomSource | None,
) -> ResourceFinding | None:
    if not findings:
        return None
    maximum = max(score for _finding, score in findings)
    tied = tuple(finding for finding, score in findings if score == maximum)
    return tied[0] if random_source is None else choose_uniformly(tied, random_source)


def _uniform_compliance_finding(
    findings: tuple[ResourceFinding, ...], random_source: RandomSource | None
) -> ResourceFinding | None:
    groups: dict[tuple[object, ...], list[ResourceFinding]] = {}
    for finding in findings:
        key = (
            finding.kind,
            finding.resource_id,
            finding.vehicle_id,
            finding.day_index,
            finding.activity_ref,
        )
        groups.setdefault(key, []).append(finding)
    if not groups:
        return None
    keys = tuple(groups)
    selected_key = (
        keys[0] if random_source is None else choose_uniformly(keys, random_source)
    )
    return _uniform_maximum_finding(
        tuple(groups[selected_key]),
        lambda item: item.measure,
        random_source,
    )


def _finding_duties(
    finding: ResourceFinding,
    person_id: str,
    solution: VrpSolution,
) -> tuple[DutyTarget, ...]:
    result: list[DutyTarget] = []
    for route_index, route in enumerate(solution.routes):
        driver = DutyTarget(route_index, person_id, 'driver', finding.activity_ref)
        if route.driver_id == person_id and _finding_matches_duty(
            finding, driver, solution
        ):
            result.append(driver)
            continue
        if finding.kind == 'driving_time' or not _finding_matches_duty(
            finding, DutyTarget(route_index, person_id, 'team'), solution
        ):
            continue
        matching_stops = tuple(
            stop
            for stop in route.stops
            if type(stop) is ActivityStop
            and person_id in stop.team_member_ids
            and (
                finding.activity_ref is None
                or stop.activity_ref == finding.activity_ref
            )
        )
        if matching_stops:
            result.append(
                DutyTarget(
                    route_index,
                    person_id,
                    'team',
                    finding.activity_ref,
                )
            )
    return tuple(result)


def _compliance_rule_selected(
    instance: LoadedVrpInstance, finding: ResourceFinding
) -> bool:
    active = instance.settings.active_simple_rules
    if finding.kind == 'driver_work':
        return 'daily_work' in active
    if finding.kind == 'driving_time':
        return bool({'daily_driving', 'continuous_driving'} & active)
    if finding.kind == 'rest':
        return 'minimum_rest' in active
    if finding.kind == 'break':
        return 'continuous_driving' in active
    if finding.kind == 'hos':
        return instance.settings.hos_regime_id != 'none'
    return False


def _skill_target_routes(
    instance: LoadedVrpInstance, solution: VrpSolution
) -> tuple[int, ...]:
    violating = tuple(
        index
        for index in range(len(solution.routes))
        if not _driver_qualified(instance, solution, index)
    )
    if violating:
        return violating
    counts = tuple(
        len(qualified_drivers(instance, solution, index))
        for index in range(len(solution.routes))
    )
    if not counts:
        return ()
    minimum = min(counts)
    return tuple(index for index, count in enumerate(counts) if count == minimum)


__all__ = ['select_targets']
