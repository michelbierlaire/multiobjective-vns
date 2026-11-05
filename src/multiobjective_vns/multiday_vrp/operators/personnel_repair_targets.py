"""Feature-based target selection for personnel repair Operators.

Created: 2026-11-04T09:56:38+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from collections import defaultdict

from multiobjective_vns.identifiers import ObjectiveId, identifier_text
from multiobjective_vns.multiday_vrp.evaluation.records import ResourceFinding
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.personnel_repair_support import (
    DutyTarget,
    PersonnelRepairMode,
    RepairTarget,
    _availability_exposure,
    _continuity_targets,
    _driver_qualified,
    _finding_matches_duty,
    _has_restricted_calendar,
    _maximum_finding,
    _person_from_resource_id,
    analysis_of,
    person_duty_targets,
    qualified_drivers,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
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
) -> tuple[RepairTarget, ...]:
    """Find the mode-specific route assignments eligible for repair."""
    if mode == 'skill_guided':
        return tuple(
            RepairTarget(DutyTarget(index, solution.routes[index].driver_id, 'driver'))
            for index in _skill_target_routes(instance, solution)
        )
    if mode == 'staff_continuity':
        return tuple(
            RepairTarget(duty) for duty in _continuity_targets(instance, solution)
        )
    if mode == 'availability':
        return _availability_targets(instance, solution, evaluation, objective_id)
    return _work_rule_targets(instance, solution, evaluation, objective_id)


def _availability_targets(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    evaluation: ApplicationEvaluation | None,
    objective_id: ObjectiveId | None,
) -> tuple[RepairTarget, ...]:
    duties = tuple(
        duty
        for duty in person_duty_targets(solution)
        if _has_restricted_calendar(instance, duty.person_id)
    )
    analysis = analysis_of(evaluation)
    if analysis is not None and analysis.is_complete and objective_id is not None:
        penalties: dict[str, float] = defaultdict(float)
        scenario_probabilities = {
            item.scenario_id: item.probability
            for item in instance.scenario_bank.scenarios
        }
        objective_text = identifier_text(objective_id)
        for finding in analysis.resource_findings:
            if finding.kind != 'personnel_availability':
                continue
            contribution = dict(finding.penalty_by_objective).get(objective_text, 0.0)
            penalties[finding.resource_id] += (
                scenario_probabilities.get(finding.scenario_id or '', 1.0)
                * contribution
            )
        positive_people = tuple(
            person_id for person_id, amount in penalties.items() if amount > 0.0
        )
        if positive_people:
            maximum = max(penalties[person_id] for person_id in positive_people)
            selected_people = {
                person_id
                for person_id in positive_people
                if penalties[person_id] == maximum
            }
            duties = tuple(duty for duty in duties if duty.person_id in selected_people)
            if duties:
                exposure = {
                    duty: _availability_exposure(instance, solution, analysis, duty)
                    for duty in duties
                }
                largest = max(exposure.values())
                duties = tuple(duty for duty in duties if exposure[duty] == largest)
    return tuple(RepairTarget(duty) for duty in duties)


def _work_rule_targets(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    evaluation: ApplicationEvaluation | None,
    objective_id: ObjectiveId | None,
) -> tuple[RepairTarget, ...]:
    analysis = analysis_of(evaluation)
    selected: ResourceFinding | None = None
    if analysis is not None:
        candidates = tuple(
            finding
            for finding in analysis.resource_findings
            if finding.kind in _WORK_RULE_KINDS and finding.measure > 0.0
        )
        if instance.settings.rule_treatment == 'penalty':
            if analysis.is_complete and objective_id is not None:
                objective_text = identifier_text(objective_id)
                selected = _maximum_finding(
                    candidates,
                    lambda item: dict(item.penalty_by_objective).get(
                        objective_text, 0.0
                    ),
                )
        elif (
            analysis.hard_failure is not None
            and analysis.hard_failure.kind == 'compliance_rule'
        ):
            selected = _maximum_finding(candidates, lambda item: item.measure)

    if selected is not None:
        person_id = _person_from_resource_id(instance, selected.resource_id)
        if person_id is not None:
            duties = tuple(
                duty
                for duty in person_duty_targets(solution)
                if duty.person_id == person_id
                and _finding_matches_duty(selected, duty, solution)
            )
            if duties:
                return tuple(RepairTarget(duty, selected) for duty in duties)
    return tuple(RepairTarget(duty) for duty in person_duty_targets(solution))


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
