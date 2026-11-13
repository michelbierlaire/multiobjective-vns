"""Personnel assignment and work-rule repair Operators for the VRP.

Created: 2026-11-04T09:56:38+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    assess_complete_candidate,
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.personnel_features import (
    _eligible_teams_for_route,
    _seat_feasible,
)
from multiobjective_vns.multiday_vrp.operators.personnel_repair_support import (
    DutyTarget,
    PersonnelRepairMode,
    RepairTarget,
    _continuity_index,
    _ContinuityIndex,
    _staff_continuity_replacement_compatible,
    repair_score,
    restricted_calendar_people,
)
from multiobjective_vns.multiday_vrp.operators.personnel_repair_targets import (
    select_targets,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
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

__all__ = ['PersonnelRepairMode', 'PersonnelRepairOperator']


@dataclass(frozen=True, slots=True)
class _Alternative:
    """One complete plan and its application assessment."""

    solution: VrpSolution
    evaluation: ApplicationEvaluation


class PersonnelRepairOperator:
    """Repair skill, availability, staff-continuity, or work-rule assignments.

    The mode fixes the target-selection rule. Each move changes one person-duty
    assignment and assesses every complete replacement plan. Multi-step calls
    retain intermediate changes internally; a requested feasible result is
    returned only when the final complete plan satisfies the feasibility
    request.

    :param instance: Validated VRP instance captured by this Operator.
    :param objective_declaration: Ordered objective comparison definitions.
    :param assess_plan: Complete-plan application assessor.
    :param mode: Personnel feature repaired by this registration.
    :param max_neighborhood_size: Maximum person-duty assignments changed.
    :param objective_id: Objective used to rank alternatives, when applicable.
    :param max_candidate_alternatives: Maximum complete replacements assessed.
    :raises ValueError: If the mode or its objective configuration is invalid.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_mode',
        '_objective_id',
        '_objective_position',
        '_maximum_size',
        '_candidate_limit',
        '_qualified_drivers_by_vehicle',
        '_qualified_driver_sets_by_vehicle',
        '_seat_capacity_by_vehicle',
        '_restricted_personnel_ids',
        '_personnel_ids',
        '_qualifications_by_person',
        '_minimum_team_size_by_intervention',
        '_required_qualifications_by_intervention',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        mode: PersonnelRepairMode,
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
    ) -> None:
        if mode not in {
            'skill_guided',
            'availability',
            'staff_continuity',
            'work_rule',
        }:
            raise ValueError(f'unsupported personnel repair mode: {mode!r}')
        if mode in {'availability', 'work_rule'} and objective_id is None:
            raise ValueError(f'{mode} repair requires an objective_id')
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._mode = mode
        self._objective_id = objective_id
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
        self._qualified_drivers_by_vehicle = _qualified_driver_index(instance)
        self._qualified_driver_sets_by_vehicle = {
            vehicle_id: frozenset(person_ids)
            for vehicle_id, person_ids in self._qualified_drivers_by_vehicle.items()
        }
        self._seat_capacity_by_vehicle = {
            item.vehicle_id: item.seat_capacity for item in instance.fleet_data.vehicles
        }
        self._personnel_ids = tuple(
            sorted(person.person_id for person in instance.personnel_data.personnel)
        )
        qualifications_by_person: dict[str, set[str]] = {}
        for (
            qualification
        ) in instance.personnel_data.personnel_intervention_qualifications:
            qualifications_by_person.setdefault(qualification.person_id, set()).add(
                qualification.qualification_id
            )
        self._qualifications_by_person = {
            person_id: frozenset(qualifications)
            for person_id, qualifications in qualifications_by_person.items()
        }
        self._minimum_team_size_by_intervention = {
            item.intervention_id: item.minimum_team_size
            for item in instance.intervention_data.interventions
        }
        required_qualifications: dict[str, set[str]] = {}
        for item in instance.intervention_data.intervention_qualifications_required:
            required_qualifications.setdefault(item.intervention_id, set()).add(
                item.qualification_id
            )
        self._required_qualifications_by_intervention = {
            intervention_id: frozenset(qualifications)
            for intervention_id, qualifications in required_qualifications.items()
        }
        self._restricted_personnel_ids = (
            restricted_calendar_people(instance)
            if mode == 'availability'
            else frozenset()
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Change up to ``k`` person-duty assignments and return a full plan."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        working_evaluation = self._stored_base_evaluation(context, base)
        completed_targets: set[DutyTarget] = set()
        completed_skill_routes: set[int] = set()
        exhausted_targets: set[DutyTarget] = set()
        modifications = 0
        assessed_alternatives = 0
        availability_alternatives: tuple[_Alternative, ...] = ()
        continuity_alternatives: tuple[_Alternative, ...] = ()
        work_rule_alternatives: tuple[_Alternative, ...] = ()

        while modifications < size:
            if working_evaluation is None:
                working_evaluation = self._assess(working)
            continuity = (
                _continuity_index(self._instance, working)
                if self._mode == 'staff_continuity'
                else None
            )
            targets = tuple(
                target
                for target in self._targets(
                    working,
                    working_evaluation,
                    continuity_index=continuity,
                    random_source=context.random_source,
                )
                if (
                    target.duty.route_index not in completed_skill_routes
                    if self._mode == 'skill_guided'
                    else target.duty not in completed_targets
                )
                and target.duty not in exhausted_targets
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            remaining_steps = size - modifications
            remaining_budget = self._candidate_limit - assessed_alternatives
            if remaining_budget <= 0:
                break
            per_step_limit = max(1, remaining_budget // remaining_steps)
            alternatives = self._replacement_alternatives(
                working,
                target.duty,
                context,
                max_alternatives=per_step_limit,
                continuity_index=continuity,
            )
            if not alternatives:
                exhausted_targets.add(target.duty)
                continue

            assessed_alternatives += len(alternatives)
            evaluated = self._assess_alternatives(alternatives)
            if not evaluated:
                exhausted_targets.add(target.duty)
                continue

            if self._mode in {'availability', 'staff_continuity'}:
                eligible = evaluated
                if context.ensure_feasible:
                    feasible = tuple(
                        item for item in eligible if item.evaluation.feasible is True
                    )
                    if feasible:
                        eligible = feasible
                if self._mode == 'availability':
                    availability_alternatives = self._order_objective_alternatives(
                        eligible, context
                    )
                    selected = availability_alternatives[0]
                else:
                    continuity_alternatives = (
                        self._order_objective_alternatives(eligible, context)
                        if self._objective_position is not None
                        else randomized_order(eligible, context.random_source)
                    )
                    selected = continuity_alternatives[0]
            elif self._mode == 'work_rule':
                work_rule_alternatives = self._order_work_rule_alternatives(
                    evaluated, target, context
                )
                selected = work_rule_alternatives[0]
            else:
                selected = self._select_alternative(evaluated, context)
            working = selected.solution
            working_evaluation = selected.evaluation
            if self._mode == 'skill_guided':
                completed_skill_routes.add(target.duty.route_index)
            else:
                completed_targets.add(target.duty)
            modifications += 1

        if modifications == 0 or working_evaluation is None:
            return operator_result(None, modifications=0, requested_size=size)
        if context.ensure_feasible and working_evaluation.feasible is not True:
            return operator_result(None, modifications=0, requested_size=size)
        if self._mode == 'availability':
            return OperatorResult(
                candidates=tuple(
                    CandidateProposal(Candidate(item.solution), modifications)
                    for item in availability_alternatives[: context.max_candidates]
                )
            )
        if self._mode == 'staff_continuity':
            return OperatorResult(
                candidates=tuple(
                    CandidateProposal(Candidate(item.solution), modifications)
                    for item in continuity_alternatives[: context.max_candidates]
                )
            )
        if self._mode == 'work_rule':
            qualifying = work_rule_alternatives
            if context.ensure_feasible:
                qualifying = tuple(
                    item for item in qualifying if item.evaluation.feasible is True
                )
            return OperatorResult(
                candidates=tuple(
                    CandidateProposal(Candidate(item.solution), modifications)
                    for item in qualifying[: context.max_candidates]
                )
            )
        return operator_result(
            working,
            modifications=modifications,
            requested_size=size,
        )

    def _stored_base_evaluation(
        self, context: CandidateContext, base: VrpSolution
    ) -> ApplicationEvaluation | None:
        del base
        record = context.base_evaluation
        if record is None or record.application_evaluation is None:
            return None
        return record.application_evaluation

    def _assess(self, solution: VrpSolution) -> ApplicationEvaluation | None:
        return assess_complete_candidate(
            self._instance,
            self._objectives,
            solution,
            self._assess_plan,
            ensure_feasible=False,
        )

    def _targets(
        self,
        solution: VrpSolution,
        evaluation: ApplicationEvaluation | None,
        *,
        continuity_index: _ContinuityIndex | None = None,
        random_source: RandomSource | None = None,
    ) -> tuple[RepairTarget, ...]:
        if self._mode == 'skill_guided':
            violating = tuple(
                index
                for index, route in enumerate(solution.routes)
                if route.driver_id
                not in self._qualified_driver_sets_by_vehicle[route.vehicle_id]
            )
            if violating:
                route_indices = violating
            elif solution.routes:
                counts = tuple(
                    len(self._qualified_drivers_by_vehicle[route.vehicle_id])
                    for route in solution.routes
                )
                minimum = min(counts)
                route_indices = tuple(
                    index for index, count in enumerate(counts) if count == minimum
                )
            else:
                route_indices = ()
            return tuple(
                RepairTarget(
                    DutyTarget(index, solution.routes[index].driver_id, 'driver')
                )
                for index in route_indices
            )
        return select_targets(
            self._instance,
            self._mode,
            solution,
            evaluation,
            self._objective_id,
            restricted_personnel_ids=self._restricted_personnel_ids,
            continuity_index=continuity_index,
            random_source=random_source,
        )

    def _replacement_alternatives(
        self,
        solution: VrpSolution,
        duty: DutyTarget,
        context: CandidateContext,
        *,
        max_alternatives: int | None = None,
        continuity_index: _ContinuityIndex | None = None,
    ) -> tuple[VrpSolution, ...]:
        candidate_limit = (
            self._candidate_limit
            if max_alternatives is None
            else min(self._candidate_limit, max_alternatives)
        )
        if candidate_limit < 1:
            return ()
        route = solution.routes[duty.route_index]
        continuity = continuity_index
        if self._mode == 'staff_continuity' and continuity is None:
            continuity = _continuity_index(self._instance, solution)
        replace_team = duty.role == 'team'
        affected_stops = tuple(
            stop_index
            for stop_index, stop in enumerate(route.stops)
            if replace_team
            and type(stop) is ActivityStop
            and duty.person_id in stop.team_member_ids
            and (duty.activity_ref is None or stop.activity_ref == duty.activity_ref)
        )

        if duty.role == 'driver':
            driver_options = tuple(
                person_id
                for person_id in self._qualified_drivers_by_vehicle[route.vehicle_id]
                if person_id != duty.person_id
            )
        else:
            driver_options = (route.driver_id,)
        if not driver_options:
            return ()

        team_domains: list[tuple[int, tuple[frozenset[str], ...]]] = []
        affected_stop_set = frozenset(affected_stops)
        unchanged_crew = {route.driver_id}
        for stop_index, stop in enumerate(route.stops):
            if stop_index not in affected_stop_set and type(stop) is ActivityStop:
                unchanged_crew.update(stop.team_member_ids)
        for stop_index in affected_stops:
            stop = route.stops[stop_index]
            assert type(stop) is ActivityStop
            teams = self._replacement_team_options(
                stop,
                duty.person_id,
                frozenset(unchanged_crew),
                self._seat_capacity_by_vehicle[route.vehicle_id],
                random_source=context.random_source,
                max_candidates=candidate_limit,
            )
            alternatives = tuple(
                team
                for team in teams
                if duty.person_id not in team and team != stop.team_member_ids
            )
            if not alternatives:
                return ()
            team_domains.append((stop_index, alternatives))

        result: list[VrpSolution] = []
        seen: set[tuple[str, tuple[tuple[int, tuple[str, ...]], ...]]] = set()
        if self._mode == 'skill_guided':
            team_members = {
                person_id
                for stop in route.stops
                if type(stop) is ActivityStop
                for person_id in stop.team_member_ids
            }
            seat_capacity = self._seat_capacity_by_vehicle[route.vehicle_id]
            seat_feasible_drivers = tuple(
                person_id
                for person_id in driver_options
                if len(team_members | {person_id}) <= seat_capacity
            )
            sampled_drivers = randomized_sample(
                seat_feasible_drivers,
                min(candidate_limit, len(seat_feasible_drivers)),
                context.random_source,
            )
            for driver_id in sampled_drivers:
                routes = list(solution.routes)
                routes[duty.route_index] = replace(route, driver_id=driver_id)
                result.append(replace(solution, routes=tuple(routes)))
            return tuple(result)

        attempt_limit = max(candidate_limit * 32, candidate_limit)
        for _ in range(attempt_limit):
            driver_id = choose_uniformly(driver_options, context.random_source)
            team_changes = tuple(
                (
                    stop_index,
                    choose_uniformly(teams, context.random_source),
                )
                for stop_index, teams in team_domains
            )
            signature = (
                driver_id,
                tuple((index, tuple(sorted(team))) for index, team in team_changes),
            )
            if signature in seen:
                continue
            seen.add(signature)
            stops = list(route.stops)
            for stop_index, team in team_changes:
                stop = stops[stop_index]
                assert type(stop) is ActivityStop
                stops[stop_index] = replace(stop, team_member_ids=team)
            routes = list(solution.routes)
            routes[duty.route_index] = replace(
                route,
                driver_id=driver_id,
                stops=tuple(stops),
            )
            candidate = replace(solution, routes=tuple(routes))
            if candidate == solution or not _seat_feasible(
                self._instance, candidate, duty.route_index
            ):
                continue
            if self._mode == 'staff_continuity':
                assert continuity is not None
                if not _staff_continuity_replacement_compatible(
                    self._instance,
                    solution,
                    candidate,
                    duty,
                    continuity,
                ):
                    continue
            result.append(candidate)
            if len(result) >= candidate_limit:
                break
        return tuple(result)

    def _replacement_team_options(
        self,
        stop: ActivityStop,
        person_id: str,
        unchanged_crew: frozenset[str],
        seat_capacity: int,
        *,
        random_source: RandomSource,
        max_candidates: int,
    ) -> tuple[frozenset[str], ...]:
        intervention_id = stop.activity_ref.activity_id
        minimum_team_size = self._minimum_team_size_by_intervention.get(intervention_id)
        if minimum_team_size is None:
            return ()
        people = tuple(
            candidate for candidate in self._personnel_ids if candidate != person_id
        )
        return _eligible_teams_for_route(
            minimum_team_size,
            self._required_qualifications_by_intervention.get(
                intervention_id, frozenset()
            ),
            people,
            self._qualifications_by_person,
            unchanged_crew,
            seat_capacity,
            stop.team_member_ids,
            random_source=random_source,
            max_candidates=max_candidates,
            max_samples=max(64, max_candidates * 16),
        )

    def _assess_alternatives(
        self, alternatives: tuple[VrpSolution, ...]
    ) -> tuple[_Alternative, ...]:
        result: list[_Alternative] = []
        for candidate in alternatives:
            evaluation = self._assess(candidate)
            if evaluation is not None:
                result.append(_Alternative(candidate, evaluation))
        return tuple(result)

    def _repair_score(
        self,
        solution: VrpSolution,
        evaluation: ApplicationEvaluation | None,
        target: RepairTarget,
    ) -> float:
        return repair_score(self._instance, self._mode, solution, evaluation, target)

    def _select_alternative(
        self,
        alternatives: tuple[_Alternative, ...],
        context: CandidateContext,
    ) -> _Alternative:
        ranked = alternatives
        if context.ensure_feasible:
            feasible = tuple(
                item for item in ranked if item.evaluation.feasible is True
            )
            if feasible:
                ranked = feasible
        if self._objective_position is not None and len(ranked) > 1:
            ranked = self._best_by_objective(ranked)
        return choose_uniformly(ranked, context.random_source)

    def _order_work_rule_alternatives(
        self,
        alternatives: tuple[_Alternative, ...],
        target: RepairTarget,
        context: CandidateContext,
    ) -> tuple[_Alternative, ...]:
        feasible = tuple(
            item for item in alternatives if item.evaluation.feasible is True
        )
        infeasible = tuple(
            item for item in alternatives if item.evaluation.feasible is not True
        )
        ordered_feasible = (
            self._order_objective_alternatives(feasible, context) if feasible else ()
        )
        if target.finding is None:
            ordered_infeasible = randomized_order(infeasible, context.random_source)
        else:
            scored = tuple(
                (
                    item,
                    self._repair_score(item.solution, item.evaluation, target),
                )
                for item in infeasible
            )
            ranked = sorted(scored, key=lambda pair: pair[1])
            ordered: list[_Alternative] = []
            group_start = 0
            while group_start < len(ranked):
                group_end = group_start + 1
                while (
                    group_end < len(ranked)
                    and ranked[group_end][1] == ranked[group_start][1]
                ):
                    group_end += 1
                ordered.extend(
                    randomized_order(
                        tuple(item for item, _score in ranked[group_start:group_end]),
                        context.random_source,
                    )
                )
                group_start = group_end
            ordered_infeasible = tuple(ordered)
        return (*ordered_feasible, *ordered_infeasible)

    def _best_by_objective(
        self, alternatives: tuple[_Alternative, ...]
    ) -> tuple[_Alternative, ...]:
        assert self._objective_position is not None
        objective_position = self._objective_position
        objective = self._objectives.objectives[objective_position]
        best = alternatives[0]
        tied = [best]
        for candidate in alternatives[1:]:
            candidate_values = candidate.evaluation.objective_values
            best_values = best.evaluation.objective_values
            if candidate_values is None or best_values is None:
                raise ValueError('valid VRP assessment requires objective values')
            comparison = objective.compare(
                candidate_values[self._objective_position],
                best_values[self._objective_position],
            )
            if comparison is ObjectiveComparison.LESS:
                best = candidate
                tied = [candidate]
            elif comparison is ObjectiveComparison.EQUAL:
                tied.append(candidate)
        return tuple(tied)

    def _order_objective_alternatives(
        self,
        alternatives: tuple[_Alternative, ...],
        context: CandidateContext,
    ) -> tuple[_Alternative, ...]:
        assert self._objective_position is not None
        objective_position = self._objective_position
        objective = self._objectives.objectives[objective_position]

        def compare(left: _Alternative, right: _Alternative) -> int:
            left_values = left.evaluation.objective_values
            right_values = right.evaluation.objective_values
            if left_values is None or right_values is None:
                raise ValueError('valid VRP assessment requires objective values')
            result = objective.compare(
                left_values[objective_position],
                right_values[objective_position],
            )
            if result is ObjectiveComparison.LESS:
                return -1
            if result is ObjectiveComparison.GREATER:
                return 1
            return 0

        ranked = sorted(alternatives, key=cmp_to_key(compare))
        ordered: list[_Alternative] = []
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


def _qualified_driver_index(instance: LoadedVrpInstance) -> dict[str, tuple[str, ...]]:
    """Build each vehicle's stable list of authorized, skill-qualified drivers."""
    required_by_vehicle: dict[str, set[str]] = {
        vehicle.vehicle_id: set() for vehicle in instance.fleet_data.vehicles
    }
    for vehicle_skill in instance.fleet_data.vehicle_driving_skills:
        required_by_vehicle.setdefault(vehicle_skill.vehicle_id, set()).add(
            vehicle_skill.skill_id
        )

    skills_by_person: dict[str, set[str]] = {}
    for person_skill in instance.personnel_data.personnel_driving_skills:
        skills_by_person.setdefault(person_skill.person_id, set()).add(
            person_skill.skill_id
        )

    authorized_people = tuple(
        sorted(
            person.person_id
            for person in instance.personnel_data.personnel
            if person.authorized_driver
        )
    )
    return {
        vehicle_id: tuple(
            person_id
            for person_id in authorized_people
            if required <= skills_by_person.get(person_id, set())
        )
        for vehicle_id, required in required_by_vehicle.items()
    }
