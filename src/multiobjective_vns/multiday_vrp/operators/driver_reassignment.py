"""Generic driver-to-route reassignment Operator.

Created: 2026-11-03T21:38:00+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace

from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    candidate_is_eligible,
    choose_uniformly,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.values import require_integer

__all__ = ['DriverReassignmentOperator']


class DriverReassignmentOperator:
    """Assign a different qualified person to active vehicle-day routes."""

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
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
        max_candidate_alternatives: int = 32,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._maximum_size = require_integer(
            max_neighborhood_size,
            name='max_neighborhood_size',
            minimum=1,
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
        """Reassign up to ``k`` routes to different qualified drivers.

        Driver skill qualification is a structural candidate filter. The
        shared plan assessor evaluates availability, duty overlap, and other
        driver or vehicle constraints.

        :param context: Base plan, requested size, feasibility, and shared RNG.
        :returns: Up to ``context.max_candidates`` distinct feasible plans.
        :raises TypeError: If the base or assessment callback is malformed.
        :raises ValueError: If the requested size exceeds the registration limit.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        eligible_assignments = tuple(
            (route_index, self._qualified_alternatives(base, route_index))
            for route_index in range(len(base.routes))
        )
        eligible_assignments = tuple(
            assignment for assignment in eligible_assignments if assignment[1]
        )
        if not eligible_assignments:
            return OperatorResult(candidates=())

        proposals: list[CandidateProposal] = []
        seen_solutions = {base}
        for _ in range(self._candidate_limit):
            working = base
            available_assignments = list(eligible_assignments)
            modifications = 0
            for _ in range(size):
                if not available_assignments:
                    break
                route_index, driver_ids = choose_uniformly(
                    available_assignments, context.random_source
                )
                driver_id = choose_uniformly(driver_ids, context.random_source)
                route = working.routes[route_index]
                routes = list(working.routes)
                routes[route_index] = replace(route, driver_id=driver_id)
                working = replace(working, routes=tuple(routes))
                available_assignments.remove((route_index, driver_ids))
                modifications += 1
            if not modifications or working in seen_solutions:
                continue
            seen_solutions.add(working)
            if not candidate_is_eligible(
                self._instance,
                self._objectives,
                working,
                ensure_feasible=context.ensure_feasible,
                assess_plan=self._assess_plan,
            ):
                continue
            proposals.append(CandidateProposal(Candidate(working), modifications))
            if len(proposals) >= context.max_candidates:
                break
        return OperatorResult(candidates=tuple(proposals[: context.max_candidates]))

    def _qualified_alternatives(
        self, solution: VrpSolution, route_index: int
    ) -> tuple[str, ...]:
        route = solution.routes[route_index]
        return tuple(
            person_id
            for person_id in self._qualified_driver_ids[route.vehicle_id]
            if person_id != route.driver_id
        )


def _qualified_driver_ids_by_vehicle(
    instance: LoadedVrpInstance,
) -> dict[str, tuple[str, ...]]:
    skills_by_person: dict[str, set[str]] = {}
    for item in instance.personnel_data.personnel_driving_skills:
        skills_by_person.setdefault(item.person_id, set()).add(item.skill_id)

    qualified_by_vehicle: dict[str, tuple[str, ...]] = {}
    for vehicle in instance.fleet_data.vehicles:
        required_skills = {
            item.skill_id
            for item in instance.fleet_data.vehicle_driving_skills
            if item.vehicle_id == vehicle.vehicle_id
        }
        qualified_by_vehicle[vehicle.vehicle_id] = tuple(
            sorted(
                person.person_id
                for person in instance.personnel_data.personnel
                if person.authorized_driver
                and required_skills <= skills_by_person.get(person.person_id, set())
            )
        )
    return qualified_by_vehicle
