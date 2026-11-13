"""Intervention-team assignment Operators for VRP personnel features.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from functools import cmp_to_key
from itertools import combinations
from math import isfinite
from typing import Literal

from multiobjective_vns.identifiers import ObjectiveId
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
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    VehicleDayRoute,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

type TeamAssignmentMode = Literal['general', 'seat_aware']
type _TeamTarget = tuple[int, int]
type _TeamCandidate = tuple[VrpSolution, tuple[object, ...]]

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
        '_interventions_by_id',
        '_required_by_intervention',
        '_qualifications_by_person',
        '_people',
        '_seat_capacity_by_vehicle',
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
        self._interventions_by_id = {
            item.intervention_id: item
            for item in instance.intervention_data.interventions
        }
        required_by_intervention: dict[str, set[str]] = {}
        for (
            required_record
        ) in instance.intervention_data.intervention_qualifications_required:
            required_by_intervention.setdefault(
                required_record.intervention_id, set()
            ).add(required_record.qualification_id)
        self._required_by_intervention = {
            intervention_id: frozenset(qualifications)
            for intervention_id, qualifications in required_by_intervention.items()
        }
        qualifications_by_person: dict[str, set[str]] = {}
        for (
            qualification_record
        ) in instance.personnel_data.personnel_intervention_qualifications:
            qualifications_by_person.setdefault(
                qualification_record.person_id, set()
            ).add(qualification_record.qualification_id)
        self._qualifications_by_person = {
            person_id: frozenset(qualifications)
            for person_id, qualifications in qualifications_by_person.items()
        }
        self._people = tuple(
            sorted(person.person_id for person in instance.personnel_data.personnel)
        )
        self._seat_capacity_by_vehicle = {
            vehicle.vehicle_id: vehicle.seat_capacity
            for vehicle in instance.fleet_data.vehicles
        }

    def call(self, context: CandidateContext) -> OperatorResult:
        """Change up to ``k`` intervention teams and return a candidate batch.

        :param context: Base plan, requested neighborhood size, feasibility
            request, shared random source, and candidate batch limit.
        :returns: Up to ``context.max_candidates`` complete candidate plans.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        changed: set[_TeamTarget] = set()
        modifications = 0
        last_candidates: tuple[_TeamCandidate, ...] = ()
        for _ in range(size):
            targets = tuple(
                target for target in self._targets(working) if target not in changed
            )
            if not targets:
                break
            if self._mode == 'seat_aware':
                routes = tuple(dict.fromkeys(route_index for route_index, _ in targets))
                route_index = choose_uniformly(routes, context.random_source)
                route_targets = tuple(
                    target for target in targets if target[0] == route_index
                )
                target = choose_uniformly(route_targets, context.random_source)
            else:
                target = choose_uniformly(targets, context.random_source)
            candidates = self._candidates(working, target, context)
            changed.add(target)
            if not candidates:
                continue
            last_candidates = _order_team_candidates(
                candidates,
                self._objectives,
                self._objective_position,
                context.random_source,
            )
            working = last_candidates[0][0]
            modifications += 1
        if not modifications:
            return OperatorResult(candidates=())
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), modifications)
                for solution, _values in last_candidates[: context.max_candidates]
            )
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
        route_indices = tuple(
            dict.fromkeys(route_index for route_index, _ in interventions)
        )
        crew_by_route = {
            route_index: _crew(solution.routes[route_index])
            for route_index in route_indices
        }
        maximum_crew = max(
            len(crew_by_route[route_index]) for route_index in route_indices
        )
        saturated_routes = {
            route_index
            for route_index in route_indices
            if len(crew_by_route[route_index])
            == self._seat_capacity_by_vehicle[solution.routes[route_index].vehicle_id]
        }
        if saturated_routes:
            return tuple(item for item in interventions if item[0] in saturated_routes)
        fullest_routes = {
            route_index
            for route_index in route_indices
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
        intervention_id = stop.activity_ref.activity_id
        intervention = self._interventions_by_id.get(intervention_id)
        if intervention is None:
            return ()
        other_crew = _crew_without_stop(route, stop_index)
        alternatives = _enumerated_eligible_teams_for_route(
            intervention.minimum_team_size,
            self._required_by_intervention.get(intervention_id, frozenset()),
            self._people,
            self._qualifications_by_person,
            other_crew,
            self._seat_capacity_by_vehicle[route.vehicle_id],
            stop.team_member_ids,
            random_source=context.random_source,
            max_candidates=self._candidate_limit,
        )
        candidates: list[_TeamCandidate] = []
        for team in alternatives:
            stops = list(route.stops)
            stops[stop_index] = replace(stop, team_member_ids=team)
            routes = list(solution.routes)
            routes[route_index] = replace(route, stops=tuple(stops))
            candidate = replace(solution, routes=tuple(routes))
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


def _eligible_teams(
    instance: LoadedVrpInstance,
    intervention_id: str,
    *,
    random_source: RandomSource,
    max_samples: int,
) -> tuple[frozenset[str], ...]:
    """Return qualification-valid teams for personnel repair call sites."""
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
    required = frozenset(
        item.qualification_id
        for item in instance.intervention_data.intervention_qualifications_required
        if item.intervention_id == intervention_id
    )
    qualifications_by_person: dict[str, set[str]] = {}
    for item in instance.personnel_data.personnel_intervention_qualifications:
        qualifications_by_person.setdefault(item.person_id, set()).add(
            item.qualification_id
        )
    people = tuple(
        sorted(person.person_id for person in instance.personnel_data.personnel)
    )
    return _eligible_teams_for_route(
        intervention.minimum_team_size,
        required,
        people,
        {
            person_id: frozenset(qualifications)
            for person_id, qualifications in qualifications_by_person.items()
        },
        frozenset(),
        len(people),
        frozenset(),
        random_source=random_source,
        max_candidates=max_samples,
        max_samples=max_samples,
    )


def _enumerated_eligible_teams_for_route(
    minimum_team_size: int,
    required_qualifications: frozenset[str],
    people: tuple[str, ...],
    qualifications_by_person: dict[str, frozenset[str]],
    other_crew: frozenset[str],
    seat_capacity: int,
    excluded_team: frozenset[str],
    *,
    random_source: RandomSource,
    max_candidates: int,
) -> tuple[frozenset[str], ...]:
    """Uniformly sample qualified, seat-feasible teams using reservoir sampling."""
    if len(other_crew) > seat_capacity or minimum_team_size > min(
        len(people), seat_capacity
    ):
        return ()

    qualification_bits = {
        qualification: 1 << index
        for index, qualification in enumerate(sorted(required_qualifications))
    }
    required_mask = (1 << len(qualification_bits)) - 1
    qualification_masks = {
        person_id: sum(
            qualification_bits[qualification]
            for qualification in qualifications
            if qualification in qualification_bits
        )
        for person_id, qualifications in qualifications_by_person.items()
    }
    seat_costs = tuple(person_id not in other_crew for person_id in people)
    person_qualification_masks = tuple(
        qualification_masks.get(person_id, 0) for person_id in people
    )
    selected: list[frozenset[str]] = []
    eligible_count = 0
    maximum_team_size = min(len(people), seat_capacity)
    for size in range(minimum_team_size, maximum_team_size + 1):
        for member_indices in combinations(range(len(people)), size):
            if size == len(excluded_team) and all(
                people[index] in excluded_team for index in member_indices
            ):
                continue
            crew_size = len(other_crew)
            team_qualifications = 0
            for index in member_indices:
                if seat_costs[index]:
                    crew_size += 1
                    if crew_size > seat_capacity:
                        break
                team_qualifications |= person_qualification_masks[index]
            if crew_size > seat_capacity:
                continue
            if team_qualifications & required_mask != required_mask:
                continue

            team = frozenset(people[index] for index in member_indices)
            eligible_count += 1
            if len(selected) < max_candidates:
                selected.append(team)
                continue
            replacement_index = _random_index(eligible_count, random_source)
            if replacement_index < max_candidates:
                selected[replacement_index] = team
    return tuple(selected)


def _eligible_teams_for_route(
    minimum_team_size: int,
    required_qualifications: frozenset[str],
    people: tuple[str, ...],
    qualifications_by_person: dict[str, frozenset[str]],
    other_crew: frozenset[str],
    seat_capacity: int,
    excluded_team: frozenset[str],
    *,
    random_source: RandomSource,
    max_candidates: int,
    max_samples: int,
) -> tuple[frozenset[str], ...]:
    if len(other_crew) > seat_capacity or minimum_team_size > min(
        len(people), seat_capacity
    ):
        return ()
    result: list[frozenset[str]] = []
    seen: set[frozenset[str]] = set()
    sizes = tuple(range(minimum_team_size, min(len(people), seat_capacity) + 1))
    for _ in range(max_samples):
        size = choose_uniformly(sizes, random_source)
        team = frozenset(randomized_sample(people, size, random_source))
        if team == excluded_team or team in seen:
            continue
        seen.add(team)
        crew_size = len(other_crew)
        for person_id in team:
            if person_id not in other_crew:
                crew_size += 1
        if crew_size > seat_capacity:
            continue
        missing = set(required_qualifications)
        for person_id in team:
            missing.difference_update(qualifications_by_person.get(person_id, ()))
            if not missing:
                break
        if missing:
            continue
        result.append(team)
        if len(result) >= max_candidates:
            break
    return tuple(result)


def _random_index(count: int, random_source: RandomSource) -> int:
    draw = random_source.random()
    if type(draw) is not float:
        raise TypeError('RandomSource.random() must return a Python float')
    if not isfinite(draw) or not 0.0 <= draw < 1.0:
        raise ValueError('RandomSource.random() must return a float in [0, 1)')
    return min(int(draw * count), count - 1)


def _crew(route: VehicleDayRoute) -> set[str]:
    return {
        route.driver_id,
        *(
            person_id
            for stop in route.stops
            if type(stop) is ActivityStop
            for person_id in stop.team_member_ids
        ),
    }


def _crew_without_stop(
    route: VehicleDayRoute, excluded_stop_index: int
) -> frozenset[str]:
    crew = {route.driver_id}
    crew.update(
        person_id
        for stop_index, stop in enumerate(route.stops)
        if stop_index != excluded_stop_index and type(stop) is ActivityStop
        for person_id in stop.team_member_ids
    )
    return frozenset(crew)


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


def _order_team_candidates(
    candidates: tuple[_TeamCandidate, ...],
    objectives: ObjectiveDeclaration,
    objective_index: int | None,
    random_source: RandomSource,
) -> tuple[_TeamCandidate, ...]:
    if objective_index is None:
        return randomized_order(candidates, random_source)
    objective = objectives.objectives[objective_index]

    def compare(left: _TeamCandidate, right: _TeamCandidate) -> int:
        comparison = objective.compare(
            left[1][objective_index], right[1][objective_index]
        )
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    ranked = sorted(candidates, key=cmp_to_key(compare))
    ordered: list[_TeamCandidate] = []
    group_start = 0
    while group_start < len(ranked):
        group_end = group_start + 1
        while (
            group_end < len(ranked)
            and compare(ranked[group_start], ranked[group_end]) == 0
        ):
            group_end += 1
        ordered.extend(randomized_order(ranked[group_start:group_end], random_source))
        group_start = group_end
    return tuple(ordered)
