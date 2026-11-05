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
    operator_result,
    randomized_sample,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
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

    def call(self, context: CandidateContext) -> OperatorResult:
        """Reassign up to ``k`` routes to different qualified drivers.

        Driver skill qualification is a structural candidate filter. The
        shared plan assessor evaluates availability, duty overlap, and other
        driver or vehicle constraints.

        :param context: Base plan, requested size, feasibility, and shared RNG.
        :returns: Changed assignments and their count, or a zero-count result.
        :raises TypeError: If the base or assessment callback is malformed.
        :raises ValueError: If the requested size exceeds the registration limit.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        available_routes = set(range(len(base.routes)))
        modifications = 0
        while modifications < size and available_routes:
            eligible_routes = tuple(
                route_index
                for route_index in sorted(available_routes)
                if self._qualified_alternatives(working, route_index)
            )
            if not eligible_routes:
                break
            route_index = choose_uniformly(eligible_routes, context.random_source)
            alternatives = randomized_sample(
                self._qualified_alternatives(working, route_index),
                self._candidate_limit,
                context.random_source,
            )
            route = working.routes[route_index]
            candidates: list[VrpSolution] = []
            for driver_id in alternatives:
                routes = list(working.routes)
                routes[route_index] = replace(route, driver_id=driver_id)
                candidate = replace(working, routes=tuple(routes))
                if candidate_is_eligible(
                    self._instance,
                    self._objectives,
                    candidate,
                    ensure_feasible=context.ensure_feasible,
                    assess_plan=self._assess_plan,
                ):
                    candidates.append(candidate)
            available_routes.remove(route_index)
            if not candidates:
                continue
            working = choose_uniformly(candidates, context.random_source)
            modifications += 1

        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _qualified_alternatives(
        self, solution: VrpSolution, route_index: int
    ) -> tuple[str, ...]:
        route = solution.routes[route_index]
        vehicle = next(
            item
            for item in self._instance.fleet_data.vehicles
            if item.vehicle_id == route.vehicle_id
        )
        required_skills = {
            item.skill_id
            for item in self._instance.fleet_data.vehicle_driving_skills
            if item.vehicle_id == vehicle.vehicle_id
        }
        skills_by_person: dict[str, set[str]] = {}
        for item in self._instance.personnel_data.personnel_driving_skills:
            skills_by_person.setdefault(item.person_id, set()).add(item.skill_id)
        return tuple(
            sorted(
                person.person_id
                for person in self._instance.personnel_data.personnel
                if person.authorized_driver
                and person.person_id != route.driver_id
                and required_skills <= skills_by_person.get(person.person_id, set())
            )
        )
