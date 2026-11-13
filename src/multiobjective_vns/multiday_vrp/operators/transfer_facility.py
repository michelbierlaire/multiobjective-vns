"""Storage-aware transfer-facility reassignment Operator.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from functools import cmp_to_key

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    randomized_order,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import objective_position
from multiobjective_vns.multiday_vrp.operators.schedule_features import (
    _rebuild_vehicle_routes,
)
from multiobjective_vns.multiday_vrp.solution import TransferOperationStop, VrpSolution
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = ['TransferFacilityAssignmentOperator']


class TransferFacilityAssignmentOperator:
    """Reassign transfer facilities, rebuilding both operation routes."""

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_position',
        '_maximum_size',
        '_candidate_limit',
        '_path_limit',
        '_expansion_limit',
        '_facilities_by_package',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._objective_position = (
            None
            if objective_id is None
            else objective_position(objective_declaration, objective_id)
        )
        self._maximum_size = require_integer(
            max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )
        self._candidate_limit = _limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )
        facilities_by_package: dict[str, list[str]] = {}
        for facility_record in instance.package_data.package_transfer_facilities:
            facilities_by_package.setdefault(facility_record.package_id, []).append(
                facility_record.node_id
            )
        self._facilities_by_package = {
            package_id: tuple(sorted(set(facilities)))
            for package_id, facilities in facilities_by_package.items()
        }

    def call(self, context: CandidateContext) -> OperatorResult:
        """Reassign up to ``k`` distinct transfer steps sequentially."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        changed: set[tuple[str, int]] = set()
        modifications = 0
        last_candidates: tuple[tuple[VrpSolution, tuple[object, ...], float], ...] = ()
        for _ in range(size):
            evaluation = self._assess_plan(self._instance, self._objectives, working)
            if not isinstance(evaluation, ApplicationEvaluation):
                raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
            targets = _target_steps(working, evaluation, changed)
            if not targets:
                targets = tuple(
                    (step.package_id, step.step_number)
                    for step in working.transfer_steps
                    if (step.package_id, step.step_number) not in changed
                )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            changed.add(target)
            candidates = self._candidates(working, target, context)
            if not candidates:
                continue
            last_candidates = _order_candidates(
                candidates,
                self._objectives,
                self._objective_position,
                context,
            )
            working = last_candidates[0][0]
            modifications += 1
        if not modifications:
            return OperatorResult(candidates=())
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), modifications)
                for solution, _values, _excess in last_candidates[
                    : context.max_candidates
                ]
            )
        )

    def _candidates(
        self,
        solution: VrpSolution,
        target: tuple[str, int],
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...], float], ...]:
        package_id, step_number = target
        step_index = next(
            index
            for index, step in enumerate(solution.transfer_steps)
            if step.package_id == package_id and step.step_number == step_number
        )
        step = solution.transfer_steps[step_index]
        facilities = tuple(
            facility_id
            for facility_id in self._facilities_by_package.get(package_id, ())
            if facility_id != step.facility_id
        )
        choices = randomized_sample(
            facilities, self._candidate_limit, context.random_source
        )
        affected = {
            route_index
            for route_index, route in enumerate(solution.routes)
            if any(
                type(stop) is TransferOperationStop
                and stop.transfer_step_ref.package_id == package_id
                and stop.transfer_step_ref.step_number == step_number
                for stop in route.stops
            )
        }
        candidates: list[tuple[VrpSolution, tuple[object, ...], float]] = []
        seen: set[VrpSolution] = set()
        for facility_id in choices:
            remaining = self._candidate_limit - len(candidates)
            if remaining <= 0:
                break
            steps = list(solution.transfer_steps)
            steps[step_index] = replace(step, facility_id=facility_id)
            provisional = replace(solution, transfer_steps=tuple(steps))
            rebuilt = _rebuild_vehicle_routes(
                self._instance,
                solution,
                provisional,
                affected,
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=remaining,
            )
            for candidate in rebuilt:
                if candidate in seen:
                    continue
                evaluation = self._assess_plan(
                    self._instance, self._objectives, candidate
                )
                if not isinstance(evaluation, ApplicationEvaluation):
                    raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
                if (
                    not evaluation.representation_valid
                    or evaluation.objective_values is None
                ):
                    continue
                if type(evaluation.objective_values) is not tuple:
                    raise TypeError('objective_values must be a tuple')
                if len(evaluation.objective_values) != len(self._objectives.objectives):
                    raise ValueError(
                        'objective vector length must match the declaration'
                    )
                if context.ensure_feasible and evaluation.feasible is not True:
                    continue
                candidates.append(
                    (
                        candidate,
                        evaluation.objective_values,
                        _maximum_storage_excess(evaluation),
                    )
                )
                seen.add(candidate)
                if len(candidates) >= self._candidate_limit:
                    return tuple(candidates)
        return tuple(candidates)


def _target_steps(
    solution: VrpSolution,
    evaluation: ApplicationEvaluation,
    already_changed: set[tuple[str, int]],
) -> tuple[tuple[str, int], ...]:
    if not isinstance(evaluation.analysis, VrpOperatorAnalysis):
        return ()
    storage_findings = tuple(
        item
        for item in evaluation.analysis.resource_findings
        if item.kind == 'transfer_storage' and item.measure > 0.0
    )
    if not storage_findings:
        return ()
    largest_excess = max(item.measure for item in storage_findings)
    contributing_steps = {
        (item.transfer_step_ref.package_id, item.transfer_step_ref.step_number)
        for item in storage_findings
        if item.measure == largest_excess and item.transfer_step_ref is not None
    }
    return tuple(
        (step.package_id, step.step_number)
        for step in solution.transfer_steps
        if (step.package_id, step.step_number) in contributing_steps
        and (step.package_id, step.step_number) not in already_changed
    )


def _maximum_storage_excess(evaluation: ApplicationEvaluation) -> float:
    if not isinstance(evaluation.analysis, VrpOperatorAnalysis):
        return 0.0
    return max(
        (
            item.measure
            for item in evaluation.analysis.resource_findings
            if item.kind == 'transfer_storage'
        ),
        default=0.0,
    )


def _order_candidates(
    candidates: tuple[tuple[VrpSolution, tuple[object, ...], float], ...],
    objectives: ObjectiveDeclaration,
    objective_index: int | None,
    context: CandidateContext,
) -> tuple[tuple[VrpSolution, tuple[object, ...], float], ...]:
    by_excess = sorted(candidates, key=lambda candidate: candidate[2])
    ordered: list[tuple[VrpSolution, tuple[object, ...], float]] = []
    group_start = 0
    while group_start < len(by_excess):
        group_end = group_start + 1
        while (
            group_end < len(by_excess)
            and by_excess[group_end][2] == by_excess[group_start][2]
        ):
            group_end += 1
        excess_tie = tuple(by_excess[group_start:group_end])
        if objective_index is None:
            ordered.extend(randomized_order(excess_tie, context.random_source))
        else:
            objective = objectives.objectives[objective_index]

            def compare(
                left: tuple[VrpSolution, tuple[object, ...], float],
                right: tuple[VrpSolution, tuple[object, ...], float],
            ) -> int:
                comparison = objective.compare(
                    left[1][objective_index], right[1][objective_index]
                )
                if comparison is ObjectiveComparison.LESS:
                    return -1
                if comparison is ObjectiveComparison.GREATER:
                    return 1
                return 0

            objective_ranked = sorted(excess_tie, key=cmp_to_key(compare))
            objective_start = 0
            while objective_start < len(objective_ranked):
                objective_end = objective_start + 1
                while (
                    objective_end < len(objective_ranked)
                    and compare(
                        objective_ranked[objective_start],
                        objective_ranked[objective_end],
                    )
                    == 0
                ):
                    objective_end += 1
                ordered.extend(
                    randomized_order(
                        objective_ranked[objective_start:objective_end],
                        context.random_source,
                    )
                )
                objective_start = objective_end
        group_start = group_end
    return tuple(ordered)


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
