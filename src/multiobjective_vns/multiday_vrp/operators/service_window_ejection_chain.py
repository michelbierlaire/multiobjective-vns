"""Window-prioritized route consolidation with bounded ejection chains.

Created: 2026-11-06T13:24:40+01:00
Author: Michel Bierlaire, EPFL
"""

from __future__ import annotations

from dataclasses import replace

from multiobjective_vns.identifiers import ObjectiveId
from multiobjective_vns.multiday_vrp.evaluation.helpers import _tolerance
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
from multiobjective_vns.multiday_vrp.operators.service_window_common import (
    _append_scored,
    _Bundle,
    _bundle_from_route,
    _bundle_slack,
    _complete_base_analysis,
    _insert_bundle,
    _remove_bundle,
    _route_bundles,
    _unique_solutions,
    _UnitKey,
    _WindowStructuralOperator,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_elimination import (
    _relationship_groups,
    _service_units,
)
from multiobjective_vns.multiday_vrp.operators.vehicle_route_assignment_helpers import (
    _put_depot_pickups_first,
    _rebase_service_waits,
    _rebuild_paths,
    _ScoredPlan,
    _sort_routes,
)
from multiobjective_vns.multiday_vrp.solution import VrpSolution
from multiobjective_vns.objectives import ObjectiveDeclaration
from multiobjective_vns.records.candidates import CandidateContext, OperatorResult
from multiobjective_vns.values import require_integer

__all__ = ['WindowPrioritizedEjectionChainConsolidationOperator']


class WindowPrioritizedEjectionChainConsolidationOperator(_WindowStructuralOperator):
    """Consolidate routes with bounded ejection chains and window-first scoring."""

    __slots__ = ('_maximum_ejection_depth',)

    def __init__(
        self,
        instance: LoadedVrpInstance,
        objective_declaration: ObjectiveDeclaration,
        assess_plan: VrpPlanAssessor,
        *,
        window_objective_id: ObjectiveId,
        max_neighborhood_size: int,
        max_ejection_chain_depth: int = 3,
        objective_id: ObjectiveId | None = None,
        max_candidate_alternatives: int = 32,
        max_paths_per_leg: int = 32,
        max_arc_expansion_steps: int = 10000,
    ) -> None:
        super().__init__(
            instance,
            objective_declaration,
            assess_plan,
            window_objective_id=window_objective_id,
            objective_id=objective_id,
            max_neighborhood_size=max_neighborhood_size,
            max_candidate_alternatives=max_candidate_alternatives,
            max_paths_per_leg=max_paths_per_leg,
            max_arc_expansion_steps=max_arc_expansion_steps,
        )
        self._maximum_ejection_depth = require_integer(
            max_ejection_chain_depth,
            name='max_ejection_chain_depth',
            minimum=1,
        )

    def call(self, context: CandidateContext) -> OperatorResult:
        """Remove up to ``k`` source vehicle-day route records."""
        size = validate_neighborhood_size(context.k, self._maximum_size)
        working = require_vrp_base(context)
        if (
            context.ensure_feasible
            and self._assess(working, ensure_feasible=True) is None
        ):
            return operator_result(None, modifications=0, requested_size=size)
        modifications = 0
        for _ in range(size):
            step_context = (
                context
                if modifications == 0
                else replace(context, base=working, base_evaluation=None)
            )
            candidates = self._consolidation_candidates(working, step_context)
            if not candidates:
                break
            working, _values = self._choose(candidates, step_context)
            modifications += 1
        return operator_result(
            working if modifications else None,
            modifications=modifications,
            requested_size=size,
        )

    def _consolidation_candidates(
        self,
        base: VrpSolution,
        context: CandidateContext,
    ) -> tuple[_ScoredPlan, ...]:
        units_by_route: dict[int, list[tuple[_UnitKey, ...]]] = {}
        ineligible_routes: set[int] = set()
        for group in _relationship_groups(self._instance, _service_units(base)):
            route_indices = {
                route_index
                for unit in group
                for route_index, _stop_index, _stop in unit.occurrences
            }
            if len(route_indices) != 1:
                ineligible_routes.update(route_indices)
                continue
            route_index = next(iter(route_indices))
            units_by_route.setdefault(route_index, []).append(
                tuple(unit.key for unit in group)
            )
        source_indices = tuple(
            index
            for index in range(len(base.routes))
            if units_by_route.get(index) and index not in ineligible_routes
        )
        sampled_sources = randomized_sample(
            source_indices, self._candidate_limit, context.random_source
        )
        partial_limit = min(self._candidate_limit, context.max_candidates)
        candidates: list[_ScoredPlan] = []
        for source_position, source_index in enumerate(sampled_sources):
            remaining_source_count = len(sampled_sources) - source_position - 1
            source_candidate_budget = (
                self._candidate_limit - len(candidates) - remaining_source_count
            )
            source_candidate_start = len(candidates)
            source_base = base
            units = tuple(
                _bundle_from_route(base.routes[source_index], keys)
                for keys in units_by_route[source_index]
            )
            receivers = tuple(
                index
                for index, route in enumerate(base.routes)
                if index != source_index
                and route.day_index == base.routes[source_index].day_index
                and route.vehicle_id != base.routes[source_index].vehicle_id
            )
            if not units or not receivers:
                continue
            partial: list[tuple[VrpSolution, tuple[_Bundle, ...]]] = [(base, units)]
            complete: list[VrpSolution] = []
            while partial:
                next_partial: list[tuple[VrpSolution, tuple[_Bundle, ...]]] = []
                for plan, pending_units in partial:
                    if not pending_units:
                        complete.append(plan)
                        continue
                    ordered_units = self._order_source_units(
                        plan,
                        source_index,
                        pending_units,
                        receivers,
                        context,
                        reference_base=source_base,
                    )
                    unit = ordered_units[0]
                    remaining_units = tuple(
                        pending
                        for pending in pending_units
                        if pending.keys != unit.keys
                    )
                    for alternative in self._place_unit_with_ejections(
                        plan,
                        unit,
                        source_index,
                        receivers,
                        context,
                    ):
                        if any(
                            previous == alternative
                            and tuple(item.keys for item in previous_pending)
                            == tuple(item.keys for item in remaining_units)
                            for previous, previous_pending in next_partial
                        ):
                            continue
                        next_partial.append((alternative, remaining_units))
                        if len(next_partial) >= partial_limit:
                            break
                    if len(next_partial) >= partial_limit:
                        break
                partial = next_partial
            for plan in complete:
                routes = list(plan.routes)
                del routes[source_index]
                candidate = _sort_routes(
                    replace(plan, routes=tuple(routes)), self._instance
                )
                with_paths = _rebuild_paths(
                    self._instance,
                    base,
                    candidate,
                    context,
                    max_paths=self._path_limit,
                    max_expansions=self._expansion_limit,
                    path_search_index=self._path_search_index,
                )
                if with_paths is None:
                    continue
                with_waits = _rebase_service_waits(
                    self._instance,
                    self._objectives,
                    base,
                    with_paths,
                    context,
                    self._assess_plan,
                )
                values = self._assess(
                    with_waits,
                    ensure_feasible=context.ensure_feasible,
                )
                if values is not None:
                    _append_scored(candidates, with_waits, values)
                    if (
                        len(candidates) - source_candidate_start
                        >= source_candidate_budget
                    ):
                        break
        return tuple(candidates)

    def _order_source_units(
        self,
        base: VrpSolution,
        source_index: int,
        units: tuple['_Bundle', ...],
        receivers: tuple[int, ...],
        context: CandidateContext,
        *,
        reference_base: VrpSolution | None = None,
    ) -> tuple['_Bundle', ...]:
        analysis = _complete_base_analysis(context)
        if analysis is None:
            return randomized_order(units, context.random_source)
        slack_base = base if reference_base is None else reference_base
        counts = tuple(
            (
                unit,
                self._count_direct_feasible_placements(
                    base,
                    source_index,
                    unit,
                    receivers,
                    context,
                    path_base=slack_base,
                ),
                _bundle_slack(
                    self._instance,
                    slack_base,
                    source_index,
                    unit,
                    context,
                    analysis,
                    unknown_if_incomplete=True,
                ),
            )
            for unit in units
        )
        ordered: list[_Bundle] = []
        remaining = list(counts)
        while remaining:
            minimum_count = min(item[1] for item in remaining)
            count_ties = [item for item in remaining if item[1] == minimum_count]
            known_slack = [item[2] for item in count_ties if item[2] is not None]
            if known_slack:
                minimum_slack = min(known_slack)
                slack_ties = [
                    item
                    for item in count_ties
                    if item[2] is None
                    or abs(item[2] - minimum_slack)
                    <= _tolerance(item[2], minimum_slack)
                ]
            else:
                slack_ties = count_ties
            selected = choose_uniformly(slack_ties, context.random_source)
            ordered.append(selected[0])
            remaining.remove(selected)
        return tuple(ordered)

    def _count_direct_feasible_placements(
        self,
        base: VrpSolution,
        source_index: int,
        bundle: '_Bundle',
        receivers: tuple[int, ...],
        context: CandidateContext,
        *,
        path_base: VrpSolution | None = None,
    ) -> int:
        origin = _remove_bundle(base, source_index, bundle.keys)
        if origin is None:
            return 0
        placements: list[tuple[int, int]] = []
        for destination_index in receivers:
            destination = origin.routes[destination_index]
            for position in range(len(destination.stops) + 1):
                placements.append((destination_index, position))
        sampled = randomized_sample(
            placements,
            min(self._candidate_limit, context.max_candidates),
            context.random_source,
        )
        feasible_count = 0
        path_source = base if path_base is None else path_base
        for destination_index, position in sampled:
            destination = origin.routes[destination_index]
            stops = _insert_bundle(destination.stops, bundle.stops, position)
            stops = _put_depot_pickups_first(self._instance, origin, destination, stops)
            routes = list(origin.routes)
            routes[destination_index] = replace(destination, stops=stops, legs=())
            proposal = replace(origin, routes=tuple(routes))
            candidate = _sort_routes(proposal, self._instance)
            with_paths = _rebuild_paths(
                self._instance,
                path_source,
                candidate,
                context,
                max_paths=1,
                max_expansions=self._expansion_limit,
                path_search_index=self._path_search_index,
            )
            if (
                with_paths is not None
                and self._assess(
                    with_paths,
                    ensure_feasible=context.ensure_feasible,
                )
                is not None
            ):
                feasible_count += 1
        return feasible_count

    def _place_unit_with_ejections(
        self,
        plan: VrpSolution,
        bundle: '_Bundle',
        source_index: int | None,
        receivers: tuple[int, ...],
        context: CandidateContext,
    ) -> tuple[VrpSolution, ...]:
        proposal_count = 0
        completed: list[VrpSolution] = []

        def visit(
            current: VrpSolution,
            pending: _Bundle,
            origin_index: int | None,
            depth: int,
            visited_routes: frozenset[int],
            chain_keys: frozenset[_UnitKey],
        ) -> None:
            nonlocal proposal_count
            for destination_index in receivers:
                if proposal_count >= self._candidate_limit:
                    break
                if destination_index in visited_routes:
                    continue
                current_without_pending = (
                    current
                    if origin_index is None
                    else _remove_bundle(current, origin_index, pending.keys)
                )
                if current_without_pending is None:
                    continue
                destination = current_without_pending.routes[destination_index]
                positions = tuple(range(len(destination.stops) + 1))
                for position in randomized_sample(
                    positions,
                    self._candidate_limit - proposal_count,
                    context.random_source,
                ):
                    if proposal_count >= self._candidate_limit:
                        break
                    proposal_count += 1
                    inserted_stops = _insert_bundle(
                        destination.stops, pending.stops, position
                    )
                    inserted_stops = _put_depot_pickups_first(
                        self._instance,
                        current_without_pending,
                        destination,
                        inserted_stops,
                    )
                    routes = list(current_without_pending.routes)
                    routes[destination_index] = replace(
                        destination, stops=inserted_stops, legs=()
                    )
                    inserted_plan = replace(
                        current_without_pending, routes=tuple(routes)
                    )
                    completed.append(inserted_plan)

                    if (
                        depth >= self._maximum_ejection_depth
                        or proposal_count >= self._candidate_limit
                    ):
                        continue
                    ejectable = tuple(
                        candidate_bundle
                        for candidate_bundle in _route_bundles(
                            self._instance, inserted_plan, destination_index
                        )
                        if candidate_bundle.keys.isdisjoint(chain_keys | pending.keys)
                    )
                    for displaced in randomized_sample(
                        ejectable,
                        self._candidate_limit - proposal_count,
                        context.random_source,
                    ):
                        if proposal_count >= self._candidate_limit:
                            break
                        displaced_plan = _remove_bundle(
                            inserted_plan,
                            destination_index,
                            displaced.keys,
                        )
                        if displaced_plan is None:
                            continue
                        visit(
                            displaced_plan,
                            displaced,
                            None,
                            depth + 1,
                            visited_routes | {destination_index},
                            chain_keys | pending.keys | displaced.keys,
                        )
                        if proposal_count >= self._candidate_limit:
                            break

        visit(
            plan,
            bundle,
            source_index,
            0,
            frozenset(),
            frozenset(),
        )
        return tuple(_unique_solutions(completed)[: self._candidate_limit])
