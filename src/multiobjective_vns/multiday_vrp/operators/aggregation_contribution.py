"""Scenario-aggregation contribution repair Operators for the multi-day VRP.

Created: 2026-11-04T09:12:38+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace
from math import fsum, isfinite
from typing import Literal

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.records import VrpOperatorAnalysis
from multiobjective_vns.multiday_vrp.extensions import (
    LegScenarioEvaluation,
    VehicleDayScenarioEvaluation,
    VrpScenarioEvaluation,
)
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
from multiobjective_vns.multiday_vrp.operators.feature_common import (
    assess_complete_candidate,
    objective_position,
)
from multiobjective_vns.multiday_vrp.operators.path_search import (
    PathSearchIndex,
    build_path_search_index,
    route_node_sequence,
    sample_loopless_paths,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    DepotOnlyStop,
    RouteLeg,
    TransferOperationStop,
    VrpSolution,
)
from multiobjective_vns.objectives import (
    ObjectiveComparison,
    ObjectiveDeclaration,
)
from multiobjective_vns.records.candidates import (
    Candidate,
    CandidateContext,
    CandidateProposal,
    OperatorResult,
)
from multiobjective_vns.records.evaluation import ApplicationEvaluation
from multiobjective_vns.values import finite_real, require_integer

__all__ = ['AggregationContributionOperator']

type AggregationMoveFamily = Literal['path', 'timing']
type _Alternative = tuple[VrpSolution, tuple[object, ...], int]
type _TimingTarget = tuple[int, int | None, str]
type _LegKey = tuple[int, int]

_STOP_TYPES = (ActivityStop, TransferOperationStop, DepotOnlyStop)
_TIMING_FIELDS = ('pre_service_wait', 'wait', 'break_duration')


class AggregationContributionOperator:
    """Target scenario-sensitive objective contributions with a route move.

    :param instance: Immutable VRP data and weighted ScenarioBank.
    :param objective_declaration: Ordered objective comparison definitions.
    :param assess_plan: Complete-plan assessment callback.
    :param objective_id: Objective whose scenario influence guides the move.
    :param move_family: Whether the move changes one path or timing field.
    :param max_neighborhood_size: Maximum sequential move units.
    :param time_step: Positive step used by timing-family registrations.
    :param max_candidate_alternatives: Maximum complete alternatives assessed.
    :param max_paths_per_leg: Maximum sampled paths for one route leg.
    :param max_arc_expansion_steps: Per-path-search expansion bound.
    :raises TypeError: If a setting has an unsupported Python type.
    :raises ValueError: If a setting is outside its documented range.
    """

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_objective_position',
        '_move_family',
        '_time_step',
        '_maximum_size',
        '_candidate_limit',
        '_path_limit',
        '_expansion_limit',
        '_leave_one_out_instances',
        '_path_search_index',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        objective_id: ObjectiveId,
        move_family: AggregationMoveFamily,
        max_neighborhood_size: int,
        time_step: float = 1.0,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        if move_family not in {'path', 'timing'}:
            raise ValueError(f'unsupported aggregation move family: {move_family!r}')
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._objective_position = objective_position(
            objective_declaration, objective_id
        )
        self._move_family = move_family
        self._time_step = finite_real(time_step, name='time_step')
        if self._time_step <= 0.0:
            raise ValueError('time_step must be positive')
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
        self._leave_one_out_instances = _leave_one_out_instances(instance)
        self._path_search_index: PathSearchIndex | None = None

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` sequential contribution-guided route moves.

        :param context: Base plan, size, feasibility request, and shared RNG.
        :returns: Ranked complete candidates and their changed-unit counts.
        :raises TypeError: If the base or assessor result violates its contract.
        :raises ValueError: If size or the objective vector is inconsistent.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        if context.ensure_feasible and not candidate_is_eligible(
            self._instance,
            self._objectives,
            base,
            ensure_feasible=True,
            assess_plan=self._assess_plan,
        ):
            return operator_result(None, modifications=0, requested_size=size)

        if self._move_family == 'path':
            return self._path_result(base, size, context)
        return self._timing_result(base, size, context)

    def _path_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        working = base
        changed_units: set[tuple[object, ...]] = set()
        modifications = 0
        last_alternatives: tuple[_Alternative, ...] = ()

        while modifications < size:
            current = _assess_complete(
                self._instance,
                self._objectives,
                self._assess_plan,
                working,
                ensure_feasible=False,
            )
            if current is None:
                break
            scenario_id = self._select_scenario(working, current, context)
            target_leg = _select_leg(
                self._instance,
                working,
                _analysis(current),
                scenario_id,
                changed_units,
                context,
            )
            if target_leg is None:
                break
            unit = ('path', *target_leg)
            candidates = self._path_alternatives(working, target_leg, context)
            if not candidates:
                changed_units.add(unit)
                continue

            last_alternatives = self._ordered_candidates(candidates, context)
            changed_units.add(unit)
            modifications += 1
            if modifications < size:
                working = last_alternatives[0][0]

        if not last_alternatives:
            return OperatorResult(candidates=())
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), modifications)
                for solution, _values, _candidate_modifications in last_alternatives[
                    : context.max_candidates
                ]
            )
        )

    def _timing_result(
        self, base: VrpSolution, size: int, context: CandidateContext
    ) -> OperatorResult:
        current = _assess_complete(
            self._instance,
            self._objectives,
            self._assess_plan,
            base,
            ensure_feasible=False,
        )
        if current is None:
            return OperatorResult(candidates=())
        scenario_id = self._select_scenario(base, current, context)
        target_leg = _select_leg(
            self._instance,
            base,
            _analysis(current),
            scenario_id,
            set(),
            context,
        )
        if target_leg is None:
            return OperatorResult(candidates=())

        alternatives: list[_Alternative] = []
        seen_solutions = [base]
        for _ in range(self._candidate_limit):
            working = base
            modifications = 0
            for _ in range(size):
                targets = self._upstream_timing_targets(working, target_leg)
                if not targets:
                    break
                timing_target = choose_uniformly(targets, context.random_source)
                delta = choose_uniformly(
                    (-self._time_step, self._time_step), context.random_source
                )
                candidate = _adjust_timing(working, timing_target, cast_float(delta))
                if candidate is None or candidate == working:
                    continue
                working = candidate
                modifications += 1

            if modifications == 0 or working in seen_solutions:
                continue
            assessed = _assess_complete(
                self._instance,
                self._objectives,
                self._assess_plan,
                working,
                ensure_feasible=context.ensure_feasible,
            )
            if assessed is None or assessed.objective_values is None:
                continue
            seen_solutions.append(working)
            alternatives.append((working, assessed.objective_values, modifications))

        ordered = self._ordered_candidates(tuple(alternatives), context)
        return OperatorResult(
            candidates=tuple(
                CandidateProposal(Candidate(solution), candidate_modifications)
                for solution, _values, candidate_modifications in ordered[
                    : context.max_candidates
                ]
            )
        )

    def _upstream_timing_targets(
        self, solution: VrpSolution, target: _LegKey
    ) -> tuple[_TimingTarget, ...]:
        route_index, leg_index = target
        route = solution.routes[route_index]
        targets: list[_TimingTarget] = [(route_index, None, 'start_time')]
        for stop_index in range(min(leg_index, len(route.stops))):
            stop = route.stops[stop_index]
            if type(stop) not in _STOP_TYPES:
                continue
            fields = (
                ('wait', 'break_duration')
                if type(stop) is DepotOnlyStop
                else _TIMING_FIELDS
            )
            targets.extend((route_index, stop_index, field) for field in fields)
        return tuple(
            target_item
            for target_item in targets
            if any(
                _timing_delta_is_valid(solution, target_item, delta)
                for delta in (-self._time_step, self._time_step)
            )
        )

    def _ordered_candidates(
        self,
        candidates: tuple[_Alternative, ...],
        context: CandidateContext,
    ) -> tuple[_Alternative, ...]:
        remaining = list(candidates)
        ordered: list[_Alternative] = []
        objective = self._objectives.objectives[self._objective_position]
        while remaining:
            best = remaining[0]
            for candidate in remaining[1:]:
                if (
                    objective.compare(
                        candidate[1][self._objective_position],
                        best[1][self._objective_position],
                    )
                    is ObjectiveComparison.LESS
                ):
                    best = candidate
            tied = tuple(
                candidate
                for candidate in remaining
                if objective.compare(
                    candidate[1][self._objective_position],
                    best[1][self._objective_position],
                )
                is ObjectiveComparison.EQUAL
            )
            ordered.extend(randomized_sample(tied, len(tied), context.random_source))
            remaining = [
                candidate
                for candidate in remaining
                if objective.compare(
                    candidate[1][self._objective_position],
                    best[1][self._objective_position],
                )
                is not ObjectiveComparison.EQUAL
            ]
        return tuple(ordered)

    def _select_scenario(
        self,
        solution: VrpSolution,
        assessment: ApplicationEvaluation,
        context: CandidateContext,
    ) -> str:
        scenarios = self._instance.scenario_bank.scenarios
        analysis = _analysis(assessment)
        if assessment.objective_values is None:
            return choose_uniformly(scenarios, context.random_source).scenario_id
        full_value = assessment.objective_values[self._objective_position]
        if (
            len(scenarios) < 2
            or analysis is None
            or not analysis.is_complete
            or not _finite_float(full_value)
            or len(self._leave_one_out_instances) != len(scenarios)
        ):
            return choose_uniformly(scenarios, context.random_source).scenario_id

        influences: list[tuple[str, float]] = []
        for scenario_id, reduced_instance in self._leave_one_out_instances:
            reduced = _assess_complete(
                reduced_instance,
                self._objectives,
                self._assess_plan,
                solution,
                ensure_feasible=False,
            )
            if reduced is None or reduced.objective_values is None:
                return choose_uniformly(scenarios, context.random_source).scenario_id
            value = reduced.objective_values[self._objective_position]
            if not _finite_float(value):
                return choose_uniformly(scenarios, context.random_source).scenario_id
            influences.append(
                (scenario_id, abs(cast_float(value) - cast_float(full_value)))
            )

        maximum = max(value for _scenario_id, value in influences)
        selected = tuple(
            scenario_id for scenario_id, value in influences if value == maximum
        )
        return choose_uniformly(selected, context.random_source)

    def _path_alternatives(
        self,
        working: VrpSolution,
        target: _LegKey,
        context: CandidateContext,
    ) -> tuple[_Alternative, ...]:
        route_index, leg_index = target
        route = working.routes[route_index]
        nodes = route_node_sequence(self._instance, working, route)
        path_search_index = self._path_search_index
        if path_search_index is None:
            path_search_index = build_path_search_index(self._instance)
            self._path_search_index = path_search_index
        paths = sample_loopless_paths(
            self._instance,
            nodes[leg_index],
            nodes[leg_index + 1],
            random_source=context.random_source,
            max_paths=self._path_limit,
            max_arc_expansion_steps=self._expansion_limit,
            path_search_index=path_search_index,
        )
        alternatives: list[_Alternative] = []
        sampled_paths = randomized_sample(
            tuple(path for path in paths if path != route.legs[leg_index].arc_ids),
            self._candidate_limit,
            context.random_source,
        )
        for path in sampled_paths:
            routes = list(working.routes)
            legs = list(route.legs)
            legs[leg_index] = RouteLeg(path)
            routes[route_index] = replace(route, legs=tuple(legs))
            candidate = replace(working, routes=tuple(routes))
            assessed = _assess_complete(
                self._instance,
                self._objectives,
                self._assess_plan,
                candidate,
                ensure_feasible=context.ensure_feasible,
            )
            if assessed is not None and assessed.objective_values is not None:
                alternatives.append((candidate, assessed.objective_values, 1))
        return tuple(alternatives)


def _analysis(evaluation: ApplicationEvaluation) -> VrpOperatorAnalysis | None:
    if isinstance(evaluation.analysis, VrpOperatorAnalysis):
        return evaluation.analysis
    return None


def _assess_complete(
    instance: LoadedVrpInstance,
    objectives: ObjectiveDeclaration,
    assessor: VrpPlanAssessor,
    solution: VrpSolution,
    *,
    ensure_feasible: bool,
) -> ApplicationEvaluation | None:
    return assess_complete_candidate(
        instance,
        objectives,
        solution,
        assessor,
        ensure_feasible=ensure_feasible,
    )


def _select_leg(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis | None,
    scenario_id: str,
    changed_units: set[tuple[object, ...]],
    context: CandidateContext,
) -> _LegKey | None:
    legs = tuple(
        (route_index, leg_index)
        for route_index, route in enumerate(solution.routes)
        for leg_index in range(len(route.legs))
        if ('path', route_index, leg_index) not in changed_units
    )
    if not legs:
        return None
    if analysis is None:
        return choose_uniformly(legs, context.random_source)
    durations = _scenario_leg_durations(instance, solution, analysis)
    deviations: list[tuple[_LegKey, float]] = []
    probabilities = {
        item.scenario_id: item.probability for item in instance.scenario_bank.scenarios
    }
    for key in legs:
        values = durations.get(key, {})
        if len(values) != len(probabilities) or scenario_id not in values:
            continue
        mean = fsum(probabilities[item] * values[item] for item in probabilities)
        deviations.append((key, abs(values[scenario_id] - mean)))
    if not deviations:
        return choose_uniformly(legs, context.random_source)
    maximum = max(value for _key, value in deviations)
    if maximum == 0.0:
        return choose_uniformly(legs, context.random_source)
    targets = tuple(key for key, value in deviations if value == maximum)
    return choose_uniformly(targets, context.random_source)


def _scenario_leg_durations(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    analysis: VrpOperatorAnalysis,
) -> dict[_LegKey, dict[str, float]]:
    by_scenario: dict[str, VrpScenarioEvaluation] = {}
    duplicate_scenarios: set[str] = set()
    for scenario_trace in analysis.scenario_evaluations:
        if scenario_trace.scenario_id in by_scenario:
            duplicate_scenarios.add(scenario_trace.scenario_id)
        else:
            by_scenario[scenario_trace.scenario_id] = scenario_trace

    result: dict[_LegKey, dict[str, float]] = {}
    for scenario in instance.scenario_bank.scenarios:
        record = by_scenario.get(scenario.scenario_id)
        if record is None or scenario.scenario_id in duplicate_scenarios:
            continue

        vehicle_days: dict[tuple[str, int], VehicleDayScenarioEvaluation] = {}
        duplicate_vehicle_days: set[tuple[str, int]] = set()
        for day_trace in record.vehicle_days:
            key = (day_trace.vehicle_id, day_trace.day_index)
            if key in vehicle_days:
                duplicate_vehicle_days.add(key)
            else:
                vehicle_days[key] = day_trace

        for route_index, route in enumerate(solution.routes):
            day_key = (route.vehicle_id, route.day_index)
            day_record = vehicle_days.get(day_key)
            if day_record is None or day_key in duplicate_vehicle_days:
                continue

            leg_traces: dict[int, LegScenarioEvaluation] = {}
            duplicate_legs: set[int] = set()
            for leg_trace in day_record.legs:
                if leg_trace.leg_index in leg_traces:
                    duplicate_legs.add(leg_trace.leg_index)
                else:
                    leg_traces[leg_trace.leg_index] = leg_trace

            for leg_index in range(len(route.legs)):
                leg_record = leg_traces.get(leg_index)
                if leg_record is None or leg_index in duplicate_legs:
                    continue
                expected_arc_ids = route.legs[leg_index].arc_ids
                traversals = leg_record.arc_traversals
                if (
                    len(traversals) != len(expected_arc_ids)
                    or tuple(item.arc_id for item in traversals) != expected_arc_ids
                    or any(
                        not _finite_float(item.travel_time)
                        or cast_float(item.travel_time) < 0.0
                        for item in traversals
                    )
                ):
                    continue
                duration = fsum(item.travel_time for item in traversals)
                if not _finite_float(duration):
                    continue
                result.setdefault((route_index, leg_index), {})[
                    scenario.scenario_id
                ] = duration
    return result


def _leave_one_out_instances(
    instance: LoadedVrpInstance,
) -> tuple[tuple[str, LoadedVrpInstance], ...]:
    scenarios = instance.scenario_bank.scenarios
    if len(scenarios) < 2:
        return ()
    result: list[tuple[str, LoadedVrpInstance]] = []
    for removed in scenarios:
        remaining = tuple(
            item for item in scenarios if item.scenario_id != removed.scenario_id
        )
        total_probability = fsum(item.probability for item in remaining)
        normalized = tuple(
            item._replace(probability=item.probability / total_probability)
            for item in remaining
        )
        bank = instance.scenario_bank._replace(scenarios=normalized)
        result.append((removed.scenario_id, instance._replace(scenario_bank=bank)))
    return tuple(result)


def _adjust_timing(
    solution: VrpSolution, target: _TimingTarget, delta: float
) -> VrpSolution | None:
    route_index, stop_index, field = target
    routes = list(solution.routes)
    route = routes[route_index]
    if stop_index is None:
        value = route.start_time + delta
        if not isfinite(value) or value < 0.0:
            return None
        routes[route_index] = replace(route, start_time=value)
        return replace(solution, routes=tuple(routes))
    stops = list(route.stops)
    stop = stops[stop_index]
    value = getattr(stop, field) + delta
    if not isfinite(value) or value < 0.0:
        return None
    updated = replace(stop, **{field: value})
    if updated.break_duration > updated.wait:
        return None
    stops[stop_index] = updated
    routes[route_index] = replace(route, stops=tuple(stops))
    return replace(solution, routes=tuple(routes))


def _timing_delta_is_valid(
    solution: VrpSolution, target: _TimingTarget, delta: float
) -> bool:
    route_index, stop_index, field = target
    route = solution.routes[route_index]
    if stop_index is None:
        value = route.start_time + delta
        return isfinite(value) and value >= 0.0

    stop = route.stops[stop_index]
    value = getattr(stop, field) + delta
    if not isfinite(value) or value < 0.0:
        return False
    break_duration = value if field == 'break_duration' else stop.break_duration
    wait = value if field == 'wait' else stop.wait
    return break_duration <= wait


def _bounded_limit(value: int, name: str, maximum: int) -> int:
    result = require_integer(value, name=name, minimum=1)
    if result > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return result


def _finite_float(value: object) -> bool:
    return type(value) is float and isfinite(value)


def cast_float(value: object) -> float:
    if type(value) is not float:
        raise TypeError('objective value must be a Python float')
    return value
