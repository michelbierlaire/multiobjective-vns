"""Generic route timing adjustment Operator.

Created: 2026-11-03T21:18:00+01:00
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
    require_vrp_base,
    validate_neighborhood_size,
)
from multiobjective_vns.multiday_vrp.solution import (
    ActivityStop,
    DepotOnlyStop,
    RouteStop,
    TransferOperationStop,
    VrpSolution,
)
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.random_source import RandomSource
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.values import finite_real, require_integer

__all__ = ['TimingAdjustmentOperator']

type _TimingTarget = tuple[int, int | None, str]

_STOP_TYPES = (ActivityStop, TransferOperationStop, DepotOnlyStop)
_STOP_TIMING_FIELDS = ('pre_service_wait', 'wait', 'break_duration')


class TimingAdjustmentOperator:
    """Change route starts, waits, or scheduled break durations by a fixed step."""

    __slots__ = (
        '_instance',
        '_objectives',
        '_assess_plan',
        '_time_step',
        '_maximum_size',
    )

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        time_step: float,
        max_neighborhood_size: int,
    ) -> None:
        self._instance = instance
        self._objectives = objective_declaration
        self._assess_plan = assess_plan
        self._time_step = finite_real(time_step, name='time_step')
        if self._time_step <= 0.0:
            raise ValueError('time_step must be positive')
        self._maximum_size = require_integer(
            max_neighborhood_size,
            name='max_neighborhood_size',
            minimum=1,
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Apply up to ``k`` sequential timing-field changes.

        :param context: Base plan, size, feasibility request, and shared RNG.
        :returns: The modified plan and number of timing changes, or no result.
        :raises TypeError: If the base or assessment callback has an invalid type.
        :raises ValueError: If the size or a valid assessment violates its contract.
        """
        size = validate_neighborhood_size(context.k, self._maximum_size)
        base = require_vrp_base(context)
        working = base
        modifications = 0
        for _ in range(size):
            targets = _changeable_targets(working, self._time_step)
            if not targets:
                break
            target = choose_uniformly(targets, context.random_source)
            direction = -1.0 if _draw(context.random_source) < 0.5 else 1.0
            candidate = _adjust(working, target, direction * self._time_step)
            if candidate is None or not candidate_is_eligible(
                self._instance,
                self._objectives,
                candidate,
                ensure_feasible=context.ensure_feasible,
                assess_plan=self._assess_plan,
            ):
                continue
            working = candidate
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )


def _changeable_targets(
    solution: VrpSolution, time_step: float
) -> tuple[_TimingTarget, ...]:
    targets: list[_TimingTarget] = []
    for route_index, route in enumerate(solution.routes):
        start_target = (route_index, None, 'start_time')
        if any(
            _adjust(solution, start_target, direction * time_step) is not None
            for direction in (-1.0, 1.0)
        ):
            targets.append(start_target)
        for stop_index, stop in enumerate(route.stops):
            if type(stop) not in _STOP_TYPES:
                continue
            fields = (
                ('wait', 'break_duration')
                if type(stop) is DepotOnlyStop
                else _STOP_TIMING_FIELDS
            )
            for field in fields:
                target = (route_index, stop_index, field)
                if any(
                    _adjust(solution, target, direction * time_step) is not None
                    for direction in (-1.0, 1.0)
                ):
                    targets.append(target)
    return tuple(targets)


def _adjust(
    solution: VrpSolution, target: _TimingTarget, delta: float
) -> VrpSolution | None:
    route_index, stop_index, field_name = target
    routes = list(solution.routes)
    route = routes[route_index]
    if stop_index is None:
        value = route.start_time + delta
        if value < 0.0:
            return None
        routes[route_index] = replace(route, start_time=value)
        return replace(solution, routes=tuple(routes))

    stops = list(route.stops)
    stop: RouteStop = stops[stop_index]
    value = getattr(stop, field_name) + delta
    if value < 0.0:
        return None
    updated_stop = replace(stop, **{field_name: value})
    if updated_stop.break_duration > updated_stop.wait:
        return None
    stops[stop_index] = updated_stop
    routes[route_index] = replace(route, stops=tuple(stops))
    return replace(solution, routes=tuple(routes))


def _draw(random_source: RandomSource) -> float:
    value = random_source.random()
    if type(value) is not float:
        raise TypeError('RandomSource.random() must return a Python float')
    if not 0.0 <= value < 1.0:
        raise ValueError('RandomSource.random() must return a float in [0, 1)')
    return value
