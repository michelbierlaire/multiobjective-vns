"""Feature-guided timing adjustment Operators for the multi-day VRP.

Created: 2026-11-03T22:39:28+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import replace
from functools import cmp_to_key
from math import isclose, isfinite
from typing import Literal, cast

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.records import (
    ResourceFinding,
    VrpOperatorAnalysis,
)
from multiobjective_vns.multiday_vrp.extensions import (
    StopScenarioEvaluation,
    VehicleDayScenarioEvaluation,
)
from multiobjective_vns.multiday_vrp.input_records import (
    InterventionRecord,
    LoadedScenarioBank,
    LoadedVrpInstance,
    PackageRecord,
)
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
from multiobjective_vns.multiday_vrp.operators.personnel_repair_support import (
    _person_from_resource_id,
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
from multiobjective_vns.values import finite_real, require_integer

type FeatureTimingMode = Literal[
    'departure_profile',
    'scenario_differential',
    'vehicle_availability',
    'personnel_availability',
    'service_window_wait',
    'route_start_window',
    'positive_service_duration',
    'personnel_calendar',
    'work_rule',
]
type _TimingTarget = tuple[int, int | None, str]
type _WorkRuleTrial = tuple[
    VrpSolution,
    ApplicationEvaluation,
    tuple[_TimingTarget, ...],
    tuple[_TimingTarget, ...],
]
type _RouteProfileValues = tuple[tuple[tuple[float, ...], ...], ...]
type _ScenarioObservations = tuple[tuple[float, ...], ...]
type _ActivityWindowIndex = dict[
    tuple[str, str, int], tuple[tuple[float, float, float], ...]
]
type _ProfileOption = tuple[
    VrpSolution, _RouteProfileValues, ApplicationEvaluation | None
]

__all__ = ['FeatureTimingMode', 'FeatureTimingOperator']

_VALID_MODES = {
    'departure_profile',
    'scenario_differential',
    'vehicle_availability',
    'personnel_availability',
    'service_window_wait',
    'route_start_window',
    'positive_service_duration',
    'personnel_calendar',
    'work_rule',
}
_STOP_TYPES = (ActivityStop, TransferOperationStop, DepotOnlyStop)
_WAIT_FIELDS = ('pre_service_wait', 'wait')


class FeatureTimingOperator:
    """Adjust timing decisions selected by one active instance feature.

    The same implementation serves the compatible Operator modes named in
    ``FeatureTimingMode``. A catalog registration fixes one mode and its
    objective, time step, and maximum size.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_mode',
        '_objective_position',
        '_time_step',
        '_maximum_size',
        '_candidate_limit',
        '_activity_window_index',
        '_restricted_personnel_ids',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        mode: FeatureTimingMode,
        objective_id: ObjectiveId,
        time_step: float,
        max_neighborhood_size: int,
        max_candidate_alternatives: int = 32,
    ) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f'unsupported feature timing mode: {mode!r}')
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._mode = mode
        self._objective_position = objective_position(
            objective_declaration, objective_id
        )
        self._time_step = finite_real(time_step, name='time_step')
        if self._time_step <= 0.0:
            raise ValueError('time_step must be positive')
        self._maximum_size = require_integer(
            max_neighborhood_size, name='max_neighborhood_size', minimum=1
        )
        self._candidate_limit = _limit(
            max_candidate_alternatives, 'max_candidate_alternatives', 32
        )
        self._activity_window_index = (
            _index_activity_windows(instance)
            if mode in {'service_window_wait', 'route_start_window'}
            else None
        )
        self._restricted_personnel_ids = (
            _restricted_calendar_personnel(instance)
            if mode == 'personnel_calendar'
            else frozenset()
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Change up to ``k`` distinct timing fields and rank full plans."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        if self._mode == 'vehicle_availability':
            return self._availability_result(base, size, context)
        if self._mode == 'personnel_calendar':
            return self._personnel_calendar_result(base, size, context)
        if self._mode == 'departure_profile':
            return _departure_profile_result(
                self._instance,
                self._objectives,
                self._assess_plan,
                base,
                size,
                self._objective_position,
                self._time_step,
                self._candidate_limit,
                context,
            )
        if self._mode == 'scenario_differential':
            return _scenario_differential_result(
                self._instance,
                self._objectives,
                self._assess_plan,
                base,
                size,
                self._objective_position,
                self._time_step,
                self._candidate_limit,
                context,
            )
        if self._mode in {'service_window_wait', 'route_start_window'}:
            return self._window_timing_result(base, size, context)
        if self._mode == 'work_rule':
            return self._work_rule_result(base, size, context)
        working = base
        modified_targets: set[_TimingTarget] = set()
        modifications = 0
        for _ in range(size):
            evaluation = self._assess_plan(self._instance, self._objectives, working)
            if not isinstance(evaluation, ApplicationEvaluation):
                raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
            analysis = _analysis(evaluation)
            targets = tuple(
                target
                for target in _targets(
                    self._instance,
                    working,
                    analysis,
                    self._mode,
                    str(
                        self._objectives.objectives[
                            self._objective_position
                        ].objective_id
                    ),
                )
                if target not in modified_targets
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            alternatives = _candidate_values(
                self._instance,
                working,
                analysis,
                target,
                self._mode,
                self._time_step,
                context.random_source,
            )
            candidates: list[tuple[VrpSolution, object]] = []
            for value in randomized_sample(
                alternatives, self._candidate_limit, context.random_source
            ):
                candidate = _set_timing_value(working, target, value)
                if candidate is None or candidate == working:
                    continue
                result = self._assess_plan(self._instance, self._objectives, candidate)
                if not isinstance(result, ApplicationEvaluation):
                    raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
                if not result.representation_valid or result.objective_values is None:
                    continue
                if type(result.objective_values) is not tuple:
                    raise TypeError('objective_values must be a tuple')
                if len(result.objective_values) != len(self._objectives.objectives):
                    raise ValueError(
                        'objective vector length must match the declaration'
                    )
                if context.ensure_feasible and result.feasible is not True:
                    continue
                candidates.append(
                    (candidate, result.objective_values[self._objective_position])
                )
            if not candidates:
                modified_targets.add(target)
                continue
            working = _choose_best(
                candidates,
                self._objectives,
                self._objective_position,
                context.random_source,
            )
            modified_targets.add(target)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _work_rule_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        objective_position = self._objective_position
        objective_id = str(self._objectives.objectives[objective_position].objective_id)
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
        if not working_evaluation.representation_valid:
            return OperatorResult(candidates=())

        working = base
        excluded_targets: set[_TimingTarget] = set()
        modified_targets: set[_TimingTarget] = set()
        last_candidates: tuple[_WorkRuleTrial, ...] = ()
        while len(modified_targets) < size:
            analysis = _analysis(working_evaluation)
            finding = _select_work_rule_finding(
                self._instance,
                analysis,
                objective_id,
                context.random_source,
            )
            matched_routes = _work_rule_matching_routes(
                self._instance, working, finding
            )
            available_by_route = {
                route_index: _work_rule_timing_targets(
                    working,
                    route_index,
                    finding,
                    excluded_targets,
                )
                for route_index in range(len(working.routes))
            }
            available_by_route = {
                route_index: targets
                for route_index, targets in available_by_route.items()
                if targets
            }
            route_choices = tuple(
                route_index
                for route_index in matched_routes
                if route_index in available_by_route
            ) or tuple(available_by_route)
            if not route_choices:
                break
            route_index = choose_uniformly(route_choices, context.random_source)
            targets = available_by_route[route_index]
            moves = tuple(
                (target, value)
                for target in targets
                for value in _candidate_values(
                    self._instance,
                    working,
                    analysis,
                    target,
                    self._mode,
                    self._time_step,
                    context.random_source,
                    work_rule_finding=finding,
                )
            )
            sampled_moves = randomized_sample(
                moves, self._candidate_limit, context.random_source
            )
            candidates: list[_WorkRuleTrial] = []
            for target, value in sampled_moves:
                candidate = _set_timing_value(
                    working,
                    target,
                    value,
                    work_rule_finding=finding,
                )
                if candidate is None or candidate == working:
                    continue
                changed_targets = _timing_field_changes_for_move(
                    working, candidate, target, finding
                )
                if not changed_targets:
                    continue
                affected_targets = {target}
                if (
                    target[1] is not None
                    and target[2] == 'break_duration'
                    and finding is not None
                    and finding.kind == 'break'
                ):
                    affected_targets.add((target[0], target[1], 'wait'))
                candidate_modified = (modified_targets - affected_targets) | set(
                    _timing_field_changes_for_move(base, candidate, target, finding)
                )
                if len(candidate_modified) > size:
                    continue
                evaluation = assess_complete_candidate(
                    self._instance,
                    self._objectives,
                    candidate,
                    self._assess_plan,
                    ensure_feasible=False,
                )
                if evaluation is None:
                    continue
                candidates.append(
                    (
                        candidate,
                        evaluation,
                        tuple(candidate_modified),
                        changed_targets,
                    )
                )
            if not candidates:
                excluded_targets.update(targets)
                continue
            last_candidates = tuple(candidates)
            ordered = _order_work_rule_timing_candidates(
                last_candidates,
                self._objectives,
                objective_position,
                context.random_source,
            )
            selected = _select_work_rule_timing_trial(
                ordered,
                working_evaluation,
                finding,
                self._instance,
                self._objectives,
                objective_position,
                context.random_source,
            )
            working, working_evaluation, modified, changed = selected
            modified_targets = set(modified)
            excluded_targets.update(changed)

        if not last_candidates:
            return OperatorResult(candidates=())
        if context.ensure_feasible and working_evaluation.feasible is not True:
            return OperatorResult(candidates=())
        ordered_final = _order_work_rule_timing_candidates(
            last_candidates,
            self._objectives,
            objective_position,
            context.random_source,
        )
        proposals = tuple(
            CandidateProposal(
                Candidate(candidate),
                len(modified),
            )
            for candidate, evaluation, modified, _changed in ordered_final
            if len(modified) <= size
            if not context.ensure_feasible or evaluation.feasible is True
        )
        return OperatorResult(candidates=proposals[: context.max_candidates])

    def _window_timing_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        target_position = self._objective_position
        window_id = self._instance.time_window_violation_objective_id
        window_position = (
            target_position
            if window_id is None
            else objective_position(self._objectives, window_id)
        )
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
        modified_targets: set[_TimingTarget] = set()
        modifications = 0
        last_candidates: tuple[tuple[VrpSolution, ApplicationEvaluation], ...] = ()
        objective_id = str(self._objectives.objectives[target_position].objective_id)
        for _ in range(size):
            analysis = _complete_analysis(working_evaluation)
            if not working_evaluation.representation_valid:
                analysis = None
            targets = _targets(
                self._instance,
                working,
                analysis,
                self._mode,
                objective_id,
                excluded_targets=modified_targets,
                activity_window_index=self._activity_window_index,
            )
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            alternatives = _candidate_values(
                self._instance,
                working,
                analysis,
                target,
                self._mode,
                self._time_step,
                context.random_source,
                activity_window_index=self._activity_window_index,
            )
            candidates: list[tuple[VrpSolution, ApplicationEvaluation]] = []
            seen: set[VrpSolution] = set()
            for value in randomized_sample(
                alternatives, self._candidate_limit, context.random_source
            ):
                candidate = _set_timing_value(working, target, value)
                if candidate is None or candidate == working or candidate in seen:
                    continue
                evaluation = assess_complete_candidate(
                    self._instance,
                    self._objectives,
                    candidate,
                    self._assess_plan,
                    ensure_feasible=context.ensure_feasible,
                )
                if evaluation is None:
                    continue
                seen.add(candidate)
                candidates.append((candidate, evaluation))
            modified_targets.add(target)
            if not candidates:
                continue

            last_candidates = _rank_service_window_candidates(
                candidates,
                self._objectives,
                window_position=window_position,
                target_position=target_position,
                random_source=context.random_source,
            )
            working, working_evaluation = last_candidates[0]
            modifications += 1

        if not modifications:
            return OperatorResult(candidates=())
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(candidate), modifications)
                for candidate, _evaluation in last_candidates[: context.max_candidates]
            )
        )

    def _personnel_calendar_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        objective_position = self._objective_position
        if objective_position is None:
            raise RuntimeError('personnel calendar timing requires an objective')
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
        if not working_evaluation.representation_valid:
            return OperatorResult(candidates=())

        working = base
        modified_targets: set[_TimingTarget] = set()
        modifications = 0
        last_candidates: tuple[
            tuple[VrpSolution, object, ApplicationEvaluation], ...
        ] = ()
        objective_id = str(self._objectives.objectives[objective_position].objective_id)
        for _ in range(size):
            analysis = _analysis(working_evaluation)
            targets = _targets(
                self._instance,
                working,
                analysis,
                self._mode,
                objective_id,
                excluded_targets=modified_targets,
                restricted_personnel_ids=self._restricted_personnel_ids,
            )
            if not targets:
                break
            targets_by_route: dict[int, list[_TimingTarget]] = {}
            for target in targets:
                targets_by_route.setdefault(target[0], []).append(target)
            route_index = choose_uniformly(
                tuple(targets_by_route), context.random_source
            )
            target = choose_uniformly(
                targets_by_route[route_index], context.random_source
            )
            alternatives = _candidate_values(
                self._instance,
                working,
                analysis,
                target,
                self._mode,
                self._time_step,
                context.random_source,
            )
            candidates: list[tuple[VrpSolution, object, ApplicationEvaluation]] = []
            for value in randomized_sample(
                alternatives, self._candidate_limit, context.random_source
            ):
                candidate = _set_timing_value(working, target, value)
                if candidate is None or candidate == working:
                    continue
                evaluation = assess_complete_candidate(
                    self._instance,
                    self._objectives,
                    candidate,
                    self._assess_plan,
                    ensure_feasible=context.ensure_feasible,
                )
                if evaluation is None:
                    continue
                assert evaluation.objective_values is not None
                candidates.append(
                    (
                        candidate,
                        evaluation.objective_values[objective_position],
                        evaluation,
                    )
                )
            modified_targets.add(target)
            if not candidates:
                continue
            last_candidates = _order_availability_candidates(
                candidates,
                self._objectives,
                objective_position,
                context.random_source,
            )
            working, _value, working_evaluation = last_candidates[0]
            modifications += 1

        if not modifications:
            return operator_result(None, modifications=0, requested_size=size)
        proposals = tuple(
            CandidateProposal(Candidate(candidate), modifications)
            for candidate, _value, _evaluation in last_candidates[
                : context.max_candidates
            ]
        )
        return OperatorResult(candidates=proposals)

    def _availability_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        objective_position = self._objective_position
        if objective_position is None:
            raise RuntimeError('vehicle availability timing requires an objective')
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
        modified_targets: set[_TimingTarget] = set()
        modifications = 0
        last_candidates: tuple[
            tuple[VrpSolution, object, ApplicationEvaluation], ...
        ] = ()
        for _ in range(size):
            analysis = _analysis(working_evaluation)
            if analysis is not None and not analysis.is_complete:
                analysis = None
            targets = tuple(
                target
                for target in _targets(
                    self._instance,
                    working,
                    analysis,
                    self._mode,
                    str(self._objectives.objectives[objective_position].objective_id),
                )
                if target not in modified_targets
            )
            if not targets:
                break
            route_targets: dict[int, list[_TimingTarget]] = {}
            for target in targets:
                route_targets.setdefault(target[0], []).append(target)
            route_scores = _vehicle_availability_route_scores(
                analysis,
                str(self._objectives.objectives[objective_position].objective_id),
                working,
            )
            scored_routes = tuple(
                route_index
                for route_index in route_targets
                if route_index in route_scores
            )
            if scored_routes:
                largest_score = max(route_scores[index] for index in scored_routes)
                route_choices = tuple(
                    index
                    for index in scored_routes
                    if route_scores[index] == largest_score
                )
            else:
                route_choices = tuple(route_targets)
            route_index = choose_uniformly(route_choices, context.random_source)
            target = choose_uniformly(route_targets[route_index], context.random_source)
            alternatives = _candidate_values(
                self._instance,
                working,
                analysis,
                target,
                self._mode,
                self._time_step,
                context.random_source,
            )
            candidates: list[tuple[VrpSolution, object, ApplicationEvaluation]] = []
            for value in randomized_sample(
                alternatives, self._candidate_limit, context.random_source
            ):
                candidate = _set_timing_value(working, target, value)
                if candidate is None or candidate == working:
                    continue
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
                candidates.append(
                    (
                        candidate,
                        evaluation.objective_values[objective_position],
                        evaluation,
                    )
                )
            if not candidates:
                modified_targets.add(target)
                continue
            last_candidates = _order_availability_candidates(
                candidates,
                self._objectives,
                objective_position,
                context.random_source,
            )
            working, _value, working_evaluation = last_candidates[0]
            modified_targets.add(target)
            modifications += 1

        if not modifications:
            return operator_result(None, modifications=0, requested_size=size)
        proposals = tuple(
            CandidateProposal(Candidate(candidate), modifications)
            for candidate, _value, _evaluation in last_candidates[
                : context.max_candidates
            ]
        )
        return OperatorResult(candidates=proposals)


def _departure_profile_result(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    base: VrpSolution,
    size: int,
    objective_index: int,
    time_step: float,
    candidate_limit: int,
    context: CandidateContext,
) -> OperatorResult:
    """Generate independent profile-sensitive trials and rank complete plans."""
    bank = instance.scenario_bank
    builtin_profile = (
        bank.post_horizon_rule_id,
        bank.post_horizon_rule_configuration_id,
    ) == ('constant_last_boundary', 'builtin-v1')
    durations = _service_durations(instance)
    base_evaluation: ApplicationEvaluation | None = None
    if builtin_profile:
        base_profiles = [
            _route_profile_values(instance, route, durations) for route in base.routes
        ]
    else:
        base_evaluation = assess_plan(instance, objectives, base)
        if not isinstance(base_evaluation, ApplicationEvaluation):
            raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
        analysis = _analysis(base_evaluation)
        if (
            not base_evaluation.representation_valid
            or analysis is None
            or not analysis.is_complete
        ):
            return OperatorResult(candidates=())
        base_profiles = []
        for route_index in range(len(base.routes)):
            profile = _route_profile_values_from_analysis(base, analysis, route_index)
            if profile is None:
                return OperatorResult(candidates=())
            base_profiles.append(profile)

    proposals: list[tuple[VrpSolution, int, object]] = []
    seen_solutions: set[VrpSolution] = set()
    base_move_profiles: dict[
        tuple[_TimingTarget, float],
        tuple[VrpSolution, _RouteProfileValues, tuple[int, ...]],
    ] = {}
    # A bounded retry budget lets random trials avoid duplicate plans without
    # spending unbounded time when the base has only a few possible moves.
    attempt_limit = candidate_limit * 4
    for _ in range(attempt_limit):
        if len(seen_solutions) >= candidate_limit:
            break
        working = base
        profiles = list(base_profiles)
        current_evaluation = base_evaluation
        modified_targets: set[_TimingTarget] = set()
        modifications = 0
        for _ in range(size):
            eligible_by_leg: dict[
                tuple[int, int], dict[_TimingTarget, list[_ProfileOption]]
            ] = {}
            for target in _departure_profile_targets(working):
                if target in modified_targets:
                    continue
                route_index, stop_index, _field = target
                for value in _candidate_values(
                    instance,
                    working,
                    None,
                    target,
                    'departure_profile',
                    time_step,
                    context.random_source,
                ):
                    first_affected_leg = 0 if stop_index is None else stop_index + 1
                    cache_key = (target, value)
                    cached_move = (
                        base_move_profiles.get(cache_key)
                        if builtin_profile and modifications == 0
                        else None
                    )
                    candidate_evaluation: ApplicationEvaluation | None = None
                    if cached_move is not None:
                        candidate, candidate_profile, changed_legs = cached_move
                    else:
                        candidate_plan = _set_timing_value(working, target, value)
                        if candidate_plan is None or candidate_plan == working:
                            continue
                        candidate = candidate_plan
                        if builtin_profile:
                            candidate_profile = _route_profile_values(
                                instance, candidate.routes[route_index], durations
                            )
                        else:
                            candidate_evaluation = assess_plan(
                                instance, objectives, candidate
                            )
                            if not isinstance(
                                candidate_evaluation, ApplicationEvaluation
                            ):
                                raise TypeError(
                                    'VrpPlanAssessor must return ApplicationEvaluation'
                                )
                            candidate_analysis = _analysis(candidate_evaluation)
                            if (
                                not candidate_evaluation.representation_valid
                                or candidate_analysis is None
                                or not candidate_analysis.is_complete
                            ):
                                continue
                            profile = _route_profile_values_from_analysis(
                                candidate, candidate_analysis, route_index
                            )
                            if profile is None:
                                continue
                            candidate_profile = profile
                        changed_legs = _profile_changed_legs(
                            profiles[route_index],
                            candidate_profile,
                            first_affected_leg,
                        )
                        if builtin_profile and modifications == 0:
                            base_move_profiles[cache_key] = (
                                candidate,
                                candidate_profile,
                                changed_legs,
                            )
                    for leg_index in changed_legs:
                        options = eligible_by_leg.setdefault(
                            (route_index, leg_index), {}
                        ).setdefault(target, [])
                        option = (candidate, candidate_profile, candidate_evaluation)
                        if all(existing[0] != candidate for existing in options):
                            options.append(option)

            if not eligible_by_leg:
                break
            eligible_legs = tuple(eligible_by_leg)
            selected_leg = choose_uniformly(eligible_legs, context.random_source)
            eligible_fields = tuple(eligible_by_leg[selected_leg])
            target = choose_uniformly(eligible_fields, context.random_source)
            candidate, candidate_profile, candidate_evaluation = choose_uniformly(
                eligible_by_leg[selected_leg][target], context.random_source
            )
            working = candidate
            profiles[target[0]] = candidate_profile
            if candidate_evaluation is not None:
                current_evaluation = candidate_evaluation
            modified_targets.add(target)
            modifications += 1

        if not modifications or working in seen_solutions:
            continue
        seen_solutions.add(working)
        evaluation = current_evaluation
        if evaluation is None:
            evaluation = assess_plan(instance, objectives, working)
        if not isinstance(evaluation, ApplicationEvaluation):
            raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
        if not evaluation.representation_valid or evaluation.objective_values is None:
            continue
        if type(evaluation.objective_values) is not tuple:
            raise TypeError('objective_values must be a tuple')
        if len(evaluation.objective_values) != len(objectives.objectives):
            raise ValueError('objective vector length must match the declaration')
        if context.ensure_feasible and evaluation.feasible is not True:
            continue
        proposals.append(
            (working, modifications, evaluation.objective_values[objective_index])
        )
        if len(proposals) >= candidate_limit:
            break

    objective = objectives.objectives[objective_index]

    def compare(
        left: tuple[VrpSolution, int, object],
        right: tuple[VrpSolution, int, object],
    ) -> int:
        result = objective.compare(left[2], right[2])
        if result is ObjectiveComparison.LESS:
            return -1
        if result is ObjectiveComparison.GREATER:
            return 1
        return 0

    proposals.sort(key=cmp_to_key(compare))
    ordered: list[tuple[VrpSolution, int, object]] = []
    position = 0
    while position < len(proposals):
        end = position + 1
        while (
            end < len(proposals) and compare(proposals[position], proposals[end]) == 0
        ):
            end += 1
        ordered.extend(randomized_order(proposals[position:end], context.random_source))
        position = end

    return OperatorResult(
        candidates=tuple(
            CandidateProposal(Candidate(solution), modifications)
            for solution, modifications, _value in ordered[: context.max_candidates]
        )
    )


def _departure_profile_targets(solution: VrpSolution) -> tuple[_TimingTarget, ...]:
    targets: list[_TimingTarget] = []
    for route_index, route in enumerate(solution.routes):
        targets.append((route_index, None, 'start_time'))
        for stop_index, stop in enumerate(route.stops):
            if type(stop) is DepotOnlyStop:
                targets.append((route_index, stop_index, 'wait'))
            elif type(stop) in (ActivityStop, TransferOperationStop):
                targets.extend(
                    (
                        (route_index, stop_index, 'pre_service_wait'),
                        (route_index, stop_index, 'wait'),
                    )
                )
    return tuple(targets)


def _scenario_differential_result(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    base: VrpSolution,
    size: int,
    objective_index: int,
    time_step: float,
    candidate_limit: int,
    context: CandidateContext,
) -> OperatorResult:
    """Generate distinct multi-field trials guided by scenario sensitivity."""
    bank = instance.scenario_bank
    builtin_profile = (
        bank.post_horizon_rule_id,
        bank.post_horizon_rule_configuration_id,
    ) == ('constant_last_boundary', 'builtin-v1')
    sensitivity_possible = len(bank.scenarios) > 1
    if builtin_profile and sensitivity_possible:
        sensitivity_possible = not _scenario_profiles_identical_for_solution(
            instance, base
        )
    durations = _service_durations(instance)
    base_evaluation: ApplicationEvaluation | None = None
    if builtin_profile and sensitivity_possible:
        base_observations = tuple(
            _route_profile_observations(
                route,
                _route_profile_values(instance, route, durations),
                durations,
            )
            for route in base.routes
        )
    elif not builtin_profile and sensitivity_possible:
        base_evaluation = assess_plan(instance, objectives, base)
        if not isinstance(base_evaluation, ApplicationEvaluation):
            raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
        analysis = _analysis(base_evaluation)
        base_observations = (
            tuple(
                _route_observations_from_analysis(base, analysis, route_index)
                for route_index in range(len(base.routes))
            )
            if analysis is not None and analysis.is_complete
            else tuple(() for _route in base.routes)
        )
    else:
        base_observations = tuple(() for _route in base.routes)

    sensitivity_available = sensitivity_possible
    if not builtin_profile:
        base_analysis = None if base_evaluation is None else _analysis(base_evaluation)
        sensitivity_available = (
            sensitivity_possible
            and base_evaluation is not None
            and base_evaluation.representation_valid
            and base_analysis is not None
            and base_analysis.is_complete
        )

    options_by_target: dict[_TimingTarget, list[tuple[float, bool]]] = {}
    for target in _departure_profile_targets(base):
        route_index, stop_index, field = target
        current = _timing_value(base, target)
        for value in (current - time_step, current + time_step):
            if value < 0.0 or value == current:
                continue
            candidate = _set_timing_value(base, target, value)
            if candidate is None or candidate == base:
                continue
            sensitive = False
            if sensitivity_available:
                if builtin_profile:
                    profile = _route_profile_values(
                        instance, candidate.routes[route_index], durations
                    )
                    candidate_observations = _route_profile_observations(
                        candidate.routes[route_index], profile, durations
                    )
                else:
                    evaluation = assess_plan(instance, objectives, candidate)
                    if not isinstance(evaluation, ApplicationEvaluation):
                        raise TypeError(
                            'VrpPlanAssessor must return ApplicationEvaluation'
                        )
                    if not evaluation.representation_valid:
                        continue
                    analysis = _analysis(evaluation)
                    candidate_observations = (
                        _route_observations_from_analysis(
                            candidate, analysis, route_index
                        )
                        if analysis is not None and analysis.is_complete
                        else ()
                    )
                sensitive = _scenario_move_is_sensitive(
                    base_observations[route_index], candidate_observations
                )
            options_by_target.setdefault(target, []).append((value, sensitive))

    sensitive_targets = tuple(
        target
        for target, options in options_by_target.items()
        if any(sensitive for _value, sensitive in options)
    )
    if sensitive_targets:
        eligible_by_target = {
            target: [option for option in options_by_target[target] if option[1]]
            for target in sensitive_targets
        }
    else:
        # No scenario-sensitive move is available, so use any domain-valid
        # timing field as the specification requires.
        eligible_by_target = options_by_target

    proposals: list[tuple[VrpSolution, int, object]] = []
    seen_solutions: set[VrpSolution] = set()
    attempt_limit = candidate_limit * 8
    for _ in range(attempt_limit):
        if len(seen_solutions) >= candidate_limit:
            break
        working = base
        remaining_targets = list(eligible_by_target)
        modifications = 0
        for _ in range(min(size, len(remaining_targets))):
            target = choose_uniformly(remaining_targets, context.random_source)
            value, _sensitive = choose_uniformly(
                eligible_by_target[target], context.random_source
            )
            updated = _set_timing_value(working, target, value)
            if updated is None:
                continue
            working = updated
            remaining_targets.remove(target)
            modifications += 1

        if not modifications or working in seen_solutions:
            continue
        seen_solutions.add(working)
        evaluation = assess_plan(instance, objectives, working)
        if not isinstance(evaluation, ApplicationEvaluation):
            raise TypeError('VrpPlanAssessor must return ApplicationEvaluation')
        if not evaluation.representation_valid or evaluation.objective_values is None:
            continue
        if type(evaluation.objective_values) is not tuple:
            raise TypeError('objective_values must be a tuple')
        if len(evaluation.objective_values) != len(objectives.objectives):
            raise ValueError('objective vector length must match the declaration')
        if context.ensure_feasible and evaluation.feasible is not True:
            continue
        proposals.append(
            (working, modifications, evaluation.objective_values[objective_index])
        )
        if len(proposals) >= candidate_limit:
            break

    objective = objectives.objectives[objective_index]

    def compare(
        left: tuple[VrpSolution, int, object],
        right: tuple[VrpSolution, int, object],
    ) -> int:
        result = objective.compare(left[2], right[2])
        if result is ObjectiveComparison.LESS:
            return -1
        if result is ObjectiveComparison.GREATER:
            return 1
        return 0

    proposals.sort(key=cmp_to_key(compare))
    ordered: list[tuple[VrpSolution, int, object]] = []
    position = 0
    while position < len(proposals):
        end = position + 1
        while (
            end < len(proposals) and compare(proposals[position], proposals[end]) == 0
        ):
            end += 1
        ordered.extend(randomized_order(proposals[position:end], context.random_source))
        position = end
    return OperatorResult(
        candidates=tuple(
            CandidateProposal(Candidate(solution), modifications)
            for solution, modifications, _value in ordered[: context.max_candidates]
        )
    )


def _route_profile_observations(
    route: VehicleDayRoute,
    profile: _RouteProfileValues,
    durations: tuple[dict[str, PackageRecord], dict[str, InterventionRecord]],
) -> _ScenarioObservations:
    observations: list[tuple[float, ...]] = []
    for scenario_profile in profile:
        current_time = route.start_time
        values: list[float] = []
        for leg_index, leg in enumerate(route.legs):
            for travel_time in scenario_profile[leg_index]:
                current_time += travel_time
                values.extend((travel_time, current_time))
            if leg_index < len(route.stops):
                # Retain arrivals for zero-arc legs as well.
                values.append(current_time)
                stop = route.stops[leg_index]
                current_time += (
                    stop.pre_service_wait
                    + _stop_service_duration(stop, durations)
                    + stop.wait
                )
        observations.append(tuple(values))
    return tuple(observations)


def _timing_value(solution: VrpSolution, target: _TimingTarget) -> float:
    route_index, stop_index, field = target
    if stop_index is None:
        return solution.routes[route_index].start_time
    return cast(float, getattr(solution.routes[route_index].stops[stop_index], field))


def _scenario_profiles_identical_for_solution(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
) -> bool:
    """Return whether scenarios agree on every arc this plan can traverse."""
    scenarios = instance.scenario_bank.scenarios
    if len(scenarios) < 2:
        return True
    bank = instance.scenario_bank
    scenario_ids = tuple(scenario.scenario_id for scenario in scenarios)
    for route in solution.routes:
        for leg in route.legs:
            for arc_id in leg.arc_ids:
                for day_index, boundaries in enumerate(bank.day_boundaries, start=1):
                    for boundary_index in range(len(boundaries)):
                        first = bank.travel_times[
                            (scenario_ids[0], arc_id, day_index, boundary_index)
                        ]
                        if any(
                            bank.travel_times[
                                (scenario_id, arc_id, day_index, boundary_index)
                            ]
                            != first
                            for scenario_id in scenario_ids[1:]
                        ):
                            return False
    return True


def _route_observations_from_analysis(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    route_index: int,
) -> _ScenarioObservations:
    route = solution.routes[route_index]
    observations: list[tuple[float, ...]] = []
    for scenario in analysis.scenario_evaluations:
        vehicle_day = next(
            (
                item
                for item in scenario.vehicle_days
                if item.vehicle_id == route.vehicle_id
                and item.day_index == route.day_index
            ),
            None,
        )
        if vehicle_day is None or len(vehicle_day.legs) != len(route.legs):
            return ()
        values: list[float] = []
        for leg_index, leg in enumerate(vehicle_day.legs):
            for arc in leg.arc_traversals:
                values.extend((arc.travel_time, arc.arrival_time))
            if leg_index < len(vehicle_day.stops):
                values.append(vehicle_day.stops[leg_index].arrival_time)
        observations.append(tuple(values))
    return tuple(observations)


def _scenario_move_is_sensitive(
    base: _ScenarioObservations,
    candidate: _ScenarioObservations,
) -> bool:
    if len(base) < 2 or len(base) != len(candidate):
        return False
    if not base or any(len(row) != len(candidate[0]) for row in base):
        return False
    if any(len(row) != len(base[0]) for row in candidate):
        return False
    for observation_index in range(len(base[0])):
        first_delta = candidate[0][observation_index] - base[0][observation_index]
        for scenario_index in range(1, len(base)):
            delta = (
                candidate[scenario_index][observation_index]
                - base[scenario_index][observation_index]
            )
            if not isclose(delta, first_delta, rel_tol=1e-9, abs_tol=1e-9):
                return True
    return False


def _service_durations(
    instance: LoadedVrpInstance,
) -> tuple[dict[str, PackageRecord], dict[str, InterventionRecord]]:
    packages = {item.package_id: item for item in instance.package_data.packages}
    interventions = {
        item.intervention_id: item for item in instance.intervention_data.interventions
    }
    return packages, interventions


def _stop_service_duration(
    stop: RouteStop,
    durations: tuple[dict[str, PackageRecord], dict[str, InterventionRecord]],
) -> float:
    packages, interventions = durations
    if type(stop) is ActivityStop:
        reference = stop.activity_ref
        if reference.kind in {'pickup', 'delivery'}:
            package = packages[reference.activity_id]
            return float(
                package.pickup_service_duration
                if reference.kind == 'pickup'
                else package.delivery_service_duration
            )
        return float(interventions[reference.activity_id].service_duration)
    if type(stop) is TransferOperationStop:
        package = packages[stop.transfer_step_ref.package_id]
        return float(
            package.transfer_out_service_duration
            if stop.role == 'out'
            else package.transfer_in_service_duration
        )
    return 0.0


def _route_profile_values(
    instance: LoadedVrpInstance,
    route: VehicleDayRoute,
    durations: tuple[dict[str, PackageRecord], dict[str, InterventionRecord]],
) -> _RouteProfileValues:
    bank = instance.scenario_bank
    day_starts = tuple(day[0] for day in bank.day_boundaries)
    scenario_profiles: list[tuple[tuple[float, ...], ...]] = []
    for scenario in bank.scenarios:
        current_time = route.start_time
        leg_values: list[tuple[float, ...]] = []
        for leg_index, leg in enumerate(route.legs):
            travel_values: list[float] = []
            for arc_id in leg.arc_ids:
                travel_time = _profile_travel_time(
                    bank, scenario.scenario_id, arc_id, current_time, day_starts
                )
                travel_values.append(travel_time)
                current_time += travel_time
            leg_values.append(tuple(travel_values))
            if leg_index < len(route.stops):
                stop = route.stops[leg_index]
                current_time += (
                    stop.pre_service_wait
                    + _stop_service_duration(stop, durations)
                    + stop.wait
                )
        scenario_profiles.append(tuple(leg_values))
    return tuple(scenario_profiles)


def _profile_travel_time(
    bank: LoadedScenarioBank,
    scenario_id: str,
    arc_id: str,
    departure_time: float,
    day_starts: tuple[float, ...],
) -> float:
    final_boundary = bank.day_boundaries[-1][-1]
    if departure_time >= final_boundary:
        return bank.travel_times[
            (
                scenario_id,
                arc_id,
                len(bank.planning_dates),
                len(bank.day_boundaries[-1]) - 1,
            )
        ]
    day_index = bisect_right(day_starts, departure_time)
    if day_index < 1 or day_index > len(bank.day_boundaries):
        raise RuntimeError(f'no ScenarioBank day covers time {departure_time!r}')
    boundaries = bank.day_boundaries[day_index - 1]
    segment = bisect_right(boundaries, departure_time) - 1
    segment = min(max(segment, 0), len(boundaries) - 2)
    left_time = boundaries[segment]
    right_time = boundaries[segment + 1]
    left_value = bank.travel_times[(scenario_id, arc_id, day_index, segment)]
    right_value = bank.travel_times[(scenario_id, arc_id, day_index, segment + 1)]
    fraction = (departure_time - left_time) / (right_time - left_time)
    return left_value + fraction * (right_value - left_value)


def _route_profile_values_from_analysis(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    route_index: int,
) -> _RouteProfileValues | None:
    route = solution.routes[route_index]
    scenario_profiles: list[tuple[tuple[float, ...], ...]] = []
    for scenario in analysis.scenario_evaluations:
        vehicle_day = next(
            (
                item
                for item in scenario.vehicle_days
                if item.vehicle_id == route.vehicle_id
                and item.day_index == route.day_index
            ),
            None,
        )
        if vehicle_day is None:
            return None
        scenario_profiles.append(
            tuple(
                tuple(arc.travel_time for arc in leg.arc_traversals)
                for leg in vehicle_day.legs
            )
        )
    if not scenario_profiles:
        return None
    return tuple(scenario_profiles)


def _profile_changed_legs(
    current: _RouteProfileValues,
    candidate: _RouteProfileValues,
    first_affected_leg: int,
) -> tuple[int, ...]:
    changed: list[int] = []
    for leg_index in range(first_affected_leg, min(len(current[0]), len(candidate[0]))):
        if any(
            current[scenario_index][leg_index] != candidate[scenario_index][leg_index]
            for scenario_index in range(min(len(current), len(candidate)))
        ):
            changed.append(leg_index)
    return tuple(changed)


def _analysis(evaluation: ApplicationEvaluation) -> VrpOperatorAnalysis | None:
    if isinstance(evaluation.analysis, VrpOperatorAnalysis):
        return evaluation.analysis
    return None


def _complete_analysis(
    evaluation: ApplicationEvaluation,
) -> VrpOperatorAnalysis | None:
    analysis = _analysis(evaluation)
    if analysis is not None and analysis.is_complete:
        return analysis
    return None


_WORK_RULE_KINDS = frozenset({'driver_work', 'driving_time', 'break', 'rest', 'hos'})


def _select_work_rule_finding(
    instance: LoadedVrpInstance,
    analysis: VrpOperatorAnalysis | None,
    objective_id: str,
    random_source: RandomSource,
) -> ResourceFinding | None:
    if analysis is None:
        return None
    if instance.settings.rule_treatment == 'penalty':
        if not analysis.is_complete:
            return None
        scored = tuple(
            (finding, contribution)
            for finding in analysis.resource_findings
            if finding.kind in _WORK_RULE_KINDS and finding.measure > 0.0
            if (
                contribution := dict(finding.penalty_by_objective).get(
                    objective_id, 0.0
                )
            )
            > 0.0
        )
    elif (
        analysis.hard_failure is not None
        and analysis.hard_failure.kind == 'compliance_rule'
    ):
        applicable = tuple(
            finding
            for finding in analysis.resource_findings
            if finding.kind in _WORK_RULE_KINDS
            and finding.measure > 0.0
            and _compliance_work_rule_selected(instance, finding)
        )
        groups: dict[tuple[object, ...], list[ResourceFinding]] = {}
        for finding in applicable:
            groups.setdefault(_work_rule_finding_key(finding), []).append(finding)
        if not groups:
            return None
        group = choose_uniformly(tuple(groups.values()), random_source)
        maximum_measure = max(finding.measure for finding in group)
        return choose_uniformly(
            tuple(finding for finding in group if finding.measure == maximum_measure),
            random_source,
        )
    else:
        return None
    if not scored:
        return None
    maximum = max(contribution for _finding, contribution in scored)
    return choose_uniformly(
        tuple(finding for finding, contribution in scored if contribution == maximum),
        random_source,
    )


def _work_rule_finding_key(finding: ResourceFinding) -> tuple[object, ...]:
    return (
        finding.kind,
        finding.resource_id,
        finding.vehicle_id,
        finding.day_index,
        finding.activity_ref,
    )


def _compliance_work_rule_selected(
    instance: LoadedVrpInstance, finding: ResourceFinding
) -> bool:
    active = instance.settings.active_simple_rules
    if finding.kind == 'driver_work':
        return 'daily_work' in active
    if finding.kind == 'driving_time':
        return bool({'daily_driving', 'continuous_driving'} & active)
    if finding.kind == 'rest':
        return 'minimum_rest' in active
    if finding.kind == 'break':
        return 'continuous_driving' in active
    if finding.kind == 'hos':
        return instance.settings.hos_regime_id != 'none'
    return False


def _work_rule_matching_routes(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    finding: ResourceFinding | None,
) -> tuple[int, ...]:
    if finding is None:
        return ()
    person_id = _person_from_resource_id(instance, finding.resource_id)
    routes: list[int] = []
    for route_index, route in enumerate(solution.routes):
        if finding.vehicle_id is not None and finding.vehicle_id != route.vehicle_id:
            continue
        if finding.day_index is not None and finding.day_index != route.day_index:
            continue
        matching_stops = tuple(
            stop
            for stop in route.stops
            if type(stop) is ActivityStop
            and (
                finding.activity_ref is None
                or stop.activity_ref == finding.activity_ref
            )
        )
        if finding.activity_ref is not None and not matching_stops:
            continue
        if person_id is None:
            if (
                finding.vehicle_id is None
                and finding.day_index is None
                and finding.activity_ref is None
            ):
                continue
        elif finding.kind == 'driving_time':
            if route.driver_id != person_id:
                continue
        elif finding.activity_ref is not None:
            if route.driver_id != person_id and not any(
                person_id in stop.team_member_ids for stop in matching_stops
            ):
                continue
        elif route.driver_id != person_id and not any(
            type(stop) is ActivityStop and person_id in stop.team_member_ids
            for stop in route.stops
        ):
            continue
        routes.append(route_index)
    return tuple(routes)


def _work_rule_timing_targets(
    solution: VrpSolution,
    route_index: int,
    finding: ResourceFinding | None,
    excluded: set[_TimingTarget],
) -> tuple[_TimingTarget, ...]:
    route = solution.routes[route_index]
    result: list[_TimingTarget] = []
    start_target = (route_index, None, 'start_time')
    if start_target not in excluded:
        result.append(start_target)
    for stop_index, stop in enumerate(route.stops):
        if type(stop) not in _STOP_TYPES:
            continue
        fields = ('wait',) if type(stop) is DepotOnlyStop else _WAIT_FIELDS
        for field in fields:
            target = (route_index, stop_index, field)
            if target not in excluded:
                result.append(target)
        if (
            finding is not None
            and finding.kind == 'break'
            and (
                finding.activity_ref is None
                or (
                    type(stop) is ActivityStop
                    and stop.activity_ref == finding.activity_ref
                )
            )
        ):
            target = (route_index, stop_index, 'break_duration')
            if target not in excluded:
                result.append(target)
    return tuple(result)


def _timing_field_changes_for_move(
    before: VrpSolution,
    after: VrpSolution,
    target: _TimingTarget,
    finding: ResourceFinding | None,
) -> tuple[_TimingTarget, ...]:
    affected = [target]
    if (
        target[1] is not None
        and target[2] == 'break_duration'
        and finding is not None
        and finding.kind == 'break'
    ):
        affected.append((target[0], target[1], 'wait'))
    return tuple(
        item
        for item in affected
        if _timing_value(before, item) != _timing_value(after, item)
    )


def _order_work_rule_timing_candidates(
    candidates: tuple[_WorkRuleTrial, ...],
    objectives: ObjectiveDeclaration,
    position: int,
    random_source: RandomSource,
) -> tuple[_WorkRuleTrial, ...]:
    objective = objectives.objectives[position]

    def compare(
        left: _WorkRuleTrial,
        right: _WorkRuleTrial,
    ) -> int:
        left_values = left[1].objective_values
        right_values = right[1].objective_values
        if left_values is None or right_values is None:
            raise ValueError('valid VRP assessment requires objective values')
        comparison = objective.compare(left_values[position], right_values[position])
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    ranked = sorted(candidates, key=cmp_to_key(compare))
    ordered: list[_WorkRuleTrial] = []
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


def _work_rule_measure_score(
    evaluation: ApplicationEvaluation, finding: ResourceFinding
) -> float:
    analysis = _analysis(evaluation)
    if analysis is None:
        return float('inf')
    matches = tuple(
        item
        for item in analysis.resource_findings
        if _work_rule_finding_key(item) == _work_rule_finding_key(finding)
    )
    if matches:
        return max(item.measure for item in matches)
    return 0.0 if analysis.is_complete else float('inf')


def _select_work_rule_timing_trial(
    candidates: tuple[_WorkRuleTrial, ...],
    current_evaluation: ApplicationEvaluation,
    finding: ResourceFinding | None,
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    position: int,
    random_source: RandomSource,
) -> _WorkRuleTrial:
    feasible = tuple(item for item in candidates if item[1].feasible is True)
    if feasible:
        return _order_work_rule_timing_candidates(
            feasible, objectives, position, random_source
        )[0]
    if (
        instance.settings.rule_treatment == 'compliance'
        and current_evaluation.feasible is not True
        and finding is not None
    ):
        scored = tuple(
            (candidate, _work_rule_measure_score(candidate[1], finding))
            for candidate in candidates
        )
        known = tuple(
            (candidate, score) for candidate, score in scored if isfinite(score)
        )
        if not known:
            return choose_uniformly(candidates, random_source)
        best = min(score for _candidate, score in known)
        tied = tuple(candidate for candidate, score in known if score == best)
        return choose_uniformly(tied, random_source)
    return _order_work_rule_timing_candidates(
        candidates, objectives, position, random_source
    )[0]


def _targets(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    mode: FeatureTimingMode,
    objective_id: str,
    *,
    excluded_targets: set[_TimingTarget] | None = None,
    activity_window_index: _ActivityWindowIndex | None = None,
    restricted_personnel_ids: frozenset[str] | None = None,
) -> tuple[_TimingTarget, ...]:
    if mode == 'service_window_wait':
        window_targets = _window_targets(
            solution,
            analysis,
            objective_id,
            True,
            excluded_targets=excluded_targets,
            instance=instance,
            activity_window_index=activity_window_index,
        )
        if window_targets:
            return window_targets
        fallback_targets: list[_TimingTarget] = []
        for route_index, route in enumerate(solution.routes):
            for stop_index, stop in enumerate(route.stops):
                target = (route_index, stop_index, 'pre_service_wait')
                if (
                    type(stop) is ActivityStop
                    and target not in (excluded_targets or ())
                    and _activity_has_window(
                        instance,
                        stop,
                        route.day_index,
                        activity_window_index=activity_window_index,
                    )
                ):
                    fallback_targets.append(target)
        return tuple(fallback_targets)
    if mode == 'route_start_window':
        window_targets = _window_targets(
            solution,
            analysis,
            objective_id,
            False,
            excluded_targets=excluded_targets,
            instance=instance,
            activity_window_index=activity_window_index,
        )
        if window_targets:
            return window_targets
        fallback_targets = []
        for route_index, route in enumerate(solution.routes):
            route_target = (route_index, None, 'start_time')
            if route_target in (excluded_targets or ()):
                continue
            for stop in route.stops:
                if type(stop) is ActivityStop and _activity_has_window(
                    instance,
                    stop,
                    route.day_index,
                    activity_window_index=activity_window_index,
                ):
                    fallback_targets.append(route_target)
        return tuple(fallback_targets)

    if mode == 'personnel_calendar':
        if analysis is None:
            restricted_people = (
                _restricted_calendar_personnel(instance)
                if restricted_personnel_ids is None
                else restricted_personnel_ids
            )
            route_indices = {
                route_index
                for route_index, route in enumerate(solution.routes)
                if _route_uses_personnel(route, restricted_people)
            }
        else:
            route_indices = _personnel_calendar_finding_routes(
                analysis, objective_id, solution
            )
        if not route_indices:
            return ()
        selected: list[_TimingTarget] = []
        excluded = excluded_targets or set()
        for route_index, route in enumerate(solution.routes):
            if route_index not in route_indices:
                continue
            route_start = (route_index, None, 'start_time')
            if route_start not in excluded:
                selected.append(route_start)
            for stop_index, stop in enumerate(route.stops):
                if type(stop) not in (ActivityStop, TransferOperationStop):
                    continue
                for field in ('pre_service_wait', 'wait'):
                    target = (route_index, stop_index, field)
                    if target not in excluded:
                        selected.append(target)
        return tuple(selected)

    targets: list[_TimingTarget] = []
    for route_index, route in enumerate(solution.routes):
        targets.append((route_index, None, 'start_time'))
        for stop_index, stop in enumerate(route.stops):
            if type(stop) not in _STOP_TYPES:
                continue
            fields = _fields_for_stop(stop)
            if mode == 'vehicle_availability':
                fields = tuple(field for field in fields if field != 'break_duration')
            targets.extend((route_index, stop_index, field) for field in fields)
    all_targets = tuple(targets)
    if mode == 'departure_profile':
        return all_targets
    if mode == 'vehicle_availability':
        return all_targets
    if mode == 'personnel_availability':
        route_indices = _finding_routes(
            analysis, 'personnel_availability', objective_id, solution
        )
        if not route_indices:
            return all_targets
        return tuple(target for target in all_targets if target[0] in route_indices)
    if mode == 'positive_service_duration':
        activity_by_id = {
            item.package_id: item for item in instance.package_data.packages
        }
        intervention_by_id = {
            item.intervention_id: item
            for item in instance.intervention_data.interventions
        }
        eligible: set[_TimingTarget] = set()
        for route_index, route in enumerate(solution.routes):
            for stop_index, stop in enumerate(route.stops):
                if type(stop) is not ActivityStop:
                    continue
                ref = stop.activity_ref
                duration = 0.0
                if ref.kind in {'pickup', 'delivery'}:
                    package = activity_by_id.get(ref.activity_id)
                    if package is not None:
                        duration = (
                            package.pickup_service_duration
                            if ref.kind == 'pickup'
                            else package.delivery_service_duration
                        )
                elif ref.kind == 'intervention':
                    intervention = intervention_by_id.get(ref.activity_id)
                    if intervention is not None:
                        duration = intervention.service_duration
                if duration > 0.0:
                    eligible.add((route_index, None, 'start_time'))
                    eligible.update(
                        target
                        for target in all_targets
                        if target[0] == route_index
                        and target[1] is not None
                        and target[1] < stop_index
                    )
        return tuple(target for target in all_targets if target in eligible)
    return all_targets


def _restricted_calendar_personnel(
    instance: LoadedVrpInstance,
) -> frozenset[str]:
    """Return personnel whose availability records leave a planning gap."""
    intervals_by_person: dict[str, list[tuple[float, float]]] = {}
    for item in instance.personnel_data.personnel_availability:
        intervals_by_person.setdefault(item.person_id, []).append(
            (item.start_time, item.end_time)
        )
    horizon_start = instance.scenario_bank.day_boundaries[0][0]
    horizon_end = instance.scenario_bank.day_boundaries[-1][-1]
    restricted: set[str] = set()
    for person_id, intervals in intervals_by_person.items():
        cursor = horizon_start
        for interval_start, interval_end in sorted(intervals):
            tolerance = 1e-9 + 1e-12 * max(abs(interval_start), abs(cursor))
            if interval_start > cursor + tolerance:
                restricted.add(person_id)
                break
            cursor = max(cursor, interval_end)
        else:
            tolerance = 1e-9 + 1e-12 * max(abs(cursor), abs(horizon_end))
            if cursor < horizon_end - tolerance:
                restricted.add(person_id)
    return frozenset(restricted)


def _route_uses_personnel(route: VehicleDayRoute, person_ids: frozenset[str]) -> bool:
    """Return whether a person is assigned as route driver or intervention teammate."""
    if route.driver_id in person_ids:
        return True
    return any(
        person_id in person_ids
        for stop in route.stops
        if type(stop) is ActivityStop
        for person_id in stop.team_member_ids
    )


def _personnel_calendar_finding_routes(
    analysis: VrpOperatorAnalysis,
    objective_id: str,
    solution: VrpSolution,
) -> set[int]:
    """Map the strongest positive personnel finding to its assigned routes."""
    contribution_by_person: dict[str, float] = {}
    for finding in analysis.resource_findings:
        if finding.kind != 'personnel_availability':
            continue
        contribution = dict(finding.penalty_by_objective).get(objective_id, 0.0)
        if contribution > 0.0:
            contribution_by_person[finding.resource_id] = max(
                contribution_by_person.get(finding.resource_id, 0.0), contribution
            )
    if not contribution_by_person:
        return set()
    maximum = max(contribution_by_person.values())
    selected_people = {
        person_id
        for person_id, contribution in contribution_by_person.items()
        if contribution == maximum
    }
    selected_person_set = frozenset(selected_people)
    return {
        route_index
        for route_index, route in enumerate(solution.routes)
        if _route_uses_personnel(route, selected_person_set)
    }


def _fields_for_stop(stop: RouteStop) -> tuple[str, ...]:
    if type(stop) is DepotOnlyStop:
        return ('wait', 'break_duration')
    return _WAIT_FIELDS


def _vehicle_availability_route_scores(
    analysis: VrpOperatorAnalysis | None,
    objective_id: str,
    solution: VrpSolution,
) -> dict[int, float]:
    if analysis is None:
        return {}
    contribution_by_vehicle_day: dict[tuple[str, int], float] = {}
    for finding in analysis.resource_findings:
        if finding.kind != 'vehicle_availability' or finding.vehicle_id is None:
            continue
        contribution = next(
            (
                value
                for key, value in finding.penalty_by_objective
                if key == objective_id
            ),
            0.0,
        )
        if contribution <= 0.0 or finding.day_index is None:
            continue
        route_key = (finding.vehicle_id, finding.day_index)
        # Findings are scenario-specific. The documented guidance is the
        # largest per-finding contribution, so compare scenarios for this
        # vehicle-day instead of summing them as if they were independent
        # objective penalties.
        contribution_by_vehicle_day[route_key] = max(
            contribution_by_vehicle_day.get(route_key, 0.0), contribution
        )
    return {
        index: contribution_by_vehicle_day[(route.vehicle_id, route.day_index)]
        for index, route in enumerate(solution.routes)
        if (route.vehicle_id, route.day_index) in contribution_by_vehicle_day
    }


def _finding_routes(
    analysis: VrpOperatorAnalysis | None,
    kind: str,
    objective_id: str,
    solution: VrpSolution,
) -> set[int]:
    if analysis is None:
        return set()
    findings = [
        finding
        for finding in analysis.resource_findings
        if finding.kind == kind
        and any(
            key == objective_id and value > 0.0
            for key, value in finding.penalty_by_objective
        )
    ]
    keys = {
        (finding.vehicle_id, finding.day_index)
        for finding in findings
        if finding.vehicle_id is not None
    }
    return {
        index
        for index, route in enumerate(solution.routes)
        if (route.vehicle_id, route.day_index) in keys
    }


def _window_targets(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    objective_id: str,
    wait_only: bool,
    *,
    excluded_targets: set[_TimingTarget] | None = None,
    instance: LoadedVrpInstance | None = None,
    activity_window_index: _ActivityWindowIndex | None = None,
) -> tuple[_TimingTarget, ...]:
    if analysis is None:
        return ()
    eligible_stop_indices: tuple[tuple[int, ...], ...] | None = None
    if instance is not None:
        eligible_stop_indices = tuple(
            tuple(
                stop_index
                for stop_index, stop in enumerate(route.stops)
                if type(stop) is ActivityStop
                and (
                    excluded_targets is None
                    or (
                        (
                            route_index,
                            stop_index,
                            'pre_service_wait',
                        )
                        not in excluded_targets
                        if wait_only
                        else (route_index, None, 'start_time') not in excluded_targets
                    )
                )
                and _activity_has_window(
                    instance,
                    stop,
                    route.day_index,
                    activity_window_index=activity_window_index,
                )
            )
            for route_index, route in enumerate(solution.routes)
        )
    score_by_occurrence: dict[tuple[int, int], float] = {}
    for scenario in analysis.scenario_evaluations:
        vehicle_day_by_key: dict[
            tuple[str, int],
            tuple[
                VehicleDayScenarioEvaluation,
                dict[int, StopScenarioEvaluation] | None,
            ],
        ]
        if eligible_stop_indices is None:
            vehicle_day_by_key = {
                (item.vehicle_id, item.day_index): (item, None)
                for item in scenario.vehicle_days
            }
        else:
            vehicle_day_by_key = {
                (item.vehicle_id, item.day_index): (
                    item,
                    {trace.stop_index: trace for trace in item.stops},
                )
                for item in scenario.vehicle_days
            }
        for route_index, route in enumerate(solution.routes):
            day_data = vehicle_day_by_key.get((route.vehicle_id, route.day_index))
            if day_data is None:
                continue
            vehicle_day, stop_trace_by_index = day_data
            if eligible_stop_indices is None:
                traces = vehicle_day.stops
            else:
                assert stop_trace_by_index is not None
                traces = tuple(
                    stop_trace_by_index[stop_index]
                    for stop_index in eligible_stop_indices[route_index]
                    if stop_index in stop_trace_by_index
                )
            for stop_trace in traces:
                stop_index = stop_trace.stop_index
                if stop_index < 0 or stop_index >= len(route.stops):
                    continue
                if type(route.stops[stop_index]) is not ActivityStop:
                    continue
                guidance = 0.0
                for assessment in stop_trace.window_assessments_by_objective:
                    if assessment.objective_id == objective_id:
                        guidance = assessment.guidance_value
                        break
                if guidance <= 0.0:
                    continue
                target = (
                    (route_index, stop_index, 'pre_service_wait')
                    if wait_only
                    else (route_index, None, 'start_time')
                )
                if excluded_targets is not None and target in excluded_targets:
                    continue
                occurrence = (route_index, stop_index)
                score_by_occurrence[occurrence] = max(
                    score_by_occurrence.get(occurrence, 0.0), guidance
                )
    if not score_by_occurrence:
        return ()
    maximum = max(score_by_occurrence.values())
    targeted_occurrences = tuple(
        occurrence
        for occurrence, score in score_by_occurrence.items()
        if score == maximum
    )
    if wait_only:
        return tuple(
            (route_index, stop_index, 'pre_service_wait')
            for route_index, stop_index in targeted_occurrences
        )
    return tuple(
        (route_index, None, 'start_time')
        for route_index, _stop_index in targeted_occurrences
    )


def _candidate_values(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    target: _TimingTarget,
    mode: FeatureTimingMode,
    time_step: float,
    random_source: RandomSource,
    *,
    activity_window_index: _ActivityWindowIndex | None = None,
    work_rule_finding: ResourceFinding | None = None,
) -> tuple[float, ...]:
    route_index, stop_index, field = target
    route = solution.routes[route_index]
    current = (
        route.start_time
        if stop_index is None
        else getattr(route.stops[stop_index], field)
    )
    minimum_value = 0.0
    if mode == 'personnel_calendar' and stop_index is not None and field == 'wait':
        minimum_value = route.stops[stop_index].break_duration
    elif mode == 'work_rule' and stop_index is not None and field == 'wait':
        minimum_value = route.stops[stop_index].break_duration
    allowed_start_interval: tuple[float, float] | None = None
    if (
        mode in {'route_start_window', 'personnel_calendar', 'work_rule'}
        and stop_index is None
    ):
        day_index = route.day_index
        if not 1 <= day_index <= len(instance.scenario_bank.day_boundaries):
            return ()
        boundaries = instance.scenario_bank.day_boundaries[day_index - 1]
        allowed_start_interval = (boundaries[0], boundaries[-1])
    if mode == 'work_rule' and field == 'break_duration':
        if work_rule_finding is None or work_rule_finding.kind != 'break':
            return ()
        values = [current + time_step]
    else:
        values = [current - time_step, current + time_step]
    if mode == 'service_window_wait' and stop_index is not None:
        stop = route.stops[stop_index]
        if type(stop) is ActivityStop:
            for trace in _stop_traces(solution, analysis, route_index, stop_index):
                arrival = trace.arrival_time
                for lower, upper, duration in _indexed_activity_windows(
                    instance, stop, route.day_index, activity_window_index
                ):
                    values.extend(
                        (
                            max(0.0, lower - arrival),
                            max(0.0, upper - duration - arrival),
                        )
                    )
    if mode == 'route_start_window' and stop_index is None and analysis is not None:
        for trace in _target_stop_traces(solution, analysis, route_index):
            if trace.service_start is None or trace.service_finish is None:
                continue
            if not 0 <= trace.stop_index < len(route.stops):
                continue
            stop = route.stops[trace.stop_index]
            if type(stop) is not ActivityStop:
                continue
            for lower, upper, _duration in _indexed_activity_windows(
                instance,
                stop,
                route.day_index,
                activity_window_index,
            ):
                values.extend(
                    (
                        current + lower - trace.service_start,
                        current + upper - trace.service_finish,
                    )
                )
    unique_values = dict.fromkeys(values)
    if allowed_start_interval is not None:
        day_start, day_end = allowed_start_interval
        return tuple(
            value
            for value in unique_values
            if isfinite(value) and day_start <= value < day_end and value != current
        )
    return tuple(
        value
        for value in unique_values
        if value >= minimum_value
        and value != current
        and (
            mode not in {'service_window_wait', 'personnel_calendar', 'work_rule'}
            or isfinite(value)
        )
    )


def _stop_traces(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    route_index: int,
    stop_index: int,
) -> tuple[StopScenarioEvaluation, ...]:
    if analysis is None:
        return ()
    route = solution.routes[route_index]
    found: list[StopScenarioEvaluation] = []
    for scenario in analysis.scenario_evaluations:
        vehicle_day_by_key = {
            (item.vehicle_id, item.day_index): item for item in scenario.vehicle_days
        }
        vehicle_day = vehicle_day_by_key.get((route.vehicle_id, route.day_index))
        if vehicle_day is not None and stop_index < len(vehicle_day.stops):
            found.append(vehicle_day.stops[stop_index])
    return tuple(found)


def _target_stop_traces(
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
    route_index: int,
) -> tuple[StopScenarioEvaluation, ...]:
    route = solution.routes[route_index]
    found: list[StopScenarioEvaluation] = []
    for scenario in analysis.scenario_evaluations:
        vehicle_day_by_key = {
            (item.vehicle_id, item.day_index): item for item in scenario.vehicle_days
        }
        vehicle_day = vehicle_day_by_key.get((route.vehicle_id, route.day_index))
        if vehicle_day is not None:
            found.extend(vehicle_day.stops)
    return tuple(found)


def _index_activity_windows(instance: LoadedVrpInstance) -> _ActivityWindowIndex:
    package_durations = {
        item.package_id: {
            'pickup': item.pickup_service_duration,
            'delivery': item.delivery_service_duration,
        }
        for item in instance.package_data.packages
    }
    intervention_durations = {
        item.intervention_id: item.service_duration
        for item in instance.intervention_data.interventions
    }
    values: dict[tuple[str, str, int], list[tuple[float, float, float]]] = {}
    for window in instance.package_data.package_time_windows:
        durations = package_durations.get(window.package_id)
        if durations is None:
            continue
        key = (window.activity, window.package_id, -1)
        values.setdefault(key, []).append(
            (window.start_time, window.end_time, durations[window.activity])
        )
    for intervention_window in instance.intervention_data.intervention_time_windows:
        duration = intervention_durations.get(intervention_window.intervention_id)
        if duration is None:
            continue
        key = (
            'intervention',
            intervention_window.intervention_id,
            intervention_window.day_index,
        )
        values.setdefault(key, []).append(
            (intervention_window.start_time, intervention_window.end_time, duration)
        )
    return {key: tuple(windows) for key, windows in values.items()}


def _indexed_activity_windows(
    instance: LoadedVrpInstance,
    stop: ActivityStop,
    day_index: int,
    activity_window_index: _ActivityWindowIndex | None,
) -> tuple[tuple[float, float, float], ...]:
    if activity_window_index is None:
        return _activity_windows(instance, stop, day_index)
    ref = stop.activity_ref
    key_day = -1 if ref.kind in {'pickup', 'delivery'} else day_index
    return activity_window_index.get((ref.kind, ref.activity_id, key_day), ())


def _activity_windows(
    instance: LoadedVrpInstance, stop: ActivityStop, day_index: int
) -> tuple[tuple[float, float, float], ...]:
    ref = stop.activity_ref
    if ref.kind in {'pickup', 'delivery'}:
        package = next(
            (
                item
                for item in instance.package_data.packages
                if item.package_id == ref.activity_id
            ),
            None,
        )
        if package is None:
            return ()
        duration = (
            package.pickup_service_duration
            if ref.kind == 'pickup'
            else package.delivery_service_duration
        )
        windows = tuple(
            (item.start_time, item.end_time, duration)
            for item in instance.package_data.package_time_windows
            if item.package_id == ref.activity_id and item.activity == ref.kind
        )
        return windows
    intervention = next(
        (
            item
            for item in instance.intervention_data.interventions
            if item.intervention_id == ref.activity_id
        ),
        None,
    )
    if intervention is None:
        return ()
    return tuple(
        (item.start_time, item.end_time, intervention.service_duration)
        for item in instance.intervention_data.intervention_time_windows
        if item.intervention_id == ref.activity_id and item.day_index == day_index
    )


def _activity_has_window(
    instance: LoadedVrpInstance,
    stop: ActivityStop,
    day_index: int,
    *,
    activity_window_index: _ActivityWindowIndex | None = None,
) -> bool:
    return bool(
        _indexed_activity_windows(instance, stop, day_index, activity_window_index)
    )


def _set_timing_value(
    solution: VrpSolution,
    target: _TimingTarget,
    value: float,
    *,
    work_rule_finding: ResourceFinding | None = None,
) -> VrpSolution | None:
    route_index, stop_index, field = target
    routes = list(solution.routes)
    route = routes[route_index]
    if stop_index is None:
        routes[route_index] = replace(route, start_time=value)
    else:
        stops = list(route.stops)
        stop = stops[stop_index]
        updated: RouteStop | None
        if (
            field == 'break_duration'
            and work_rule_finding is not None
            and work_rule_finding.kind == 'break'
        ):
            updated = replace(
                stop,
                break_duration=value,
                wait=max(stop.wait, value),
            )
        else:
            updated = _replace_stop_timing(stop, field, value)
        if updated is None:
            return None
        if updated.break_duration > updated.wait:
            return None
        stops[stop_index] = updated
        routes[route_index] = replace(route, stops=tuple(stops))
    return replace(solution, routes=tuple(routes))


def _replace_stop_timing(stop: RouteStop, field: str, value: float) -> RouteStop | None:
    if field == 'pre_service_wait':
        if type(stop) is ActivityStop:
            return replace(stop, pre_service_wait=value)
        if type(stop) is TransferOperationStop:
            return replace(stop, pre_service_wait=value)
        return None
    if field == 'wait':
        return replace(stop, wait=value)
    if field == 'break_duration':
        return replace(stop, break_duration=value)
    raise ValueError(f'unsupported timing field {field!r}')


def _rank_service_window_candidates(
    candidates: list[tuple[VrpSolution, ApplicationEvaluation]],
    objectives: ObjectiveDeclaration,
    *,
    window_position: int,
    target_position: int,
    random_source: RandomSource,
) -> tuple[tuple[VrpSolution, ApplicationEvaluation], ...]:
    """Apply the documented window-first, then target-first Pareto ranking."""
    window_objective = objectives.objectives[window_position]
    initial_values = candidates[0][1].objective_values
    assert initial_values is not None
    best_window_value = initial_values[window_position]
    window_candidates = [candidates[0]]
    for candidate in candidates[1:]:
        values = candidate[1].objective_values
        assert values is not None
        comparison = window_objective.compare(
            values[window_position], best_window_value
        )
        if comparison is ObjectiveComparison.LESS:
            best_window_value = values[window_position]
            window_candidates = [candidate]
        elif comparison is ObjectiveComparison.EQUAL:
            window_candidates.append(candidate)

    target_objective = objectives.objectives[target_position]
    initial_values = window_candidates[0][1].objective_values
    assert initial_values is not None
    best_target_value = initial_values[target_position]
    target_candidates = [window_candidates[0]]
    for candidate in window_candidates[1:]:
        values = candidate[1].objective_values
        assert values is not None
        comparison = target_objective.compare(
            values[target_position], best_target_value
        )
        if comparison is ObjectiveComparison.LESS:
            best_target_value = values[target_position]
            target_candidates = [candidate]
        elif comparison is ObjectiveComparison.EQUAL:
            target_candidates.append(candidate)

    def dominates(
        left: tuple[VrpSolution, ApplicationEvaluation],
        right: tuple[VrpSolution, ApplicationEvaluation],
    ) -> bool:
        left_values = left[1].objective_values
        right_values = right[1].objective_values
        assert left_values is not None and right_values is not None
        strictly_better = False
        for index, objective in enumerate(objectives.objectives):
            if index == target_position:
                continue
            comparison = objective.compare(left_values[index], right_values[index])
            if comparison is ObjectiveComparison.GREATER:
                return False
            if comparison is ObjectiveComparison.LESS:
                strictly_better = True
        return strictly_better

    frontier = tuple(
        candidate
        for candidate in target_candidates
        if not any(
            other is not candidate and dominates(other, candidate)
            for other in target_candidates
        )
    )
    return randomized_order(frontier, random_source)


def _choose_best(
    candidates: list[tuple[VrpSolution, object]],
    objectives: ObjectiveDeclaration,
    position: int,
    random_source: RandomSource,
) -> VrpSolution:
    objective = objectives.objectives[position]
    best_value = candidates[0][1]
    tied = [candidates[0]]
    for candidate in candidates[1:]:
        comparison = objective.compare(candidate[1], best_value)
        if comparison is ObjectiveComparison.LESS:
            best_value = candidate[1]
            tied = [candidate]
        elif comparison is ObjectiveComparison.EQUAL:
            tied.append(candidate)
    return choose_uniformly(tied, random_source)[0]


def _order_availability_candidates(
    candidates: list[tuple[VrpSolution, object, ApplicationEvaluation]],
    objectives: ObjectiveDeclaration,
    position: int,
    random_source: RandomSource,
) -> tuple[tuple[VrpSolution, object, ApplicationEvaluation], ...]:
    objective = objectives.objectives[position]

    def compare(
        left: tuple[VrpSolution, object, ApplicationEvaluation],
        right: tuple[VrpSolution, object, ApplicationEvaluation],
    ) -> int:
        comparison = objective.compare(left[1], right[1])
        if comparison is ObjectiveComparison.LESS:
            return -1
        if comparison is ObjectiveComparison.GREATER:
            return 1
        return 0

    ranked = sorted(candidates, key=cmp_to_key(compare))
    ordered: list[tuple[VrpSolution, object, ApplicationEvaluation]] = []
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


def _limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result
