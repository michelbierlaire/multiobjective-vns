"""Destroy-and-repair moves for multi-route driver schedules.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace

from multiobjective_vns.identifiers import ObjectiveId
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
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = ['DriverScheduleDestroyRepairOperator']


class DriverScheduleDestroyRepairOperator:
    """Jointly reassign a bounded set of routes to qualified drivers.

    :param instance: Validated VRP input and feature profile.
    :param objective_declaration: Ordered objective comparison contract.
    :param assess_plan: Complete-plan application assessor.
    :param max_neighborhood_size: Maximum number of driver assignments changed.
    :param objective_id: Optional objective used to rank complete repairs.
    :param max_candidate_alternatives: Maximum complete vectors assessed per call.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_position',
        '_maximum_size',
        '_candidate_limit',
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
        self._candidate_limit = require_integer(
            max_candidate_alternatives,
            name='max_candidate_alternatives',
            minimum=1,
        )
        if self._candidate_limit > 32:
            raise ValueError('max_candidate_alternatives must be at most 32')

    def call(self, context: CandidateContext) -> OperatorResult:
        """Jointly replace up to ``k`` route-driver assignments."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        route_indices = randomized_sample(
            tuple(range(len(base.routes))),
            min(size, len(base.routes)),
            context.random_source,
        )
        if not route_indices:
            return operator_result(None, modifications=0, requested_size=size)
        driver_domains = tuple(
            _qualified_drivers(self._instance, base, route_index)
            for route_index in route_indices
        )
        if any(not domain for domain in driver_domains):
            return operator_result(None, modifications=0, requested_size=size)

        seen: set[tuple[str, ...]] = set()
        candidates: list[tuple[VrpSolution, tuple[object, ...], int]] = []
        attempts = 0
        attempt_limit = self._candidate_limit * 32
        while len(candidates) < self._candidate_limit and attempts < attempt_limit:
            attempts += 1
            assignment = tuple(
                choose_uniformly(domain, context.random_source)
                for domain in driver_domains
            )
            if assignment in seen:
                continue
            seen.add(assignment)
            if all(
                driver_id == base.routes[route_index].driver_id
                for route_index, driver_id in zip(
                    route_indices, assignment, strict=True
                )
            ):
                continue
            routes = list(base.routes)
            for route_index, driver_id in zip(route_indices, assignment, strict=True):
                routes[route_index] = replace(routes[route_index], driver_id=driver_id)
            candidate = replace(base, routes=tuple(routes))
            evaluation = self._assess_plan(self._instance, self._objectives, candidate)
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
                raise ValueError('objective vector length must match the declaration')
            if context.ensure_feasible and evaluation.feasible is not True:
                continue
            changed_count = sum(
                base.routes[index].driver_id != candidate.routes[index].driver_id
                for index in route_indices
            )
            candidates.append((candidate, evaluation.objective_values, changed_count))
        if not candidates:
            return operator_result(None, modifications=0, requested_size=size)

        selected = self._choose(candidates, context)
        return operator_result(
            selected[0],
            modifications=selected[2],
            requested_size=size,
        )

    def _choose(
        self,
        candidates: list[tuple[VrpSolution, tuple[object, ...], int]],
        context: CandidateContext,
    ) -> tuple[VrpSolution, tuple[object, ...], int]:
        if self._objective_position is None:
            return choose_uniformly(candidates, context.random_source)
        objective = self._objectives.objectives[self._objective_position]
        best = candidates[0]
        tied = [best]
        for candidate in candidates[1:]:
            comparison = objective.compare(
                candidate[1][self._objective_position],
                best[1][self._objective_position],
            )
            if comparison is ObjectiveComparison.LESS:
                best = candidate
                tied = [candidate]
            elif comparison is ObjectiveComparison.EQUAL:
                tied.append(candidate)
        return choose_uniformly(tied, context.random_source)


def _qualified_drivers(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    route_index: int,
) -> tuple[str, ...]:
    route = solution.routes[route_index]
    vehicle = next(
        (
            item
            for item in instance.fleet_data.vehicles
            if item.vehicle_id == route.vehicle_id
        ),
        None,
    )
    if vehicle is None:
        return ()
    required = {
        item.skill_id
        for item in instance.fleet_data.vehicle_driving_skills
        if item.vehicle_id == vehicle.vehicle_id
    }
    skills_by_person: dict[str, set[str]] = {}
    for item in instance.personnel_data.personnel_driving_skills:
        skills_by_person.setdefault(item.person_id, set()).add(item.skill_id)
    return tuple(
        sorted(
            person.person_id
            for person in instance.personnel_data.personnel
            if person.authorized_driver
            and required <= skills_by_person.get(person.person_id, set())
        )
    )
