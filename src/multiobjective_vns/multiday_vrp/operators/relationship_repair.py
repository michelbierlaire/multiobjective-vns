"""Repair precedence and co-location relationships between services.

Created: 2026-11-04T09:43:07+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.helpers import _greater
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import VrpScenarioEvaluation
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
from multiobjective_vns.multiday_vrp.operators.path_search import (
    PathSearchIndex,
    build_path_search_index,
    rebuild_changed_routes,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityKind,
    ActivityRef,
    ActivityStop,
    RouteStop,
    TransferOperationStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveComparison, ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import require_integer

__all__ = ['RelationshipGuidedServiceRepairOperator']

type _Position = tuple[int, int]
type _TargetKey = tuple[str, str, str, str, str]
type _ScenarioTimes = tuple[dict[ActivityRef, tuple[float, float]], ...]
type _ScoredCandidate = tuple[VrpSolution, tuple[object, ...], ApplicationEvaluation]


@dataclass(frozen=True, slots=True)
class _RelationshipTarget:
    key: _TargetKey
    kind: str
    first: ActivityRef
    second: ActivityRef


class RelationshipGuidedServiceRepairOperator:
    """Repair one or more declared precedence or co-location relationships.

    Precedence repair inserts a missing prerequisite or relocates it. Optional
    service coverage permits resolving a fixed-node co-location mismatch by
    omitting either member. Complete candidate plans receive application
    assessment before they can be selected.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_position',
        '_maximum_size',
        '_candidate_limit',
        '_path_limit',
        '_expansion_limit',
        '_vehicle_package_compatibility',
        '_intervention_eligible_days',
        '_activity_nodes',
        '_interventions',
        '_people',
        '_required_qualifications',
        '_person_qualifications',
        '_path_search_index',
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
        self._candidate_limit = _bounded_limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _bounded_limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _bounded_limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )
        self._vehicle_package_compatibility = {
            (item.vehicle_id, item.package_id): item.compatible
            for item in instance.package_data.vehicle_package_compatibility
        }
        eligible_days: dict[str, set[int]] = {}
        for window in instance.intervention_data.intervention_time_windows:
            eligible_days.setdefault(window.intervention_id, set()).add(
                window.day_index
            )
        self._intervention_eligible_days = {
            intervention_id: frozenset(day_indices)
            for intervention_id, day_indices in eligible_days.items()
        }
        self._activity_nodes = {
            node_ref: node_id
            for package in instance.package_data.packages
            for node_ref, node_id in (
                (ActivityRef('pickup', package.package_id), package.pickup_node_id),
                (
                    ActivityRef('delivery', package.package_id),
                    package.delivery_node_id,
                ),
            )
        }
        self._activity_nodes.update(
            {
                ActivityRef('intervention', item.intervention_id): item.node_id
                for item in instance.intervention_data.interventions
            }
        )
        self._interventions = {
            intervention.intervention_id: intervention
            for intervention in instance.intervention_data.interventions
        }
        self._people = tuple(
            sorted(person.person_id for person in instance.personnel_data.personnel)
        )
        required_qualifications: dict[str, set[str]] = {}
        for (
            requirement
        ) in instance.intervention_data.intervention_qualifications_required:
            required_qualifications.setdefault(requirement.intervention_id, set()).add(
                requirement.qualification_id
            )
        self._required_qualifications = {
            intervention_id: frozenset(qualification_ids)
            for intervention_id, qualification_ids in required_qualifications.items()
        }
        person_qualifications: dict[str, set[str]] = {}
        for (
            qualification
        ) in instance.personnel_data.personnel_intervention_qualifications:
            person_qualifications.setdefault(qualification.person_id, set()).add(
                qualification.qualification_id
            )
        self._person_qualifications = {
            person_id: frozenset(qualification_ids)
            for person_id, qualification_ids in person_qualifications.items()
        }
        self._path_search_index: PathSearchIndex = build_path_search_index(instance)

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` distinct relationship repairs sequentially."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        stored_record = context.base_evaluation
        working_evaluation = (
            None if stored_record is None else stored_record.application_evaluation
        )
        if not isinstance(working_evaluation, ApplicationEvaluation):
            working_evaluation = _assessment(
                self._assess_plan, self._instance, self._objectives, working
            )
        considered: set[_TargetKey] = set()
        selected_targets: list[_RelationshipTarget] = []

        for _ in range(size):
            targets = tuple(
                target
                for target in self._targets(working, self._analysis(working_evaluation))
                if target.key not in considered
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            considered.add(target.key)
            selected_targets.append(target)
            candidates = self._repair_candidates(working, target, context)
            if candidates:
                working, _values, working_evaluation = self._choose(candidates, context)

        modifications = sum(
            _pair_changed(base, working, target) for target in selected_targets
        )
        if modifications == 0:
            return operator_result(None, modifications=0, requested_size=size)

        final_evaluation = working_evaluation
        if not final_evaluation.representation_valid:
            return operator_result(None, modifications=0, requested_size=size)
        _objective_values(final_evaluation, len(self._objectives.objectives))
        if context.ensure_feasible and final_evaluation.feasible is not True:
            return operator_result(None, modifications=0, requested_size=size)
        return operator_result(
            working, modifications=modifications, requested_size=size
        )

    def _analysis(
        self, evaluation: ApplicationEvaluation
    ) -> VrpOperatorAnalysis | None:
        return (
            evaluation.analysis
            if isinstance(evaluation.analysis, VrpOperatorAnalysis)
            else None
        )

    def _targets(
        self,
        solution: VrpSolution,
        analysis: VrpOperatorAnalysis | None,
    ) -> tuple[_RelationshipTarget, ...]:
        locations = _activity_locations(solution)
        scenario_times = _scenario_service_times_by_scenario(solution, analysis)
        targets: list[_RelationshipTarget] = []
        for precedence in self._instance.activity_relations.precedence:
            before = _reference(
                precedence.before_activity_kind, precedence.before_activity_id
            )
            after = _reference(
                precedence.after_activity_kind, precedence.after_activity_id
            )
            if after not in locations:
                continue
            if before not in locations or not _precedence_satisfied(
                scenario_times, before, after
            ):
                targets.append(
                    _RelationshipTarget(
                        (
                            'precedence',
                            before.kind,
                            before.activity_id,
                            after.kind,
                            after.activity_id,
                        ),
                        'precedence',
                        before,
                        after,
                    )
                )

        if self._instance.settings.service_coverage_mode == 'penalized_omission':
            for co_location in self._instance.activity_relations.co_location:
                first = _reference(
                    co_location.activity_kind_1, co_location.activity_id_1
                )
                second = _reference(
                    co_location.activity_kind_2, co_location.activity_id_2
                )
                first_node = self._activity_nodes[first]
                second_node = self._activity_nodes[second]
                if first_node != second_node and (
                    first in locations or second in locations
                ):
                    targets.append(
                        _RelationshipTarget(
                            (
                                'co_location',
                                first.kind,
                                first.activity_id,
                                second.kind,
                                second.activity_id,
                            ),
                            'co_location',
                            first,
                            second,
                        )
                    )
        return tuple(targets)

    def _repair_candidates(
        self,
        solution: VrpSolution,
        target: _RelationshipTarget,
        context: CandidateContext,
    ) -> tuple[_ScoredCandidate, ...]:
        if target.kind == 'precedence':
            return self._precedence_candidates(solution, target, context)
        if target.kind == 'co_location':
            return self._co_location_candidates(solution, target, context)
        raise ValueError(f'unknown relationship target kind: {target.kind!r}')

    def _precedence_candidates(
        self,
        solution: VrpSolution,
        target: _RelationshipTarget,
        context: CandidateContext,
    ) -> tuple[_ScoredCandidate, ...]:
        locations = _activity_locations(solution)
        source_position = locations.get(target.first)
        if source_position is None:
            stop_variants = self._new_activity_stops(
                target.first, context, self._candidate_limit
            )
        else:
            source_route, source_stop = source_position
            source_value = solution.routes[source_route].stops[source_stop]
            if type(source_value) is not ActivityStop:
                return ()
            stop_variants = (source_value,)
        if not stop_variants:
            return ()

        partial = [list(route.stops) for route in solution.routes]
        if source_position is not None:
            del partial[source_position[0]][source_position[1]]
        positions = self._eligible_positions(solution, partial, target.first)
        after_position = locations.get(target.second)
        if after_position is not None:
            after_route, after_stop = after_position
            if source_position is not None and source_position[0] == after_route:
                if source_position[1] < after_stop:
                    after_stop -= 1
            positions = tuple(
                position
                for position in positions
                if position[0] != after_route or position[1] <= after_stop
            )
        if source_position is not None:
            positions = tuple(
                position for position in positions if position != source_position
            )
        move_options = tuple(
            (candidate_stop, position)
            for candidate_stop in stop_variants
            for position in positions
        )
        sampled = randomized_sample(
            move_options, self._candidate_limit, context.random_source
        )
        alternatives: list[_ScoredCandidate] = []
        for selected_stop, (route_index, stop_index) in sampled:
            trial = [list(items) for items in partial]
            trial[route_index].insert(stop_index, selected_stop)
            alternatives.extend(
                self._rebuild_and_assess(
                    solution,
                    trial,
                    target,
                    context,
                )
            )
        return tuple(alternatives)

    def _eligible_positions(
        self,
        solution: VrpSolution,
        stops_by_route: list[list[RouteStop]],
        reference: ActivityRef,
    ) -> tuple[_Position, ...]:
        allowed_days = self._intervention_eligible_days.get(
            reference.activity_id, frozenset()
        )
        return tuple(
            (route_index, stop_index)
            for route_index, route in enumerate(solution.routes)
            if (
                reference.kind not in {'pickup', 'delivery'}
                or self._vehicle_package_compatibility.get(
                    (route.vehicle_id, reference.activity_id), True
                )
            )
            and (
                reference.kind != 'intervention'
                or not allowed_days
                or route.day_index in allowed_days
            )
            for stop_index in range(len(stops_by_route[route_index]) + 1)
        )

    def _new_activity_stops(
        self,
        reference: ActivityRef,
        context: CandidateContext,
        limit: int,
    ) -> tuple[ActivityStop, ...]:
        if reference.kind != 'intervention':
            return (ActivityStop(reference, frozenset(), 0.0, 0.0, 0.0, False),)
        intervention = self._interventions.get(reference.activity_id)
        if intervention is None or intervention.minimum_team_size > len(self._people):
            return ()
        required = self._required_qualifications.get(reference.activity_id, frozenset())
        sizes = tuple(range(intervention.minimum_team_size, len(self._people) + 1))
        teams: list[frozenset[str]] = []
        seen: set[frozenset[str]] = set()
        attempts = max(limit * 64, 64)
        for _ in range(attempts):
            size = choose_uniformly(sizes, context.random_source)
            team = frozenset(
                randomized_sample(self._people, size, context.random_source)
            )
            if team in seen:
                continue
            seen.add(team)
            coverage = set().union(
                *(
                    self._person_qualifications.get(person, frozenset())
                    for person in team
                )
            )
            if required <= coverage:
                teams.append(team)
                if len(teams) >= limit:
                    break
        return tuple(
            ActivityStop(reference, team, 0.0, 0.0, 0.0, False) for team in teams
        )

    def _co_location_candidates(
        self,
        solution: VrpSolution,
        target: _RelationshipTarget,
        context: CandidateContext,
    ) -> tuple[_ScoredCandidate, ...]:
        candidates: list[_ScoredCandidate] = []
        removal_order = randomized_sample(
            (target.first, target.second), 2, context.random_source
        )
        for option_index, reference in enumerate(removal_order):
            remaining_options = len(removal_order) - option_index
            remaining_budget = self._candidate_limit - len(candidates)
            if remaining_budget <= 0:
                break
            option_budget = (
                remaining_budget + remaining_options - 1
            ) // remaining_options
            locations = _activity_locations(solution)
            if reference not in locations:
                continue
            partial = [list(route.stops) for route in solution.routes]
            transfer_steps = solution.transfer_steps
            removed_ids = {reference}
            package_id = reference.activity_id if reference.kind == 'pickup' else None
            if package_id is not None:
                removed_ids = {
                    ActivityRef('pickup', package_id),
                    ActivityRef('delivery', package_id),
                }
                transfer_steps = tuple(
                    item for item in transfer_steps if item.package_id != package_id
                )
            changed: dict[int, tuple[RouteStop, ...]] = {}
            for route_index, route_stops in enumerate(partial):
                route_stops[:] = [
                    stop
                    for stop in route_stops
                    if not (
                        type(stop) is ActivityStop and stop.activity_ref in removed_ids
                    )
                    and not (
                        package_id is not None
                        and type(stop) is TransferOperationStop
                        and stop.transfer_step_ref.package_id == package_id
                    )
                ]
                if tuple(route_stops) != solution.routes[route_index].stops:
                    changed[route_index] = tuple(route_stops)
            rebuilt = rebuild_changed_routes(
                self._instance,
                solution,
                changed,
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=option_budget,
                path_search_index=self._path_search_index,
            )
            for candidate in rebuilt:
                if transfer_steps != candidate.transfer_steps:
                    candidate = replace(candidate, transfer_steps=transfer_steps)
                if not _pair_changed(solution, candidate, target):
                    continue
                evaluation = _assessment(
                    self._assess_plan, self._instance, self._objectives, candidate
                )
                if not evaluation.representation_valid:
                    continue
                if context.ensure_feasible and evaluation.feasible is not True:
                    continue
                values = _objective_values(evaluation, len(self._objectives.objectives))
                candidates.append((candidate, values, evaluation))
        return tuple(candidates)

    def _rebuild_and_assess(
        self,
        solution: VrpSolution,
        trial_stops: list[list[RouteStop]],
        target: _RelationshipTarget,
        context: CandidateContext,
    ) -> tuple[_ScoredCandidate, ...]:
        changed = {
            index: tuple(stops)
            for index, stops in enumerate(trial_stops)
            if tuple(stops) != solution.routes[index].stops
        }
        rebuilt = rebuild_changed_routes(
            self._instance,
            solution,
            changed,
            random_source=context.random_source,
            max_paths_per_leg=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            max_candidate_alternatives=1,
            path_search_index=self._path_search_index,
        )
        alternatives: list[_ScoredCandidate] = []
        for candidate in rebuilt:
            if not _pair_changed(solution, candidate, target):
                continue
            evaluation = _assessment(
                self._assess_plan, self._instance, self._objectives, candidate
            )
            if not evaluation.representation_valid:
                continue
            if context.ensure_feasible and evaluation.feasible is not True:
                continue
            analysis = (
                evaluation.analysis
                if isinstance(evaluation.analysis, VrpOperatorAnalysis)
                else None
            )
            if not _precedence_satisfied(
                _scenario_service_times_by_scenario(candidate, analysis),
                target.first,
                target.second,
            ):
                continue
            values = _objective_values(evaluation, len(self._objectives.objectives))
            alternatives.append((candidate, values, evaluation))
        return tuple(alternatives)

    def _choose(
        self,
        candidates: tuple[_ScoredCandidate, ...],
        context: CandidateContext,
    ) -> _ScoredCandidate:
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


def _precedence_satisfied(
    scenario_times: _ScenarioTimes | None,
    before: ActivityRef,
    after: ActivityRef,
) -> bool:
    if not scenario_times:
        return False
    for times in scenario_times:
        before_times = times.get(before)
        after_times = times.get(after)
        if before_times is None or after_times is None:
            return False
        before_finish, _ = before_times
        _, after_start = after_times
        if _greater(before_finish, after_start):
            return False
    return True


def _scenario_service_times_by_scenario(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
) -> _ScenarioTimes | None:
    if (
        analysis is None
        or not analysis.is_complete
        or not analysis.scenario_evaluations
    ):
        return None
    return tuple(
        _scenario_service_times(solution, scenario)
        for scenario in analysis.scenario_evaluations
    )


def _scenario_service_times(
    solution: VrpSolution, scenario: VrpScenarioEvaluation
) -> dict[ActivityRef, tuple[float, float]]:
    result: dict[ActivityRef, tuple[float, float]] = {}
    traces = {
        (item.vehicle_id, item.day_index): {
            stop.stop_index: stop for stop in item.stops
        }
        for item in scenario.vehicle_days
    }
    for route in solution.routes:
        trace = traces.get((route.vehicle_id, route.day_index))
        if trace is None:
            continue
        for index, stop in enumerate(route.stops):
            if type(stop) is not ActivityStop:
                continue
            evaluated = trace.get(index)
            if (
                evaluated is not None
                and evaluated.activity_ref == stop.activity_ref
                and evaluated.service_start is not None
                and evaluated.service_finish is not None
            ):
                result[stop.activity_ref] = (
                    evaluated.service_finish,
                    evaluated.service_start,
                )
    return result


def _activity_locations(solution: VrpSolution) -> dict[ActivityRef, _Position]:
    return {
        stop.activity_ref: (route_index, stop_index)
        for route_index, route in enumerate(solution.routes)
        for stop_index, stop in enumerate(route.stops)
        if type(stop) is ActivityStop
    }


def _reference(kind: str, activity_id: str) -> ActivityRef:
    return ActivityRef(cast(ActivityKind, kind), activity_id)


def _pair_changed(
    before: VrpSolution, after: VrpSolution, target: _RelationshipTarget
) -> bool:
    before_positions = _activity_locations(before)
    after_positions = _activity_locations(after)
    refs = (target.first, target.second)
    if any(before_positions.get(ref) != after_positions.get(ref) for ref in refs):
        return True
    if target.kind == 'co_location':
        package_ids = {
            ref.activity_id for ref in refs if ref.kind in {'pickup', 'delivery'}
        }
        before_steps = tuple(
            item for item in before.transfer_steps if item.package_id in package_ids
        )
        after_steps = tuple(
            item for item in after.transfer_steps if item.package_id in package_ids
        )
        return before_steps != after_steps
    return False


def _assessment(
    assess_plan: VrpPlanAssessor,
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    solution: VrpSolution,
) -> ApplicationEvaluation:
    evaluation = assess_plan(instance, objectives, solution)
    if not isinstance(evaluation, ApplicationEvaluation):
        raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
    return evaluation


def _objective_values(
    evaluation: ApplicationEvaluation, objective_count: int
) -> tuple[object, ...]:
    values = evaluation.objective_values
    if type(values) is not tuple:
        raise TypeError(
            'representation-valid VRP evaluations require an objective tuple'
        )
    if len(values) != objective_count:
        raise ValueError('objective vector length must match the declaration')
    return values


def _bounded_limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
