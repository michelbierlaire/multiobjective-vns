"""Storage-aware transfer-facility reassignment Operator.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
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
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
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

    def call(self, context: CandidateContext) -> OperatorResult:
        """Reassign up to ``k`` distinct transfer steps sequentially."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        changed: set[tuple[str, int]] = set()
        modifications = 0
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
            working = self._choose(candidates, context)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
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
            sorted(
                record.node_id
                for record in self._instance.package_data.package_transfer_facilities
                if record.package_id == package_id
                and record.node_id != step.facility_id
            )
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
        for facility_id in choices:
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
                max_candidate_alternatives=self._candidate_limit,
            )
            for candidate in rebuilt:
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
        return tuple(candidates)

    def _choose(
        self,
        candidates: tuple[tuple[VrpSolution, tuple[object, ...], float], ...],
        context: CandidateContext,
    ) -> VrpSolution:
        best_excess = min(candidate[2] for candidate in candidates)
        tied = [candidate for candidate in candidates if candidate[2] == best_excess]
        if self._objective_position is not None:
            objective = self._objectives.objectives[self._objective_position]
            best = tied[0]
            objective_ties = [best]
            for candidate in tied[1:]:
                comparison = objective.compare(
                    candidate[1][self._objective_position],
                    best[1][self._objective_position],
                )
                if comparison is ObjectiveComparison.LESS:
                    best = candidate
                    objective_ties = [candidate]
                elif comparison is ObjectiveComparison.EQUAL:
                    objective_ties.append(candidate)
            tied = objective_ties
        return choose_uniformly(tied, context.random_source)[0]


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
    facility_ids = {item.resource_id for item in storage_findings}
    return tuple(
        (step.package_id, step.step_number)
        for step in solution.transfer_steps
        if step.facility_id in facility_ids
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


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
