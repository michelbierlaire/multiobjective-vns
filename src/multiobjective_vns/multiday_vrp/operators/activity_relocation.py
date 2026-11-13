"""Feature-guided activity relocation for multi-day VRP plans.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from bisect import bisect_right
from functools import cmp_to_key
from typing import Literal

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
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
from multiobjective_vns.multiday_vrp.operators.path_search import (
    PathSearchIndex,
    build_path_search_index,
    rebuild_changed_routes,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    DepotOnlyStop,
    RouteStop,
    TransferOperationStop,
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

type ActivityRelocationMode = Literal[
    'intervention',
    'vehicle_availability',
    'window_guided',
    'service_order_impact',
    'objective_targeted',
]
type _ActivityPosition = tuple[int, int]
type _EvaluatedRelocation = tuple[
    VrpSolution, tuple[object, ...], ApplicationEvaluation
]

__all__ = ['ActivityRelocationMode', 'ActivityRelocationOperator']


class ActivityRelocationOperator:
    """Relocate eligible route stops while preserving their application fields.

    A registration fixes one ``mode`` and, optionally, an objective. Package
    precedence, route capacities, relationships, qualifications, path
    connectivity, and the requested feasibility guarantee are screened by
    complete plan assessment. Availability mode can select any route-stop
    type; the other modes select activity stops.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_mode',
        '_objective_position',
        '_objective_id',
        '_windowed_activity_keys',
        '_intervention_eligible_days',
        '_maximum_size',
        '_candidate_limit',
        '_path_limit',
        '_expansion_limit',
        '_path_search_index',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        mode: ActivityRelocationMode,
        max_neighborhood_size: int,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        if mode not in {
            'intervention',
            'vehicle_availability',
            'window_guided',
            'service_order_impact',
            'objective_targeted',
        }:
            raise ValueError(f'unsupported activity relocation mode: {mode!r}')
        if (
            mode in {'window_guided', 'service_order_impact', 'objective_targeted'}
            and objective_id is None
        ):
            raise ValueError(f'{mode} mode requires objective_id')
        if mode == 'window_guided' and (
            instance.time_window_violation_objective_id is None
            or str(objective_id) != instance.time_window_violation_objective_id
        ):
            raise ValueError(
                'window_guided mode requires the dedicated time-window objective'
            )
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._mode = mode
        self._objective_position = (
            None
            if objective_id is None
            else objective_position(objective_declaration, objective_id)
        )
        self._objective_id = None if objective_id is None else str(objective_id)
        self._windowed_activity_keys = _windowed_activity_keys(instance)
        self._intervention_eligible_days: dict[str, frozenset[int]] = {}
        if mode == 'intervention':
            eligible_days: dict[str, set[int]] = {}
            for window in instance.intervention_data.intervention_time_windows:
                eligible_days.setdefault(window.intervention_id, set()).add(
                    window.day_index
                )
            self._intervention_eligible_days = {
                intervention_id: frozenset(day_indices)
                for intervention_id, day_indices in eligible_days.items()
            }
        self._maximum_size = require_integer(
            max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )
        self._candidate_limit = _limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._path_limit = _limit(max_paths_per_leg, 'max_paths_per_leg', 32)
        self._expansion_limit = _limit(
            max_arc_expansion_steps, 'max_arc_expansion_steps', 10000
        )
        self._path_search_index: PathSearchIndex | None = None

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` eligible stop relocations sequentially."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        if self._mode == 'vehicle_availability':
            return self._availability_result(base, size, context)
        if self._mode == 'service_order_impact':
            return self._service_order_result(base, size, context)
        if self._mode == 'objective_targeted':
            return self._objective_targeted_result(base, size, context)
        working = base
        moved_occurrences: set[str] = set()
        modifications = 0
        working_evaluation: ApplicationEvaluation | None = None
        if self._mode == 'window_guided' and context.base_evaluation is not None:
            stored_evaluation = context.base_evaluation.application_evaluation
            if isinstance(stored_evaluation, ApplicationEvaluation):
                working_evaluation = stored_evaluation
        for _ in range(size):
            if self._mode == 'window_guided':
                analysis = _complete_analysis(working_evaluation)
            else:
                working_evaluation = self._assess_plan(
                    self._instance, self._objectives, working
                )
                if not isinstance(working_evaluation, ApplicationEvaluation):
                    raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
                analysis = _analysis(working_evaluation)
            targets = tuple(
                target
                for target in _eligible_occurrences(
                    self._instance,
                    working,
                    analysis,
                    self._mode,
                    self._objective_id,
                    self._windowed_activity_keys,
                )
                if _occurrence_key(working, target) not in moved_occurrences
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            occurrence_key = _occurrence_key(working, target)
            evaluated_candidates = self._relocation_candidates(working, target, context)
            if not evaluated_candidates:
                moved_occurrences.add(occurrence_key)
                continue
            selected = self._choose(evaluated_candidates, context)
            working, _values, working_evaluation = selected
            moved_occurrences.add(occurrence_key)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _service_order_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        stored_record = context.base_evaluation
        working_evaluation = (
            None if stored_record is None else stored_record.application_evaluation
        )
        if not isinstance(working_evaluation, ApplicationEvaluation):
            working_evaluation = self._assess_plan(
                self._instance, self._objectives, base
            )
        if not isinstance(working_evaluation, ApplicationEvaluation):
            raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')

        working = base
        moved_occurrences: set[str] = set()
        selected_activity_refs: set[tuple[str, str]] = set()
        last_candidates: tuple[_EvaluatedRelocation, ...] = ()
        for _ in range(size):
            targets = tuple(
                target
                for target in _eligible_occurrences(
                    self._instance,
                    working,
                    _analysis(working_evaluation),
                    'service_order_impact',
                    self._objective_id,
                    self._windowed_activity_keys,
                )
                if _occurrence_key(working, target) not in moved_occurrences
            )
            if not targets:
                break

            target = choose_uniformly(targets, context.random_source)
            occurrence_key = _occurrence_key(working, target)
            selected_stop = working.routes[target[0]].stops[target[1]]
            if type(selected_stop) is not ActivityStop:
                raise TypeError('service-order target must be an ActivityStop')
            candidates = self._relocation_candidates(
                working,
                target,
                context,
                max_total_candidates=self._candidate_limit,
            )
            moved_occurrences.add(occurrence_key)
            if not candidates:
                continue

            selected_activity_refs.add(
                (
                    selected_stop.activity_ref.kind,
                    selected_stop.activity_ref.activity_id,
                )
            )
            last_candidates = _order_relocations(
                candidates,
                self._objectives,
                self._objective_position,
                context.random_source,
            )
            working, _values, working_evaluation = last_candidates[0]

        if not last_candidates:
            return OperatorResult(candidates=())
        proposals: list[CandidateProposal] = []
        for solution, _values, _evaluation in last_candidates:
            modifications = _changed_selected_activity_occurrences(
                base, solution, selected_activity_refs
            )
            if modifications == 0:
                continue
            proposals.append(CandidateProposal(Candidate(solution), modifications))
            if len(proposals) >= context.max_candidates:
                break
        return OperatorResult(candidates=tuple(proposals))

    def _objective_targeted_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        working = base
        moved_occurrences: set[str] = set()
        successful_moves = 0
        last_candidates: tuple[_EvaluatedRelocation, ...] = ()

        for _ in range(size):
            targets = tuple(
                target
                for target in _eligible_occurrences(
                    self._instance,
                    working,
                    None,
                    'objective_targeted',
                    self._objective_id,
                    self._windowed_activity_keys,
                )
                if _occurrence_key(working, target) not in moved_occurrences
            )
            if not targets:
                break

            target = choose_uniformly(targets, context.random_source)
            occurrence_key = _occurrence_key(working, target)
            candidates = self._relocation_candidates(
                working,
                target,
                context,
                max_total_candidates=self._candidate_limit,
                one_plan_per_position=True,
            )
            moved_occurrences.add(occurrence_key)
            if not candidates:
                continue

            last_candidates = _order_relocations(
                candidates,
                self._objectives,
                self._objective_position,
                context.random_source,
            )
            working = last_candidates[0][0]
            successful_moves += 1

        if not last_candidates:
            return OperatorResult(candidates=())
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), successful_moves)
                for solution, _values, _evaluation in last_candidates[
                    : context.max_candidates
                ]
            )
        )

    def _availability_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        stored_record = context.base_evaluation
        working_evaluation = (
            None if stored_record is None else stored_record.application_evaluation
        )
        if not isinstance(working_evaluation, ApplicationEvaluation):
            working_evaluation = self._assess_plan(
                self._instance, self._objectives, base
            )
        if not isinstance(working_evaluation, ApplicationEvaluation):
            raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')

        working = base
        moved_occurrences: set[str] = set()
        modifications = 0
        last_candidates: tuple[_EvaluatedRelocation, ...] = ()
        for _ in range(size):
            analysis = _analysis(working_evaluation)
            all_targets = tuple(
                (route_index, stop_index)
                for route_index, route in enumerate(working.routes)
                for stop_index, _stop in enumerate(route.stops)
            )
            objective_id = (
                None
                if self._objective_position is None
                else str(
                    self._objectives.objectives[self._objective_position].objective_id
                )
            )
            route_scores = _availability_route_scores(analysis, objective_id)
            targeted = tuple(
                target
                for target in all_targets
                if (
                    working.routes[target[0]].vehicle_id,
                    working.routes[target[0]].day_index,
                )
                in route_scores
            )
            targets = tuple(
                target
                for target in (targeted or all_targets)
                if _occurrence_key(working, target) not in moved_occurrences
            )
            if not targets:
                break
            route_targets: dict[int, list[_ActivityPosition]] = {}
            for target in targets:
                route_targets.setdefault(target[0], []).append(target)
            if (
                analysis is not None
                and analysis.is_complete
                and objective_id is not None
            ):
                scored_routes = tuple(
                    route_index
                    for route_index in route_targets
                    if (
                        working.routes[route_index].vehicle_id,
                        working.routes[route_index].day_index,
                    )
                    in route_scores
                )
                if scored_routes:
                    largest_score = max(
                        route_scores[
                            (
                                working.routes[route_index].vehicle_id,
                                working.routes[route_index].day_index,
                            )
                        ]
                        for route_index in scored_routes
                    )
                    route_choices = tuple(
                        route_index
                        for route_index in scored_routes
                        if route_scores[
                            (
                                working.routes[route_index].vehicle_id,
                                working.routes[route_index].day_index,
                            )
                        ]
                        == largest_score
                    )
                else:
                    route_choices = tuple(route_targets)
                selected_route = choose_uniformly(route_choices, context.random_source)
                target = choose_uniformly(
                    route_targets[selected_route], context.random_source
                )
            else:
                target = choose_uniformly(targets, context.random_source)
            occurrence_key = _occurrence_key(working, target)
            candidates = self._availability_relocation_candidates(
                working, target, context
            )
            if not candidates:
                moved_occurrences.add(occurrence_key)
                continue
            last_candidates = _order_availability_candidates(
                candidates,
                self._objectives,
                self._objective_position,
                context.random_source,
            )
            working, _values, working_evaluation = last_candidates[0]
            moved_occurrences.add(occurrence_key)
            modifications += 1

        if not modifications:
            return OperatorResult(candidates=())
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), modifications)
                for solution, _values, _evaluation in last_candidates[
                    : context.max_candidates
                ]
            )
        )

    def _availability_relocation_candidates(
        self,
        working: VrpSolution,
        target: _ActivityPosition,
        context: CandidateContext,
    ) -> tuple[_EvaluatedRelocation, ...]:
        source_route_index, source_stop_index = target
        selected_stop = working.routes[source_route_index].stops[source_stop_index]
        positions = randomized_sample(
            _insertion_positions(working, target),
            self._candidate_limit,
            context.random_source,
        )
        candidates: list[_EvaluatedRelocation] = []
        seen: set[VrpSolution] = set()
        for destination_route_index, insertion_index in positions:
            source_stops = list(working.routes[source_route_index].stops)
            del source_stops[source_stop_index]
            if destination_route_index == source_route_index:
                if insertion_index > source_stop_index:
                    insertion_index -= 1
                source_stops.insert(insertion_index, selected_stop)
                stops_by_route = {source_route_index: tuple(source_stops)}
            else:
                destination_stops = list(working.routes[destination_route_index].stops)
                destination_stops.insert(insertion_index, selected_stop)
                stops_by_route = {
                    source_route_index: tuple(source_stops),
                    destination_route_index: tuple(destination_stops),
                }
            rebuilt = rebuild_changed_routes(
                self._instance,
                working,
                stops_by_route,
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                # One path assignment per sampled insertion pair keeps the
                # complete-plan assessment budget bounded by the 32 pairs.
                max_candidate_alternatives=1,
            )
            for candidate in rebuilt:
                if candidate == working or candidate in seen:
                    continue
                seen.add(candidate)
                evaluation = self._assess_plan(
                    self._instance, self._objectives, candidate
                )
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
                    raise ValueError(
                        'objective vector length must match the declaration'
                    )
                if context.ensure_feasible and evaluation.feasible is not True:
                    continue
                candidates.append((candidate, evaluation.objective_values, evaluation))
        return tuple(candidates)

    def _relocation_candidates(
        self,
        working: VrpSolution,
        target: _ActivityPosition,
        context: CandidateContext,
        *,
        max_total_candidates: int | None = None,
        one_plan_per_position: bool = False,
    ) -> tuple[_EvaluatedRelocation, ...]:
        source_route_index, source_stop_index = target
        selected_stop = working.routes[source_route_index].stops[source_stop_index]
        eligible_days = None
        if self._mode == 'intervention':
            if (
                type(selected_stop) is not ActivityStop
                or selected_stop.activity_ref.kind != 'intervention'
            ):
                raise TypeError(
                    'intervention target must be an intervention ActivityStop'
                )
            eligible_days = self._intervention_eligible_days.get(
                selected_stop.activity_ref.activity_id, frozenset()
            )
        positions = _insertion_positions(
            working, target, eligible_day_indices=eligible_days
        )
        sampled = randomized_sample(
            positions, self._candidate_limit, context.random_source
        )
        if not sampled:
            return ()
        if self._path_search_index is None:
            self._path_search_index = build_path_search_index(self._instance)
        candidates: list[_EvaluatedRelocation] = []
        seen_route_keys: set[tuple[tuple[int, ...], tuple[VehicleDayRoute, ...]]] = (
            set()
        )
        alternative_limit = (
            self._candidate_limit
            if max_total_candidates is None
            else min(max_total_candidates, self._candidate_limit)
        )
        inspected_count = 0
        for destination_route_index, insertion_index in sampled:
            remaining = alternative_limit - inspected_count
            if remaining <= 0:
                break
            stops_by_route: dict[int, tuple[RouteStop, ...]] = {}
            source_stops = list(working.routes[source_route_index].stops)
            del source_stops[source_stop_index]
            if destination_route_index == source_route_index:
                if insertion_index > source_stop_index:
                    insertion_index -= 1
                source_stops.insert(insertion_index, selected_stop)
                stops_by_route[source_route_index] = tuple(source_stops)
            else:
                destination_stops = list(working.routes[destination_route_index].stops)
                destination_stops.insert(insertion_index, selected_stop)
                stops_by_route[source_route_index] = tuple(source_stops)
                stops_by_route[destination_route_index] = tuple(destination_stops)
            rebuilt = rebuild_changed_routes(
                self._instance,
                working,
                stops_by_route,
                random_source=context.random_source,
                max_paths_per_leg=self._path_limit,
                max_arc_expansion_steps=self._expansion_limit,
                max_candidate_alternatives=(1 if one_plan_per_position else remaining),
                path_search_index=self._path_search_index,
            )
            changed_route_indices = tuple(sorted(stops_by_route))
            unchanged_route_key = tuple(
                working.routes[index] for index in changed_route_indices
            )
            for candidate in rebuilt:
                if inspected_count >= alternative_limit:
                    break
                inspected_count += 1
                route_key = (
                    changed_route_indices,
                    tuple(candidate.routes[index] for index in changed_route_indices),
                )
                if route_key[1] == unchanged_route_key or route_key in seen_route_keys:
                    continue
                seen_route_keys.add(route_key)
                evaluation = self._assess_plan(
                    self._instance, self._objectives, candidate
                )
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
                    raise ValueError(
                        'objective vector length must match the declaration'
                    )
                if context.ensure_feasible and evaluation.feasible is not True:
                    continue
                candidates.append((candidate, evaluation.objective_values, evaluation))
            if inspected_count >= alternative_limit:
                break
        return tuple(candidates)

    def _choose(
        self,
        candidates: tuple[_EvaluatedRelocation, ...],
        context: CandidateContext,
    ) -> _EvaluatedRelocation:
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


def _eligible_occurrences(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    mode: ActivityRelocationMode,
    objective_id: str | None,
    windowed_activity_keys: frozenset[tuple[str, str, int | None]] | None = None,
) -> tuple[_ActivityPosition, ...]:
    occurrences = tuple(
        (route_index, stop_index)
        for route_index, route in enumerate(solution.routes)
        for stop_index, stop in enumerate(route.stops)
        if (
            type(stop) is ActivityStop
            and (mode != 'intervention' or stop.activity_ref.kind == 'intervention')
        )
    )
    if mode == 'service_order_impact':
        durations = _positive_duration_positions(instance, solution, occurrences)
        if durations and analysis is not None and objective_id is not None:
            scores = _downstream_window_scores(
                solution, analysis, objective_id, durations
            )
            if scores and max(score for score, _position in scores) > 0.0:
                best = max(score for score, _position in scores)
                return tuple(position for score, position in scores if score == best)
        return durations
    if mode == 'window_guided' and analysis is not None and objective_id is not None:
        scored = _window_guidance(solution, analysis, objective_id, occurrences)
        if scored:
            maximum = max(value for value, _position in scored)
            return tuple(position for value, position in scored if value == maximum)
    if mode == 'window_guided':
        window_keys = (
            _windowed_activity_keys(instance)
            if windowed_activity_keys is None
            else windowed_activity_keys
        )
        return tuple(
            position
            for position in occurrences
            if _has_service_window(instance, solution, position, window_keys)
        )
    return occurrences


def _availability_route_scores(
    analysis: VrpOperatorAnalysis | None,
    objective_id: str | None,
) -> dict[tuple[str, int], float]:
    if analysis is None:
        return {}
    route_scores: dict[tuple[str, int], float] = {}
    for finding in analysis.resource_findings:
        if (
            finding.kind != 'vehicle_availability'
            or finding.vehicle_id is None
            or finding.day_index is None
        ):
            continue
        score = finding.measure
        if objective_id is not None:
            score = next(
                (
                    value
                    for candidate_objective, value in finding.penalty_by_objective
                    if candidate_objective == objective_id
                ),
                0.0,
            )
        if score <= 0.0:
            continue
        route_key = (finding.vehicle_id, finding.day_index)
        route_scores[route_key] = max(route_scores.get(route_key, 0.0), score)
    return route_scores


def _order_availability_candidates(
    candidates: tuple[_EvaluatedRelocation, ...],
    objectives: ObjectiveDeclaration,
    objective_index: int | None,
    random_source: RandomSource,
) -> tuple[_EvaluatedRelocation, ...]:
    if objective_index is None:
        return randomized_order(candidates, random_source)

    objective = objectives.objectives[objective_index]

    def compare(left: _EvaluatedRelocation, right: _EvaluatedRelocation) -> int:
        comparison = objective.compare(
            left[1][objective_index], right[1][objective_index]
        )
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    ranked = sorted(candidates, key=cmp_to_key(compare))
    ordered: list[_EvaluatedRelocation] = []
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


def _window_guidance(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    objective_id: str,
    occurrences: tuple[_ActivityPosition, ...],
) -> tuple[tuple[float, _ActivityPosition], ...]:
    occurrence_set = set(occurrences)
    values: dict[_ActivityPosition, float] = {}
    for scenario in analysis.scenario_evaluations:
        traces_by_route = {
            (item.vehicle_id, item.day_index): item for item in scenario.vehicle_days
        }
        for route_index, route in enumerate(solution.routes):
            trace = traces_by_route.get((route.vehicle_id, route.day_index))
            if trace is None:
                continue
            for stop in trace.stops:
                key = (route_index, stop.stop_index)
                if key not in occurrence_set:
                    continue
                guidance = next(
                    (
                        item.guidance_value
                        for item in stop.window_assessments_by_objective
                        if item.objective_id == objective_id
                    ),
                    0.0,
                )
                values[key] = max(values.get(key, 0.0), guidance)
    return tuple((value, key) for key, value in values.items() if value > 0.0)


def _positive_duration_positions(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    occurrences: tuple[_ActivityPosition, ...],
) -> tuple[_ActivityPosition, ...]:
    package_map = {item.package_id: item for item in instance.package_data.packages}
    intervention_map = {
        item.intervention_id: item for item in instance.intervention_data.interventions
    }
    result: list[_ActivityPosition] = []
    for route_index, stop_index in occurrences:
        stop = solution.routes[route_index].stops[stop_index]
        if type(stop) is not ActivityStop:
            continue
        ref = stop.activity_ref
        if ref.kind in {'pickup', 'delivery'}:
            package = package_map.get(ref.activity_id)
            if (
                package is not None
                and (
                    package.pickup_service_duration
                    if ref.kind == 'pickup'
                    else package.delivery_service_duration
                )
                > 0.0
            ):
                result.append((route_index, stop_index))
        elif ref.kind == 'intervention':
            intervention = intervention_map.get(ref.activity_id)
            if intervention is not None and intervention.service_duration > 0.0:
                result.append((route_index, stop_index))
    return tuple(result)


def _has_service_window(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    position: _ActivityPosition,
    windowed_activity_keys: frozenset[tuple[str, str, int | None]] | None = None,
) -> bool:
    route_index, stop_index = position
    stop = solution.routes[route_index].stops[stop_index]
    if type(stop) is not ActivityStop:
        return False
    ref = stop.activity_ref
    keys = (
        _windowed_activity_keys(instance)
        if windowed_activity_keys is None
        else windowed_activity_keys
    )
    if ref.kind in {'pickup', 'delivery'}:
        return (ref.kind, ref.activity_id, None) in keys
    route_day = solution.routes[route_index].day_index
    return ('intervention', ref.activity_id, route_day) in keys


def _windowed_activity_keys(
    instance: LoadedVrpInstance,
) -> frozenset[tuple[str, str, int | None]]:
    return frozenset(
        (
            (item.activity, item.package_id, None)
            for item in instance.package_data.package_time_windows
        )
    ) | frozenset(
        (
            'intervention',
            item.intervention_id,
            item.day_index,
        )
        for item in instance.intervention_data.intervention_time_windows
    )


def _insertion_positions(
    solution: VrpSolution,
    target: _ActivityPosition,
    *,
    eligible_day_indices: frozenset[int] | None = None,
) -> tuple[_ActivityPosition, ...]:
    source_route_index, source_stop_index = target
    return tuple(
        (route_index, insertion_index)
        for route_index, route in enumerate(solution.routes)
        if eligible_day_indices is None or route.day_index in eligible_day_indices
        for insertion_index in range(len(route.stops) + 1)
        if not (
            route_index == source_route_index
            and insertion_index in (source_stop_index, source_stop_index + 1)
        )
    )


def _occurrence_key(solution: VrpSolution, target: _ActivityPosition) -> str:
    route_index, stop_index = target
    stop = solution.routes[route_index].stops[stop_index]
    if type(stop) is ActivityStop:
        return f'activity:{stop.activity_ref.kind}:{stop.activity_ref.activity_id}'
    if type(stop) is TransferOperationStop:
        ref = stop.transfer_step_ref
        return f'transfer:{ref.package_id}:{ref.step_number}:{stop.role}'
    if type(stop) is DepotOnlyStop:
        return (
            f'depot:{stop.node_id}:{stop.wait}:{stop.break_duration}:'
            f'{stop.tour_end}:{route_index}:{stop_index}'
        )
    raise TypeError('route stop has an unsupported type')


def _downstream_window_scores(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    objective_id: str,
    positions: tuple[_ActivityPosition, ...],
) -> tuple[tuple[float, _ActivityPosition], ...]:
    scores = {position: 0.0 for position in positions}
    positions_by_route: list[list[int]] = [[] for _route in solution.routes]
    for route_index, stop_index in positions:
        positions_by_route[route_index].append(stop_index)
    for scenario in analysis.scenario_evaluations:
        traces_by_route = {
            (trace.vehicle_id, trace.day_index): trace
            for trace in scenario.vehicle_days
        }
        for route_index, route_positions in enumerate(positions_by_route):
            if not route_positions:
                continue
            route = solution.routes[route_index]
            trace = traces_by_route.get((route.vehicle_id, route.day_index))
            if trace is None:
                continue
            # Analysis traces preserve route order, so stop indexes are sorted.
            ordered_stops = trace.stops
            stop_indices = [stop.stop_index for stop in ordered_stops]
            cumulative_guidance = [0.0]
            for stop in ordered_stops:
                guidance = next(
                    (
                        item.guidance_value
                        for item in stop.window_assessments_by_objective
                        if item.objective_id == objective_id
                    ),
                    0.0,
                )
                cumulative_guidance.append(cumulative_guidance[-1] + guidance)
            total_guidance = cumulative_guidance[-1]
            for target_stop in route_positions:
                first_downstream = bisect_right(stop_indices, target_stop)
                downstream = total_guidance - cumulative_guidance[first_downstream]
                scores[(route_index, target_stop)] += scenario.probability * downstream
    return tuple((score, position) for position, score in scores.items())


def _order_relocations(
    candidates: tuple[_EvaluatedRelocation, ...],
    objectives: ObjectiveDeclaration,
    objective_index: int | None,
    random_source: RandomSource,
) -> tuple[_EvaluatedRelocation, ...]:
    if objective_index is None:
        return randomized_order(candidates, random_source)

    objective = objectives.objectives[objective_index]

    def compare(left: _EvaluatedRelocation, right: _EvaluatedRelocation) -> int:
        comparison = objective.compare(
            left[1][objective_index], right[1][objective_index]
        )
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    ranked = sorted(candidates, key=cmp_to_key(compare))
    ordered: list[_EvaluatedRelocation] = []
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


def _changed_selected_activity_occurrences(
    original: VrpSolution,
    candidate: VrpSolution,
    selected_refs: set[tuple[str, str]],
) -> int:
    def locations(solution: VrpSolution) -> dict[tuple[str, str], tuple[str, int, int]]:
        return {
            (stop.activity_ref.kind, stop.activity_ref.activity_id): (
                route.vehicle_id,
                route.day_index,
                stop_index,
            )
            for route in solution.routes
            for stop_index, stop in enumerate(route.stops)
            if type(stop) is ActivityStop
        }

    original_locations = locations(original)
    candidate_locations = locations(candidate)
    return sum(
        original_locations.get(activity_ref) != candidate_locations.get(activity_ref)
        for activity_ref in selected_refs
    )


def _analysis(evaluation: ApplicationEvaluation) -> VrpOperatorAnalysis | None:
    if isinstance(evaluation.analysis, VrpOperatorAnalysis):
        return evaluation.analysis
    return None


def _complete_analysis(
    evaluation: ApplicationEvaluation | None,
) -> VrpOperatorAnalysis | None:
    analysis = None if evaluation is None else _analysis(evaluation)
    if analysis is not None and analysis.is_complete:
        return analysis
    return None


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
