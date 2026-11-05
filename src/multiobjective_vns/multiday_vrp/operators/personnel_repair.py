"""Personnel assignment and work-rule repair Operators for the VRP.

Created: 2026-11-04T09:56:38+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.input_records import LoadedVrpInstance
from multiobjective_vns.multiday_vrp.operators.common import (
    VrpPlanAssessor,
    choose_uniformly,
    operator_result,
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    assess_complete_candidate,
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.personnel_features import (
    _eligible_teams,
    _seat_feasible,
)
from multiobjective_vns.multiday_vrp.operators.personnel_repair_support import (
    DutyTarget,
    PersonnelRepairMode,
    RepairTarget,
    qualified_drivers,
    repair_score,
    skill_violations,
)
from multiobjective_vns.multiday_vrp.operators.personnel_repair_targets import (
    select_targets,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
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

    def call(self, context: CandidateContext) -> OperatorResult:
        """Change up to ``k`` person-duty assignments and return a full plan."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        working_evaluation = self._stored_base_evaluation(context, base)
        completed_targets: set[DutyTarget] = set()
        exhausted_targets: set[DutyTarget] = set()
        modifications = 0

        while modifications < size:
            if working_evaluation is None:
                working_evaluation = self._assess(working)
            targets = tuple(
                target
                for target in self._targets(working, working_evaluation)
                if target.duty not in completed_targets
                and target.duty not in exhausted_targets
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            alternatives = self._replacement_alternatives(working, target.duty, context)
            if not alternatives:
                exhausted_targets.add(target.duty)
                continue

            evaluated = self._assess_alternatives(alternatives)
            evaluated = self._keep_improving_alternatives(
                evaluated, target, working, working_evaluation
            )
            if not evaluated:
                exhausted_targets.add(target.duty)
                continue

            selected = self._select_alternative(evaluated, target, context)
            working = selected.solution
            working_evaluation = selected.evaluation
            completed_targets.add(target.duty)
            modifications += 1

        if modifications == 0 or working_evaluation is None:
            return operator_result(None, modifications=0, requested_size=size)
        if context.ensure_feasible and working_evaluation.feasible is not True:
            return operator_result(None, modifications=0, requested_size=size)
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
    ) -> tuple[RepairTarget, ...]:
        return select_targets(
            self._instance, self._mode, solution, evaluation, self._objective_id
        )

    def _replacement_alternatives(
        self,
        solution: VrpSolution,
        duty: DutyTarget,
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        route = solution.routes[duty.route_index]
        replace_team = self._mode != 'skill_guided'
        affected_stops = tuple(
            stop_index
            for stop_index, stop in enumerate(route.stops)
            if replace_team
            and type(stop) is ActivityStop
            and duty.person_id in stop.team_member_ids
        )

        if duty.role == 'driver':
            driver_options = tuple(
                person_id
                for person_id in qualified_drivers(
                    self._instance, solution, duty.route_index
                )
                if person_id != duty.person_id
            )
        else:
            driver_options = (route.driver_id,)
        if not driver_options:
            return ()

        team_domains: list[tuple[int, tuple[frozenset[str], ...]]] = []
        for stop_index in affected_stops:
            stop = route.stops[stop_index]
            assert type(stop) is ActivityStop
            teams = _eligible_teams(
                self._instance,
                stop.activity_ref.activity_id,
                random_source=context.random_source,
                max_samples=max(64, self._candidate_limit * 16),
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
        attempt_limit = max(self._candidate_limit * 32, self._candidate_limit)
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
            result.append(candidate)
            if len(result) >= self._candidate_limit:
                break
        return tuple(result)

    def _assess_alternatives(
        self, alternatives: tuple[VrpSolution, ...]
    ) -> tuple[_Alternative, ...]:
        result: list[_Alternative] = []
        for candidate in alternatives:
            evaluation = self._assess(candidate)
            if evaluation is not None:
                result.append(_Alternative(candidate, evaluation))
        return tuple(result)

    def _keep_improving_alternatives(
        self,
        alternatives: tuple[_Alternative, ...],
        target: RepairTarget,
        current_solution: VrpSolution,
        current: ApplicationEvaluation | None,
    ) -> tuple[_Alternative, ...]:
        if not alternatives:
            return ()
        if self._mode == 'availability':
            return alternatives
        if self._mode == 'work_rule' and target.finding is None:
            return alternatives
        current_score = repair_score(
            self._instance, self._mode, current_solution, current, target
        )
        scored = tuple(
            (
                item,
                repair_score(
                    self._instance, self._mode, item.solution, item.evaluation, target
                ),
            )
            for item in alternatives
        )
        best = min(score for _, score in scored)
        if best < current_score:
            return tuple(item for item, score in scored if score == best)
        if self._mode == 'skill_guided' and not skill_violations(
            self._instance, current_solution
        ):
            return alternatives
        return ()

    def _select_alternative(
        self,
        alternatives: tuple[_Alternative, ...],
        target: RepairTarget,
        context: CandidateContext,
    ) -> _Alternative:
        ranked = alternatives
        if context.ensure_feasible:
            feasible = tuple(
                item for item in ranked if item.evaluation.feasible is True
            )
            if feasible:
                ranked = feasible
        if self._mode in {'skill_guided', 'staff_continuity', 'work_rule'}:
            scores = tuple(
                repair_score(
                    self._instance, self._mode, item.solution, item.evaluation, target
                )
                for item in ranked
            )
            best_score = min(scores)
            ranked = tuple(
                item
                for item, score in zip(ranked, scores, strict=True)
                if score == best_score
            )
        if self._objective_position is not None and len(ranked) > 1:
            ranked = self._best_by_objective(ranked)
        return choose_uniformly(ranked, context.random_source)

    def _best_by_objective(
        self, alternatives: tuple[_Alternative, ...]
    ) -> tuple[_Alternative, ...]:
        assert self._objective_position is not None
        objective = self._objectives.objectives[self._objective_position]
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
