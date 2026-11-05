"""Intervention-team assignment Operators for VRP personnel features.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

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
from multiobjective_vns.multiday_vrp.solution import ActivityStop, VrpSolution
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

type TeamAssignmentMode = Literal['general', 'seat_aware']
type _TeamTarget = tuple[int, int]

__all__ = ['InterventionTeamReassignmentOperator', 'TeamAssignmentMode']


class InterventionTeamReassignmentOperator:
    """Replace intervention teams with qualification-valid personnel sets.

    Personnel availability remains an assessment and ranking concern. Team
    generation applies qualification, minimum team-size, and vehicle-seat
    constraints as specified by the feature Operator contract.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_mode',
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
        mode: TeamAssignmentMode,
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
    ) -> None:
        if mode not in {'general', 'seat_aware'}:
            raise ValueError(f'unsupported team assignment mode: {mode!r}')
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._mode = mode
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
        """Change up to ``k`` intervention team assignments sequentially."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        changed: set[_TeamTarget] = set()
        modifications = 0
        for _ in range(size):
            targets = tuple(
                target for target in self._targets(working) if target not in changed
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            candidates = self._candidates(working, target, context)
            changed.add(target)
            if not candidates:
                continue
            working = self._choose(candidates, context)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _targets(self, solution: VrpSolution) -> tuple[_TeamTarget, ...]:
        interventions = tuple(
            (route_index, stop_index)
            for route_index, route in enumerate(solution.routes)
            for stop_index, stop in enumerate(route.stops)
            if type(stop) is ActivityStop and stop.activity_ref.kind == 'intervention'
        )
        if self._mode == 'general' or not interventions:
            return interventions
        crew_by_route = {
            route_index: _crew(route)
            for route_index, route in enumerate(solution.routes)
        }
        capacity_by_vehicle = {
            vehicle.vehicle_id: vehicle.seat_capacity
            for vehicle in self._instance.fleet_data.vehicles
        }
        maximum_crew = max(
            len(crew_by_route[route_index]) for route_index, _ in interventions
        )
        saturated_routes = {
            route_index
            for route_index, _ in interventions
            if len(crew_by_route[route_index])
            == capacity_by_vehicle[solution.routes[route_index].vehicle_id]
        }
        if saturated_routes:
            return tuple(item for item in interventions if item[0] in saturated_routes)
        fullest_routes = {
            route_index
            for route_index, _ in interventions
            if len(crew_by_route[route_index]) == maximum_crew
        }
        return tuple(item for item in interventions if item[0] in fullest_routes)

    def _candidates(
        self,
        solution: VrpSolution,
        target: _TeamTarget,
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
        route_index, stop_index = target
        route = solution.routes[route_index]
        stop = route.stops[stop_index]
        if type(stop) is not ActivityStop:
            return ()
        alternatives = _eligible_teams(
            self._instance,
            stop.activity_ref.activity_id,
            random_source=context.random_source,
            max_samples=self._candidate_limit * 32,
        )
        alternatives = tuple(
            team for team in alternatives if team != stop.team_member_ids
        )
        sampled = randomized_sample(
            alternatives, self._candidate_limit, context.random_source
        )
        candidates: list[tuple[VrpSolution, tuple[object, ...]]] = []
        for team in sampled:
            stops = list(route.stops)
            stops[stop_index] = replace(stop, team_member_ids=team)
            routes = list(solution.routes)
            routes[route_index] = replace(route, stops=tuple(stops))
            candidate = replace(solution, routes=tuple(routes))
            if not _seat_feasible(self._instance, candidate, route_index):
                continue
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
            candidates.append((candidate, evaluation.objective_values))
        return tuple(candidates)

    def _choose(
        self,
        candidates: tuple[tuple[VrpSolution, tuple[object, ...]], ...],
        context: CandidateContext,
    ) -> VrpSolution:
        if self._objective_position is None:
            return choose_uniformly(candidates, context.random_source)[0]
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
        return choose_uniformly(tied, context.random_source)[0]


def _eligible_teams(
    instance: LoadedVrpInstance,
    intervention_id: str,
    *,
    random_source: RandomSource,
    max_samples: int,
) -> tuple[frozenset[str], ...]:
    intervention = next(
        (
            item
            for item in instance.intervention_data.interventions
            if item.intervention_id == intervention_id
        ),
        None,
    )
    if intervention is None:
        return ()
    required = {
        item.qualification_id
        for item in instance.intervention_data.intervention_qualifications_required
        if item.intervention_id == intervention_id
    }
    qualifications: dict[str, set[str]] = {}
    for item in instance.personnel_data.personnel_intervention_qualifications:
        qualifications.setdefault(item.person_id, set()).add(item.qualification_id)
    people = tuple(
        sorted(person.person_id for person in instance.personnel_data.personnel)
    )
    if intervention.minimum_team_size > len(people):
        return ()
    result: list[frozenset[str]] = []
    seen: set[frozenset[str]] = set()
    sizes = tuple(range(intervention.minimum_team_size, len(people) + 1))
    for _ in range(max_samples):
        size = choose_uniformly(sizes, random_source)
        team = frozenset(randomized_sample(people, size, random_source))
        if team in seen:
            continue
        seen.add(team)
        covered = set().union(*(qualifications.get(person, set()) for person in team))
        if required <= covered:
            result.append(team)
    return tuple(result)


def _crew(route: object) -> set[str]:
    driver_id = getattr(route, 'driver_id')
    stops = getattr(route, 'stops')
    return {
        driver_id,
        *(
            person_id
            for stop in stops
            if type(stop) is ActivityStop
            for person_id in stop.team_member_ids
        ),
    }


def _seat_feasible(
    instance: LoadedVrpInstance, solution: VrpSolution, route_index: int
) -> bool:
    route = solution.routes[route_index]
    vehicle = next(
        item
        for item in instance.fleet_data.vehicles
        if item.vehicle_id == route.vehicle_id
    )
    return len(_crew(route)) <= vehicle.seat_capacity
