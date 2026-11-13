"""Destroy-and-repair moves for multi-route driver schedules.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from functools import cmp_to_key

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
    randomized_order,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import objective_position
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
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
    :param max_candidate_alternatives: Maximum complete assignment vectors
        sampled per call.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_position',
        '_maximum_size',
        '_candidate_limit',
        '_qualified_driver_ids',
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
        self._qualified_driver_ids = _qualified_driver_ids_by_vehicle(instance)

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
            self._qualified_driver_ids.get(base.routes[route_index].vehicle_id, ())
            for route_index in route_indices
        )
        if any(not domain for domain in driver_domains):
            return operator_result(None, modifications=0, requested_size=size)

        seen: set[tuple[str, ...]] = set()
        candidates: list[tuple[VrpSolution, tuple[object, ...], int]] = []
        attempts = 0
        vector_limit = min(
            self._candidate_limit,
            _assignment_vector_count(driver_domains, self._candidate_limit),
        )
        attempt_limit = vector_limit * 32
        while len(seen) < vector_limit and attempts < attempt_limit:
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

        ordered = self._ordered_candidates(candidates, context)
        proposals = tuple(
            CandidateProposal(Candidate(solution), modifications)
            for solution, _objective_values, modifications in ordered[
                : context.max_candidates
            ]
        )
        return OperatorResult(candidates=proposals)

    def _ordered_candidates(
        self,
        candidates: list[tuple[VrpSolution, tuple[object, ...], int]],
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...], int], ...]:
        if self._objective_position is None:
            return randomized_order(candidates, context.random_source)
        objective = self._objectives.objectives[self._objective_position]
        position = self._objective_position

        def compare(
            left: tuple[VrpSolution, tuple[object, ...], int],
            right: tuple[VrpSolution, tuple[object, ...], int],
        ) -> int:
            comparison = objective.compare(left[1][position], right[1][position])
            if comparison is ObjectiveComparison.LESS:
                return -1
            if comparison is ObjectiveComparison.GREATER:
                return 1
            return 0

        ranked = sorted(candidates, key=cmp_to_key(compare))
        ordered: list[tuple[VrpSolution, tuple[object, ...], int]] = []
        group_start = 0
        while group_start < len(ranked):
            group_end = group_start + 1
            while (
                group_end < len(ranked)
                and compare(ranked[group_start], ranked[group_end]) == 0
            ):
                group_end += 1
            ordered.extend(
                randomized_order(ranked[group_start:group_end], context.random_source)
            )
            group_start = group_end
        return tuple(ordered)


def _assignment_vector_count(
    driver_domains: tuple[tuple[str, ...], ...], limit: int
) -> int:
    """Return the Cartesian assignment count, capped before it grows large."""
    count = 1
    for domain in driver_domains:
        count = min(limit, count * len(domain))
    return count


def _qualified_driver_ids_by_vehicle(
    instance: LoadedVrpInstance,
) -> dict[str, tuple[str, ...]]:
    skills_by_person: dict[str, set[str]] = {}
    for person_skill in instance.personnel_data.personnel_driving_skills:
        skills_by_person.setdefault(person_skill.person_id, set()).add(
            person_skill.skill_id
        )

    required_skills_by_vehicle: dict[str, set[str]] = {}
    for vehicle_skill in instance.fleet_data.vehicle_driving_skills:
        required_skills_by_vehicle.setdefault(vehicle_skill.vehicle_id, set()).add(
            vehicle_skill.skill_id
        )

    qualified: dict[str, tuple[str, ...]] = {}
    for vehicle in instance.fleet_data.vehicles:
        required = required_skills_by_vehicle.get(vehicle.vehicle_id, set())
        qualified[vehicle.vehicle_id] = tuple(
            sorted(
                person.person_id
                for person in instance.personnel_data.personnel
                if person.authorized_driver
                and required <= skills_by_person.get(person.person_id, set())
            )
        )
    return qualified
