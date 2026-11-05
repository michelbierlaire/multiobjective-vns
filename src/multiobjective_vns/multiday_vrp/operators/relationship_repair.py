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

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` distinct relationship repairs sequentially."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        considered: set[_TargetKey] = set()
        selected_targets: list[_RelationshipTarget] = []

        for _ in range(size):
            targets = tuple(
                target
                for target in self._targets(working, self._analysis(working))
                if target.key not in considered
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            considered.add(target.key)
            selected_targets.append(target)
            candidates = self._repair_candidates(working, target, context)
            if candidates:
                working = self._choose(candidates, context)

        modifications = sum(
            _pair_changed(base, working, target) for target in selected_targets
        )
        if modifications == 0:
            return operator_result(None, modifications=0, requested_size=size)

        final_evaluation = _assessment(
            self._assess_plan, self._instance, self._objectives, working
        )
        if not final_evaluation.representation_valid:
            return operator_result(None, modifications=0, requested_size=size)
        _objective_values(final_evaluation, len(self._objectives.objectives))
        if context.ensure_feasible and final_evaluation.feasible is not True:
            return operator_result(None, modifications=0, requested_size=size)
        return operator_result(
            working, modifications=modifications, requested_size=size
        )

    def _analysis(self, solution: VrpSolution) -> VrpOperatorAnalysis | None:
        evaluation = _assessment(
            self._assess_plan, self._instance, self._objectives, solution
        )
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
                solution, analysis, before, after
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
                first_node = _activity_node(self._instance, first)
                second_node = _activity_node(self._instance, second)
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
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
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
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
        locations = _activity_locations(solution)
        source_position = locations.get(target.first)
        if source_position is None:
            stop_variants = _new_activity_stops(
                self._instance,
                target.first,
                context,
                self._candidate_limit,
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
        positions = _eligible_positions(self._instance, solution, partial, target.first)
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
        alternatives: list[tuple[VrpSolution, tuple[object, ...]]] = []
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

    def _co_location_candidates(
        self,
        solution: VrpSolution,
        target: _RelationshipTarget,
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
        candidates: list[tuple[VrpSolution, tuple[object, ...]]] = []
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
            if reference.kind == 'pickup':
                removed_ids.add(ActivityRef('delivery', reference.activity_id))
                transfer_steps = tuple(
                    item
                    for item in transfer_steps
                    if item.package_id != reference.activity_id
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
                        reference.kind == 'pickup'
                        and type(stop) is TransferOperationStop
                        and stop.transfer_step_ref.package_id == reference.activity_id
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
                values = _objective_values(evaluation, len(self._objectives.objectives))
                candidates.append((candidate, values))
        return tuple(candidates)

    def _rebuild_and_assess(
        self,
        solution: VrpSolution,
        trial_stops: list[list[RouteStop]],
        target: _RelationshipTarget,
        context: CandidateContext,
    ) -> tuple[tuple[VrpSolution, tuple[object, ...]], ...]:
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
        )
        alternatives: list[tuple[VrpSolution, tuple[object, ...]]] = []
        for candidate in rebuilt:
            if not _pair_changed(solution, candidate, target):
                continue
            evaluation = _assessment(
                self._assess_plan, self._instance, self._objectives, candidate
            )
            if not evaluation.representation_valid:
                continue
            values = _objective_values(evaluation, len(self._objectives.objectives))
            alternatives.append((candidate, values))
        return tuple(alternatives)

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


def _precedence_satisfied(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    before: ActivityRef,
    after: ActivityRef,
) -> bool:
    if analysis is None or not analysis.scenario_evaluations:
        return False
    if not analysis.is_complete:
        return False
    for scenario in analysis.scenario_evaluations:
        times = _scenario_service_times(solution, scenario)
        before_times = times.get(before)
        after_times = times.get(after)
        if before_times is None or after_times is None:
            return False
        before_finish, _ = before_times
        _, after_start = after_times
        if _greater(before_finish, after_start):
            return False
    return True


def _scenario_service_times(
    solution: VrpSolution, scenario: VrpScenarioEvaluation
) -> dict[ActivityRef, tuple[float, float]]:
    result: dict[ActivityRef, tuple[float, float]] = {}
    traces = {(item.vehicle_id, item.day_index): item for item in scenario.vehicle_days}
    for route in solution.routes:
        trace = traces.get((route.vehicle_id, route.day_index))
        if trace is None:
            continue
        for index, stop in enumerate(route.stops):
            if type(stop) is not ActivityStop:
                continue
            evaluated = next(
                (item for item in trace.stops if item.stop_index == index), None
            )
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


def _eligible_positions(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    stops_by_route: list[list[RouteStop]],
    reference: ActivityRef,
) -> tuple[_Position, ...]:
    compatibility = {
        (item.vehicle_id, item.package_id): item.compatible
        for item in instance.package_data.vehicle_package_compatibility
    }
    allowed_intervention_days = {
        item.day_index
        for item in instance.intervention_data.intervention_time_windows
        if item.intervention_id == reference.activity_id
    }
    return tuple(
        (route_index, stop_index)
        for route_index, route in enumerate(solution.routes)
        if (
            reference.kind not in {'pickup', 'delivery'}
            or compatibility.get((route.vehicle_id, reference.activity_id), True)
        )
        and (
            reference.kind != 'intervention'
            or not allowed_intervention_days
            or route.day_index in allowed_intervention_days
        )
        for stop_index in range(len(stops_by_route[route_index]) + 1)
    )


def _new_activity_stops(
    instance: LoadedVrpInstance,
    reference: ActivityRef,
    context: CandidateContext,
    limit: int,
) -> tuple[ActivityStop, ...]:
    if reference.kind != 'intervention':
        return (ActivityStop(reference, frozenset(), 0.0, 0.0, 0.0, False),)
    intervention = next(
        (
            item
            for item in instance.intervention_data.interventions
            if item.intervention_id == reference.activity_id
        ),
        None,
    )
    if intervention is None:
        return ()
    people = tuple(sorted(item.person_id for item in instance.personnel_data.personnel))
    if intervention.minimum_team_size > len(people):
        return ()
    required = {
        item.qualification_id
        for item in instance.intervention_data.intervention_qualifications_required
        if item.intervention_id == reference.activity_id
    }
    qualifications: dict[str, set[str]] = {}
    for item in instance.personnel_data.personnel_intervention_qualifications:
        qualifications.setdefault(item.person_id, set()).add(item.qualification_id)
    sizes = tuple(range(intervention.minimum_team_size, len(people) + 1))
    teams: list[frozenset[str]] = []
    seen: set[frozenset[str]] = set()
    attempts = max(limit * 64, 64)
    for _ in range(attempts):
        size = choose_uniformly(sizes, context.random_source)
        team = frozenset(randomized_sample(people, size, context.random_source))
        if team in seen:
            continue
        seen.add(team)
        coverage = set().union(*(qualifications.get(person, set()) for person in team))
        if required <= coverage:
            teams.append(team)
            if len(teams) >= limit:
                break
    return tuple(ActivityStop(reference, team, 0.0, 0.0, 0.0, False) for team in teams)


def _activity_locations(solution: VrpSolution) -> dict[ActivityRef, _Position]:
    return {
        stop.activity_ref: (route_index, stop_index)
        for route_index, route in enumerate(solution.routes)
        for stop_index, stop in enumerate(route.stops)
        if type(stop) is ActivityStop
    }


def _activity_node(instance: LoadedVrpInstance, reference: ActivityRef) -> str:
    if reference.kind in {'pickup', 'delivery'}:
        package = next(
            item
            for item in instance.package_data.packages
            if item.package_id == reference.activity_id
        )
        return (
            package.pickup_node_id
            if reference.kind == 'pickup'
            else package.delivery_node_id
        )
    intervention = next(
        item
        for item in instance.intervention_data.interventions
        if item.intervention_id == reference.activity_id
    )
    return intervention.node_id


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
