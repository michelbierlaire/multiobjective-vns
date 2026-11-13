# Multi-day VRP Operator catalog

## 1. Purpose and scope

This document organizes Operators by the problem features they address. The
VRP application provides a default catalog factory that creates a reusable
Operator set for every instance and adds feature-specific Operators when their
features are active. Applications can customize the returned registrations
before passing them to the generic search package.

The problem model is defined in
[multiday_vrp.md](multiday_vrp.md), the authoritative solution records in
[multiday_vrp_solution_design.md](multiday_vrp_solution_design.md), and the
input-driven feature activation rules in
[multiday_vrp_input_format.md](multiday_vrp_input_format.md) §3.4. This catalog
covers move behavior, neighborhood size k, modification count m, and useful
CandidateContext information. The factory and these Operators belong to the
VRP application; the generic multiobjective-vns package receives the
resulting `OperatorRegistration` and `OperatorCompositionRegistration`
values.

The move families draw on established VRP neighborhoods, including 2-opt,
relocate, Or-opt, exchange, 2-opt*, SWAP*, and large-neighborhood
destroy-and-repair search. The literature references appear in §6.

### 1.1 Default catalog factory

`build_default_vrp_operator_catalog` converts a validated VRP instance into
generic search registrations:

```text
build_default_vrp_operator_catalog(
    instance: LoadedVrpInstance,
    *,
    problem_instance_id: ProblemInstanceId,
    objective_declaration: ObjectiveDeclaration,
    assess_plan: VrpPlanAssessor,
    additive_path_costs: Mapping[ObjectiveId, AdditivePathCostRegistration] | None = None,
    settings: VrpOperatorCatalogSettings = VrpOperatorCatalogSettings(),
) -> VrpOperatorCatalog
```

`VrpOperatorCatalog` is an immutable record with
`operators: tuple[OperatorRegistration, ...]` and
`pre_registered_compositions: tuple[OperatorCompositionRegistration, ...]`.
The first field contains ordinary Operators from §1.2; the second contains
the initial standalone composite Operators specified in §1.3. The application
passes both fields to the matching `MultiobjectiveVNS` constructor arguments.

`instance` supplies the normalized application data and its derived
`VrpFeatureProfile`; its public record fields are defined in
[`multiday_vrp_input_format.md`](multiday_vrp_input_format.md) §3.5.
`objective_declaration` supplies the ordered objective
IDs and comparison functions. `problem_instance_id` is the same stable
instance identity passed to `MultiobjectiveVNS`; it identifies the captured
instance data in each generated Operator's checkpoint configuration.
`VrpPlanAssessor` is the application's shared, pure plan-assessment helper,
with the callable contract:

```text
assess_plan(
    instance: LoadedVrpInstance,
    objective_declaration: ObjectiveDeclaration,
    solution: VrpSolution,
) -> ApplicationEvaluation
```

`additive_path_costs` is an optional registry for objective-specific exact
path search. Each key is a declared `ObjectiveId`; its value is an immutable
registration:

```text
AdditivePathCostRegistration(
    provider_id: nonempty stable string,
    configuration_id: nonempty stable string,
    callback: AdditivePathCostCallback,
)
```

`provider_id` identifies the callback implementation and version, and
`configuration_id` identifies its captured settings and data. Both remain
stable for the lifetime of the registration and follow the stable textual ID
contract. The factory copies the mapping, validates every key against
`objective_declaration`, and raises `ValueError` for an undeclared objective
or a malformed registration before returning Operators. An omitted registry
means no objective has a registered additive path-cost callback. For a
registered objective, the callback contract and additivity guarantee in §2.2
apply; the factory uses that callback in its objective-specific
shortest-path Operator and captures it there as an Operator dependency. The
generic search package receives the resulting Operator registration. Other
objective-specific route Operators continue to use their stated
path-selection rule.

The factory binds the supplied instance and objective declaration when it
creates Operators. The `ApplicationEvaluator` delegates to the same helper
with those same values, so Operators use the instance's declared
representation, feasibility, and objective rules when screening alternatives.

`VrpOperatorCatalogSettings` is immutable and has these fields:

```text
VrpOperatorCatalogSettings(
    max_neighborhood_size: positive integer = 5,
    time_step: positive FiniteReal = 1.0,
    objective_targets: tuple[ObjectiveId, ...] | None = None,
    max_candidate_alternatives: positive integer = 32,
    max_ejection_chain_depth: positive integer = 3,
    max_paths_per_leg: positive integer = 32,
    max_arc_expansion_steps: positive integer = 10000,
)
```

The default factory uses the package-owned, stable
`VrpOperatorSpecId` strings listed in §1.2. Each identifies one fixed move
specification across factory calls. Application-defined Operators use
application-owned stable specification IDs. The factory creates
`VrpOperatorId(operator_spec_id, objective_id)`, an immutable value with those
two fields; `objective_id` is `None` for an unranked or objective-independent
registration. Both ID types use the generic package's stable,
collision-free textual ID contract.

| Field | Default and meaning |
|---|---|
| `max_neighborhood_size` | Positive integer, default `5`; assigned to every generated `OperatorRegistration`. |
| `time_step` | Positive `FiniteReal`, default `1.0` in the `ScenarioBank` time unit; used by Operators whose definitions require a time step. |
| `objective_targets` | A tuple of `ObjectiveId` values or `None`. `None` selects every objective in declaration order; a tuple selects a unique subset in its supplied order, and an empty tuple selects no objective-targeted variants. |
| `max_candidate_alternatives` | Positive integer in `1..32`, default `32`; limits complete internal plan alternatives inspected for one target or move attempt. The generic `MultiobjectiveVNSConfig.max_candidates_per_attempt` separately caps the ordered candidates returned to the engine. |
| `max_ejection_chain_depth` | Positive integer, default `3`; limits the number of already placed repair units displaced in one route-consolidation ejection chain. |
| `max_paths_per_leg` | Positive integer in `1..32`, default `32`; limits distinct paths returned by the bounded sampler for one `RouteLeg` endpoint pair. |
| `max_arc_expansion_steps` | Positive integer in `1..10000`, default `10000`; limits arc-expansion steps in one bounded path search for a `RouteLeg`. |

The selected objective IDs must be unique members of
`objective_declaration`; an unknown or repeated ID raises `ValueError` before
registrations are returned. The integer settings use built-in Python `int`
values with `bool` rejected and obey the displayed positive ranges.
`time_step` is validated under the generic `FiniteReal` contract.

Each Operator specification has one target mode: **independent**,
**objective-targeted**, or **unranked and objective-targeted**. Section 1.2
assigns the mode, stable specification ID, feature gate, and applicable
objectives for every default-catalog specification. An independent
specification produces one registration with `objective_id=None`. An
objective-targeted specification produces one registration for each selected,
applicable objective. An unranked-and-objective-targeted specification
produces one registration with `objective_id=None` and one for each selected,
applicable objective. The `objective_targets` setting filters only the
objective-targeted variants; an empty tuple therefore leaves unranked variants
available. The factory registers rows in §1.2 order, with objective variants
following the selected objective order. It includes a feature-specific row
when its stated feature gate and any stated objective-declaration condition
are satisfied. Each generated
registration receives the
`feasibility_restoration` value from the catalog matrix in §5 and the
modification-count scale stated in its Operator definition. Objective-targeted
variants of one specification share its restoration flag. The
factory-generated Operators are stateless in version 1 and use
`initial_state=None`.

The factory assigns stable, unique Operator IDs from each catalog
specification and its optional objective target. Each generated
`configuration_id` identifies the factory version, specification, optional
objective target, settings that affect that registration, generated
registration values, and `problem_instance_id` for captured application
data. It includes `max_candidate_alternatives` for every generated Operator and
`max_ejection_chain_depth` for the ejection-chain route-consolidation
Operator.
For an Operator that uses the built-in path sampler, it also includes
`max_paths_per_leg` and `max_arc_expansion_steps`. IDs and registration order
are deterministic for fixed inputs; changing a limit included in an Operator's
configuration ID changes that ID. The factory captures each applicable limit
in the generated Operator, where it remains fixed for that registration. For
the coordinate strip-sweep Operator, the configuration identity also covers
its fixed sweep-geometry specification, including orientations, width
multipliers, lateral offsets, and degenerate-geometry rules; changing that
specification requires a factory-version change.
For an objective-specific shortest-path Operator, its configuration ID also
includes the `provider_id` and `configuration_id` from the matching
`additive_path_costs` entry, or records that no such entry was supplied and
the sampled-path fallback applies. Changing or removing the callback
registration therefore changes the generated Operator configuration ID.
The returned `VrpOperatorCatalog` is ready for
`MultiobjectiveVNS(operators=..., pre_registered_compositions=...)`. The
application may remove registrations, replace a registration with revised
settings and a corresponding new `configuration_id`, or append custom
registrations before constructing the search. `OperatorGroup` values are a
separate input for dynamic-composition
preferences and remain application supplied. Under `performance_matching`,
group membership influences selection among eligible compositions after the
generic engine has selected the composition category; the category probability
is part of `MultiobjectiveVNSConfig`.

`max_neighborhood_size` in the factory settings is each Operator's registered
supported-size maximum. `MultiobjectiveVNSConfig.max_neighborhood_size` is a
separate run-level cap that can reduce those maxima; its `None` default uses
the largest maximum in the factory result. A caller that needs a larger or
different per-Operator range changes the relevant registration before search.

### 1.2 Default catalog specification registry

This table is the authoritative expansion map for the default factory. Each
row defines one fixed specification and its package-owned ID. **Independent**
rows create one `VrpOperatorId` with `objective_id=None`. **Objective-targeted**
rows create one ID for each applicable objective selected by
`objective_targets`. **Unranked + objective-targeted** rows create the
`objective_id=None` registration plus one registration per applicable
selected objective. For independent rows, “all objectives” means the Operator
may be used while optimizing the complete declared vector, without targeting
one component. For Pareto rows, the Operator explicitly uses all components
together.

Rows appear in factory registration order. Within a row that expands to
objective-specific registrations, the selected `ObjectiveId` order is used.
Feature-gated rows are included when the listed `VrpFeatureId` conditions hold.
The restoration flag for each specification comes from its Operator family
row in §5; variants of one family share that flag. A gate joined by `and`
requires every listed feature to be active. A gate written as `Any of {…}`
requires at least one listed feature to be active. A composite is included
only when every component specification is registered by this table and its
own feature conditions are satisfied.

| `operator_spec_id` | Operator specification | Target mode | Feature gate | Applicable objectives |
|---|---|---|---|---|
| `vrp.generic.two_opt` | 2-opt | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.relocate` | Relocate | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.or_opt_chain_relocation` | Or-opt chain relocation | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.exchange` | Exchange | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.two_opt_star_suffix_exchange` | 2-opt* suffix exchange | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.swap_star_free_insertion_exchange` | SWAP* free-insertion exchange | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.vehicle_route_consolidation_best_insertion` | Vehicle-route consolidation, best insertion | Unranked + objective-targeted | Any of `variable_active_vehicle_count`, `multi_day_planning` | All declared objectives |
| `vrp.generic.vehicle_route_consolidation_regret_insertion` | Vehicle-route consolidation, regret insertion | Unranked + objective-targeted | Any of `variable_active_vehicle_count`, `multi_day_planning` | All declared objectives |
| `vrp.generic.vehicle_elimination_best_insertion` | Vehicle elimination, best insertion | Unranked + objective-targeted | `variable_active_vehicle_count` | All declared objectives |
| `vrp.generic.vehicle_elimination_regret_insertion` | Vehicle elimination, regret insertion | Unranked + objective-targeted | `variable_active_vehicle_count` | All declared objectives |
| `vrp.generic.vehicle_route_split` | Vehicle-route split | Unranked + objective-targeted | Any of `variable_active_vehicle_count`, `multi_day_planning` | All declared objectives |
| `vrp.generic.route_leg_reroute` | Route-leg reroute | Unranked + objective-targeted | Core, always active | All declared objectives |
| `vrp.generic.objective_specific_shortest_path` | Objective-specific shortest-path Operator | Objective-targeted | Core, always active | All declared objectives; additive callback use or sampled-path fallback follows §2.2 |
| `vrp.generic.timing_adjustment` | Timing adjustment | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.driver_reassignment` | Driver reassignment | Independent | `driver_assignment_differentiation` | All objectives; no single-objective target |
| `vrp.time_dependent.departure_time_profile_adjustment` | Departure-time profile adjustment | Objective-targeted | `within_day_time_dependent_travel` | All declared objectives |
| `vrp.scenario_uncertainty.differential_reroute` | Scenario-differential reroute | Objective-targeted | `scenario_uncertainty` | All declared objectives |
| `vrp.scenario_uncertainty.differential_timing_adjustment` | Scenario-differential timing adjustment | Objective-targeted | `scenario_uncertainty` | All declared objectives |
| `vrp.network_events.event_objective_route_reroute` | Event-objective route reroute | Objective-targeted | `network_event_exposure` | Objectives using `event_count` or `maximum_event_exposure` with a nonempty event-type group |
| `vrp.multi_day.driver_swap_or_cycle` | Driver swap or cycle | Independent | `multi_day_planning` | All objectives; no single-objective target |
| `vrp.multi_day.driver_schedule_destroy_and_repair` | Driver schedule destroy-and-repair | Unranked + objective-targeted | `multi_day_planning` | All declared objectives |
| `vrp.overnight_depot.overnight_depot_change` | Overnight-depot change | Unranked + objective-targeted | `overnight_depot_choice` | All declared objectives |
| `vrp.multi_day.multi_day_depot_reassignment` | Multi-day depot reassignment | Unranked + objective-targeted | `multi_day_planning` and `overnight_depot_choice` | All declared objectives |
| `vrp.multiple_tours.split_tour` | Split tour | Unranked + objective-targeted | `multiple_tours_per_day` | All declared objectives |
| `vrp.multiple_tours.merge_tours` | Merge tours | Unranked + objective-targeted | `multiple_tours_per_day` | All declared objectives |
| `vrp.depot_visits.depot_visit_insertion` | Depot-visit insertion | Unranked + objective-targeted | Any of `multiple_tours_per_day`, `tool_capacity_reservations`, or `work_driving_rest_hos_rules` | All declared objectives |
| `vrp.depot_visits.depot_visit_removal` | Depot-visit removal | Unranked + objective-targeted | Any of `multiple_tours_per_day`, `tool_capacity_reservations`, or `work_driving_rest_hos_rules` | All declared objectives |
| `vrp.vehicle_availability.guided_timing_adjustment` | Availability-guided timing adjustment | Objective-targeted | `restricted_vehicle_availability` | Objectives bound to `vehicle_availability` |
| `vrp.vehicle_availability.guided_stop_relocation` | Availability-guided stop relocation | Unranked + objective-targeted | `restricted_vehicle_availability` | Objectives bound to `vehicle_availability` |
| `vrp.cargo_tool_capacity.capacity_aware_package_chain_relocation` | Capacity-aware package-chain relocation | Unranked + objective-targeted | `potentially_binding_cargo_tool_capacity` | All declared objectives |
| `vrp.personnel_seat_capacity.seat_aware_intervention_team_reassignment` | Seat-aware intervention-team reassignment | Unranked + objective-targeted | `potentially_binding_personnel_seat_capacity` | All declared objectives |
| `vrp.package_compatibility.compatibility_guided_package_chain_repair` | Compatibility-guided package-chain repair | Unranked + objective-targeted | `package_vehicle_compatibility` | All declared objectives |
| `vrp.package_transfer.add_transfer_step` | Transfer-step change, `add` mode | Unranked + objective-targeted | `package_transfer_operations` | All declared objectives |
| `vrp.package_transfer.remove_transfer_step` | Transfer-step change, `remove` mode | Unranked + objective-targeted | `package_transfer_operations` | All declared objectives |
| `vrp.package_transfer.reassign_transfer_step` | Transfer-step change, `reassign` mode | Unranked + objective-targeted | `package_transfer_operations` | All declared objectives |
| `vrp.transfer_storage.storage_aware_transfer_assignment` | Storage-aware transfer assignment | Unranked + objective-targeted | `transfer_facility_storage` and `package_transfer_operations` | All declared objectives |
| `vrp.package_requests.package_chain_destroy_and_repair` | Package-chain destroy-and-repair | Unranked + objective-targeted | `package_pickup_delivery` | All declared objectives |
| `vrp.positive_service_duration.service_order_impact_repair` | Service-order impact repair | Objective-targeted | `positive_service_durations` | Dedicated time-window-violation objective only |
| `vrp.positive_service_duration.duration_impact_timing_adjustment` | Duration-impact timing adjustment | Objective-targeted | `positive_service_durations` | All declared objectives |
| `vrp.optional_service_omission.service_coverage_change` | Service-coverage change | Independent | `optional_service_omission` | All objectives; no single-objective target |
| `vrp.service_windows.window_compatible_package_cluster_reassignment` | Window-compatible package-cluster reassignment | Unranked + objective-targeted | `restrictive_service_windows`; a move requires at least two active package-serving vehicles | All declared objectives |
| `vrp.service_windows.spatial_time_window_route_cluster_rebuild` | Spatial/time-window route-cluster rebuild | Unranked + objective-targeted | `restrictive_service_windows` and `package_pickup_delivery`; runtime requires at least two active vehicles | All declared objectives |
| `vrp.spatial.daganzo_strip_sweep_route_rebuild` | Daganzo-inspired coordinate strip-sweep route rebuild | Unranked + objective-targeted | `planar_node_coordinates` and `package_pickup_delivery`; runtime requires eligible same-day routes sharing a start-depot node | All declared objectives |
| `vrp.service_windows.service_wait_adjustment` | Service-wait adjustment | Objective-targeted | `restrictive_service_windows` | Dedicated time-window-violation objective only |
| `vrp.service_windows.route_start_shift` | Route-start shift | Objective-targeted | `restrictive_service_windows` | Dedicated time-window-violation objective only |
| `vrp.service_windows.schedule_compliance_repair` | Time-window schedule compliance repair | Objective-targeted | `restrictive_service_windows` | Dedicated time-window-violation objective only |
| `vrp.service_windows.activity_relocation` | Time-window-guided activity relocation | Objective-targeted | `restrictive_service_windows` | Dedicated time-window-violation objective only |
| `vrp.service_windows.route_leg_rerouting` | Time-window-guided route-leg rerouting | Objective-targeted | `restrictive_service_windows` | Dedicated time-window-violation objective only |
| `vrp.service_windows.destroy_and_repair` | Time-window-focused destroy-and-repair | Objective-targeted | `restrictive_service_windows` | Dedicated time-window-violation objective only |
| `vrp.service_windows.regret_based_reinsertion` | Time-window regret-based service reinsertion | Objective-targeted | `restrictive_service_windows` | Dedicated time-window-violation objective only |
| `vrp.service_windows.short_tour_regret_insertion` | Objective-guided feasible-tour regret reinsertion | Objective-targeted | `restrictive_service_windows` and `package_pickup_delivery` | Selected objectives other than the dedicated time-window-violation objective |
| `vrp.service_windows.backtracking_tour_insertion` | Objective-guided backtracking tour construction | Objective-targeted | `restrictive_service_windows` and `package_pickup_delivery` | Selected objectives other than the dedicated time-window-violation objective |
| `vrp.service_windows.window_feasible_delivery_edge_exchange` | Objective-guided feasible delivery-order edge exchange | Objective-targeted | `restrictive_service_windows` and `package_pickup_delivery` | Selected objectives other than the dedicated time-window-violation objective |
| `vrp.service_windows.slack_guided_or_opt_relocation` | Slack-guided Or-opt chain relocation | Unranked + objective-targeted | `restrictive_service_windows` and a declared time-window-violation objective | All declared objectives |
| `vrp.service_windows.window_feasible_swap_star` | Window-prioritized SWAP* free-insertion exchange | Unranked + objective-targeted | `restrictive_service_windows` and a declared time-window-violation objective | All declared objectives |
| `vrp.service_windows.ejection_chain_route_consolidation` | Window-prioritized ejection-chain route consolidation | Unranked + objective-targeted | `restrictive_service_windows` and a declared time-window-violation objective | All declared objectives |
| `vrp.interventions.intervention_relocation` | Intervention relocation | Unranked + objective-targeted | `interventions` | All declared objectives |
| `vrp.tool_capacity.tool_reservation_guided_intervention_relocation` | Tool-reservation-guided intervention relocation | Unranked + objective-targeted | `tool_capacity_reservations` | All declared objectives |
| `vrp.activity_relationships.relationship_guided_service_repair` | Relationship-guided service repair | Unranked + objective-targeted | `additional_activity_relationships` | All declared objectives |
| `vrp.driving_skills.skill_guided_driver_schedule_repair` | Skill-guided driver schedule repair | Unranked + objective-targeted | `driving_skill_restrictions` | All declared objectives |
| `vrp.intervention_qualifications.intervention_team_reassignment` | Intervention-team reassignment | Unranked + objective-targeted | `intervention_qualifications_or_team_size` | All declared objectives |
| `vrp.personnel_availability.personnel_assignment_repair` | Personnel-assignment repair | Objective-targeted | `restricted_personnel_availability` and `personnel_availability_differentiation` | Objectives bound to `personnel_availability` |
| `vrp.personnel_availability.personnel_calendar_timing_adjustment` | Personnel-calendar timing adjustment | Objective-targeted | `restricted_personnel_availability` | Objectives bound to `personnel_availability` |
| `vrp.staff_depot_continuity.staff_continuity_schedule_repair` | Staff-continuity schedule repair | Unranked + objective-targeted | `staff_depot_continuity` | All declared objectives |
| `vrp.work_rules.violation_guided_assignment_repair` | Violation-guided assignment repair | Objective-targeted | `work_driving_rest_hos_rules` | All declared objectives |
| `vrp.work_rules.violation_guided_timing_repair` | Violation-guided timing repair | Objective-targeted | `work_driving_rest_hos_rules` | All declared objectives |
| `vrp.multiple_objectives.objective_targeted_activity_relocation` | Objective-targeted activity relocation | Objective-targeted | `multiple_objectives` | All declared objectives |
| `vrp.multiple_objectives.pareto_tradeoff_path` | Pareto trade-off route move, `path` family | Independent | `multiple_objectives` | All objectives jointly |
| `vrp.multiple_objectives.pareto_tradeoff_timing` | Pareto trade-off route move, `timing` family | Independent | `multiple_objectives` | All objectives jointly |
| `vrp.multiple_objectives.pareto_tradeoff_activity` | Pareto trade-off route move, `activity` family | Independent | `multiple_objectives` | All objectives jointly |
| `vrp.scenario_aggregation.aggregation_contribution_path_repair` | Aggregation-contribution route repair, `path` family | Objective-targeted | `scenario_aggregation` | All declared objectives |
| `vrp.scenario_aggregation.aggregation_contribution_timing_repair` | Aggregation-contribution route repair, `timing` family | Objective-targeted | `scenario_aggregation` | All declared objectives |

The three transfer-step modes and the `path`, `timing`, and `activity`
families of the Pareto trade-off Operator are separate specifications because
each fixes a distinct move rule or neighborhood. The `path` and `timing`
families of aggregation-contribution repair are also separate specifications.
Split tour and merge tours are separate specifications because they change
tour boundaries in opposite directions and have distinct eligibility rules.
Vehicle-route consolidation, vehicle-route split, and vehicle-elimination
destroy-and-repair are separate generic families because they define distinct
changes to work assignments and active vehicle use. The
window-prioritized ejection-chain consolidation is an additional,
feature-gated family with its own displacement bound and feedback identity.
Each consolidation and
vehicle-elimination family has separate best-insertion and regret-insertion
specifications, so their success statistics and adaptive selection remain
distinct. Consolidation rebuilds one route by moving its repair units into a
route assigned to another vehicle; split partitions one route; vehicle
elimination redistributes one vehicle's service units across routes assigned
to other vehicles before removing its routes. These vehicle-assignment
Operators are available independently of
`multiple_tours_per_day`; the multiple-tour Operators continue to change tour
boundaries within one vehicle-day route.
Their IDs therefore remain distinct in `OperatorGroup`, feedback statistics,
and checkpoint provenance. Their configuration IDs include the row's fixed
variant and all settings that affect its behavior.

The three objective-guided tour-search specifications are registered once for
each selected objective target other than the dedicated time-window-violation
objective. Their shared feasibility and ranking contract is specified in
§4.16.

## 2. Shared Operator contract

Each call receives an immutable base `VrpSolution`, a positive integer `k`, an
`ensure_feasible` request, `max_candidates`, the Operator's registered state,
and the shared `RandomSource`. `max_candidates` is the effective limit from
the generic algorithm configuration and remaining evaluation-request budget.
The Operator returns an ordered tuple of up to that many immutable candidate
plans, distinct under the canonical identity rule in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md) §4.6.
Each candidate is built independently from the same base and has its own
modification count.
When `k` permits several move units in one
candidate, those units are applied sequentially to that candidate's private
working plan; the next candidate starts again from the supplied base. The
Operator forms a list from distinct complete alternatives generated under its
move rule and internal sampling limit, applies structural and requested
feasibility filters, then orders the qualifying plans. Objective-targeted
Operators order candidates by the ranking rule stated for that Operator,
breaking remaining ties with the shared random source; unranked Operators
return candidates in randomized generation order. Randomized target
selection, alternative sampling, and ordering use the shared
`RandomSource`.

Every returned candidate is representation-valid under
multiday_vrp_solution_design.md §6. With `ensure_feasible=True`, every
returned plan satisfies every hard requirement in every scenario. With
`ensure_feasible=False`, a returned plan may be operationally infeasible
while retaining a representation-valid solution structure. The engine
resolves each submitted candidate's identity before evaluation under the
application contract.

The instance's `PickupSymmetryGroup` declarations define canonical identity
as specified in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md) §4.6.
The VRP identity callback and the default catalog use that same canonical
identity form.
The engine applies that rule to every final candidate before evaluation, so a
proposal that differs from a registered solution only by a qualifying pickup
sub-block permutation reuses the existing solution record and evaluation.
Default Operators exclude moves whose only effect is a qualifying pickup
sub-block permutation and return at most one alternative per canonical
identity. In a composition,
identity is resolved for its final candidates; the intermediate plan passes
directly to the second Operator.

Each `CandidateProposal.m` is a positive integer no larger than `k`, measured
on the Operator's declared scale. Unless an Operator defines `k` as one move's
magnitude, `k` is the maximum number of its named move units that may be
applied to that proposal. An empty result means no qualifying candidate was
produced under the requested size and feasibility requirement; it makes no
claim about the full neighborhood. In the catalog below, ranking and selection
phrases define candidate-list order: a “best candidate” is the first proposal,
and further distinct eligible alternatives follow that order up to the output
limit. A uniform choice among alternatives means a uniformly randomized order
without replacement, truncated at that limit. Each Operator below defines its own unit,
so `m` values across Operators are not directly comparable. When a feature
offers variants with different move units, register each variant as a separate
Operator with one fixed `k` and `m` scale.

A move may update multiple solution fields, including stop order, `RouteLeg`
paths, transfer references, and assignments. m counts the named move units,
not the number of fields written. Each Operator preserves unaffected plan data
and updates all dependent references required for a representation-valid
`VrpSolution`.

When stop order changes, the Operator rebuilds the aligned `RouteLeg` sequence.
Every resulting leg is a directed walk between its new endpoints. Route
timing, package custody, tool reservations, personnel, and objective values
are assessed from the resulting plan and instance according to the problem
model.

`CandidateContext.base_evaluation.analysis` may guide target selection when
the application exposes relevant analysis. It supplements the base plan and
instance data available to the Operator. Randomized move selection and
tie-breaking use `CandidateContext.random_source`.

Time-valued decisions use the solution's shared continuous time axis and the
`ScenarioBank` time unit, as specified in
multiday_vrp_solution_design.md §4.2 and multiday_vrp.md §1.1.
All real-valued plan fields and intermediate calculations use Python
binary64 `float` values. Operators use the numeric feasibility tolerance in
`multiday_vrp.md` §1 when checking candidate constraints; objective values are
compared exactly through their declared `ObjectiveFunction`.

### 1.3 Pre-registered vehicle-structure and window-repair composites

The default catalog includes three composite Operator families when
`restrictive_service_windows` is active and the dedicated time-window-
violation objective is selected. Each family is available as a standalone
choice from the first search iteration. For each selected structural objective
`o`, the factory pairs the structural Operator targeted at `o` with
`vrp.service_windows.schedule_compliance_repair` targeted at the dedicated
time-window-violation objective `o_tw`. A composite is created when both `o`
and `o_tw` are selected by `objective_targets`. The table also lists the
separate route-cluster composite, which is available when its operator and
generic 2-opt are active; it does not require a selected time-window objective.

| Composite specification ID | Ordered component specifications | Meaning |
|---|---|---|
| `vrp.composite.vehicle_route_consolidation_best_then_window_repair` | `vrp.generic.vehicle_route_consolidation_best_insertion` → `vrp.service_windows.schedule_compliance_repair` | Consolidate two vehicle routes, then repair schedule timing. |
| `vrp.composite.vehicle_route_consolidation_regret_then_window_repair` | `vrp.generic.vehicle_route_consolidation_regret_insertion` → `vrp.service_windows.schedule_compliance_repair` | Consolidate two vehicle routes by regret insertion, then repair schedule timing. |
| `vrp.composite.vehicle_route_split_then_window_repair` | `vrp.generic.vehicle_route_split` → `vrp.service_windows.schedule_compliance_repair` | Split a vehicle route across vehicles, then repair schedule timing. |
| `vrp.composite.spatial_time_window_route_cluster_rebuild_then_two_opt` | `vrp.service_windows.spatial_time_window_route_cluster_rebuild` → `vrp.generic.two_opt` | Rebuild spatial/time-window customer groups, then refine route order. |

For each of the first three rows and selected structural objective `o`, the
factory creates one `OperatorCompositionRegistration` whose `operator_id` is
`VrpOperatorId(composite_spec_id, o)`. Its first component is the structural
registration targeted at `o`; its second component is the schedule-repair
registration targeted at `o_tw`. The three rows therefore produce three
composites per selected structural objective. The source Operators remain
available as ordinary standalone choices. Their exact ordered pair is not
also generated as a dynamic-composition choice; the composite has one
standalone feedback identity and a separate size cursor, initialized to
`k=1` for each Pareto base at search start, under the generic per-base cursor
rule in the algorithm design.

For the route-cluster row, the factory registers an unranked composite and one
composite for each selected objective target. Its first component uses that
same target (or is unranked), and its second component is the independent
`vrp.generic.two_opt` registration.

The composite maximum size is the smaller component maximum, its
modification count is `min(m_first, m_second)`, and its
`feasibility_restoration` capability is inherited from the schedule-repair
component. Its configuration ID includes the ordered component IDs and
configuration IDs, the composite specification ID, both objective targets,
and problem-instance ID. The generic engine reports the composite's own
standalone statistics in `ChoiceStatistics` and identifies its ordered
components in the Operator catalog provenance. The composite can itself
participate in later dynamically selected compositions.

The composite candidate list follows the generic composition order: visit
first-component proposals in their returned order, call the second component
for each intermediate, and append its final proposals in their returned
order until the effective output limit is reached. Thus each structural
intermediate's schedule repairs are ordered by the shared window-first rule;
the outer list preserves the structural Operator's proposal order.

### 2.1 Move units, candidate trials, and feasibility

An **activity unit** is one named service activity in the instance, identified
by its `ActivityRef`, such as a package pickup, package delivery, or
intervention. A plan may contain an occurrence of that activity at a route
stop; that occurrence is identified by its `ActivityRef`, route, and stop
index. A **package-chain unit** is one package request together with its
pickup, delivery, transfer steps, and paired transfer operations. A package
chain can be edited as one atomic unit when an Operator needs to preserve
custody while changing routes. Route-order Operators use individual
`RouteStop` occurrences unless their definition names a larger unit. The term
**service unit** means an activity unit or a package-chain unit as explicitly
specified by the Operator; it is not an additional solution record. A
**route-decision unit** is one route-leg path replacement, one timing-field
change, or one activity relocation, as named by the Operator.

Each Operator registration fixes its move rule, target rule, count scale, and
configuration values. Unless a definition gives a different target rule, the
Operator selects a target uniformly from the eligible targets. It samples
complete plan alternatives without replacement up to
`VrpOperatorCatalogSettings.max_candidate_alternatives` for the relevant
target or move attempt (default `32`, configurable from `1` through `32`).
Identifiers and list positions are ordered stably before each random choice.
A candidate that fails structural checks or the requested feasibility
condition is discarded, and repeated canonical identities are skipped. The
Operator returns the qualifying distinct plans in the order defined for its
registration, stopping at `CandidateContext.max_candidates` or when its
bounded alternative set is exhausted. Unless a definition states a ranking
rule, sampled order is the candidate-list order. Any phrase such as “choose the best candidate”
specifies the first item in an ordered list; remaining qualifying alternatives
follow the same ranking, with random tie resolution. Objective-ranked
Operators return their best eligible alternatives first; unranked Operators
return eligible alternatives in uniformly randomized generation order.

`max_candidate_alternatives` counts each complete plan alternative inspected,
including an alternative rejected by structural or feasibility checks. In a
path-sampling context, “up to 32 paths” refers to
`max_paths_per_leg`, and the traversal limit refers to
`max_arc_expansion_steps`. The alternative-inspection limit also caps
complete plan alternatives formed from sampled paths, including one-leg path
replacements.
When the available path or path-assignment set exceeds that cap, the Operator
samples complete plan alternatives without replacement using the shared
random source, then applies feasibility and objective checks to those
candidates.

Path-generating Operators use the separate path-search limits in
`VrpOperatorCatalogSettings`. For each `RouteLeg` endpoint pair, the sampler
returns at most `VrpOperatorCatalogSettings.max_paths_per_leg` loopless paths
and stops after `VrpOperatorCatalogSettings.max_arc_expansion_steps` arc
expansions, whichever limit is reached first. These settings default to `32`
and `10000`, respectively; callers may lower them to positive integers. One
arc-expansion step is the consideration of one outgoing arc from a visited
node during depth-first traversal, before looplessness filtering.
`max_candidate_alternatives` also caps complete plan alternatives formed from
one or more sampled legs.

To build each candidate proposal, the Operator starts from the supplied base
and applies at most `k` effective move units sequentially. It recomputes
eligibility after each accepted unit. With `ensure_feasible=False`, each
retained intermediate plan must be representation-valid. With
`ensure_feasible=True` and a feasible base, each retained intermediate plan
must also satisfy all hard requirements in every scenario. An infeasible base
with `ensure_feasible=True` is handled by an Operator registered with
`feasibility_restoration=True`; each returned candidate is the result of a
complete repair sequence and is assessed before inclusion in the result list.

An internal partial plan is a temporary construction state used only inside an
Operator call. Destroy-and-repair Operators may temporarily remove activities
and their dependent records, but they return only a complete, representation-
valid `VrpSolution`. A repair beam may also retain complete plans in which all
required service remains assigned while some repair units still await
relocation. The application uses one shared pure plan-assessment helper for the
`ApplicationEvaluator` and Operators that need feasibility or objective
screening; Operators may use it to rank these complete construction states.
Those internal assessments create no registry records, outcomes, feedback, or
engine candidate submissions. The helper applies the model rules in
`multiday_vrp.md` and the same objective declarations. A genuinely incomplete
plan with unassigned required service is not assessed. The engine submits and
records every final candidate proposal in the Operator result. A completed
trial that fails the requested feasibility guarantee is discarded. If no
distinct alternative retains an effective move, the Operator returns an empty
result. If a proposal retains fewer than `k` units, its `m` reports that count
under the Operator's declared scale.

An objective-ranked Operator evaluates at most
`VrpOperatorCatalogSettings.max_candidate_alternatives` eligible alternatives
for one target, orders them by the registered `ObjectiveFunction`, and breaks
exact objective ties uniformly with the shared random source. It returns up to
`CandidateContext.max_candidates` of the best distinct alternatives. A
multiobjective Operator uses the Pareto rule explicitly defined for that
Operator. A registration that uses objective ranking must name its
`ObjectiveId`; Operators without objective ranking return their qualifying
candidate list in uniformly randomized order.

### 2.2 Building directed route paths

Every route-order Operator that changes consecutive stop endpoints rebuilds
the affected `RouteLeg` values with directed loopless paths. For a path from
node `u` to node `v`, a loopless path is a directed path whose node sequence
contains no repeated node; parallel arcs remain distinct alternatives. If
`u = v`, the empty arc sequence is the sole zero-length path.

For an ordinary randomized path replacement, the built-in path sampler uses a
randomized depth-first traversal. At each visited node it orders outgoing
arcs by stable arc ID, shuffles that order through the shared random source,
and visits heads not already in the current path. It returns at most
`VrpOperatorCatalogSettings.max_paths_per_leg` distinct paths and stops after
`VrpOperatorCatalogSettings.max_arc_expansion_steps` arc-expansion steps. This
bounded sampler defines the candidate paths for route-order, reroute, and
sampled objective-focused Operators. A
route move with no generated path alternative is ineligible. For one-leg
unranked moves, feasible complete plan alternatives are returned in a uniformly
randomized order. When one stop
sequence change creates multiple legs, the Operator generates at most
`VrpOperatorCatalogSettings.max_candidate_alternatives`
distinct complete path assignments, sampled without replacement from the
products of the returned per-leg path sets. Each assignment chooses one
sampled path for every changed leg. These assignments form the finite
candidate set for the move. The Operator assesses them under the requested
feasibility condition and, without objective ranking, returns feasible
assignments in a uniformly randomized order. An objective-ranked Operator
orders feasible assignments or complete plans by the registered objective
value, resolving ties uniformly. Each call returns up to
`CandidateContext.max_candidates` proposals.

The `callback` in an `AdditivePathCostRegistration` has this contract:

```text
AdditivePathCostCallback(
    arc_id: ArcId,
    request: AdditivePathCostRequest,
) -> float
```

The immutable request contains `objective_id: ObjectiveId`, the current
working plan as `plan: VrpSolution`, `vehicle_id: VehicleId`, one-based
`day_index: int`, zero-based `leg_index: int`, and the leg's
`origin_node_id: NodeId` and `destination_node_id: NodeId`. The plan is the
working value immediately before this leg is replaced. For a fixed request,
the callback is pure and deterministically returns the same finite,
nonnegative built-in Python `float` for an arc on every invocation; the value
depends on the arc and request, not on the candidate path or the time at which
the arc is entered. The factory validates the registration's IDs and
callability. The generated Operator validates every returned arc cost during
search. A callback exception or invalid arc cost follows the generic Operator
failure handling, attributed to the generated Operator's ID.

The caller registers this callback for an objective only when its application
can guarantee the additive decomposition below for every request and path.
That semantic guarantee is application-supplied; the factory treats the
registry entry as the caller's declaration that it holds.

For objective `i`, a matching entry in `additive_path_costs` declares that the
application guarantees the following decomposition: replacing the requested
leg by any loopless path `P` changes the complete plan's selected objective
according to
`F_i(x[P]) = C + sum(additive_path_cost(a, request) for a in P)`, where `C` is
independent of `P`. Here `x[P]` means the same complete plan with that leg
replaced by `P`. The objective's ordering must agree with the numeric order of
the sum. Thus the callback's weights are static and nonnegative per arc, and
their sum gives the entire path-dependent objective change. The
factory-generated objective-specific shortest-path Operator uses Dijkstra
when its objective has a matching entry; without one, it uses the bounded
sampled-path procedure below. Other path Operators, including Route-leg
reroute, follow their own definitions and use sampled paths.

Dijkstra's algorithm then finds an exact minimum-cost path; equal-cost path
ties are resolved using the shared random source. When that path fails the
requested feasibility check, the Operator discards it and does not count a
move unit. It continues with another selected leg when one remains; the
exact-path variant does not search for a more expensive feasible path for that
leg.

When the objective-specific shortest-path Operator has no matching callback
registration, it samples the bounded loopless paths above, substitutes each
into a complete candidate plan, checks the requested feasibility condition,
calculates the selected objective across the full scenario bank, and orders
feasible sampled candidates by that value. Exact ties are randomized uniformly.
Return up to `CandidateContext.max_candidates` candidates. This fallback covers
objectives based on scenario statistics, departure-time effects, downstream
timing, and penalties. Other objective-ranked path Operators use the sampled
path behavior specified in their definitions. An unranked path choice selects
uniformly from the feasible paths or complete path assignments returned by
the bounded sampler.

### 2.3 Evaluation analysis consumed by Operators

When the VRP `ApplicationEvaluator` supplies `ApplicationEvaluation.analysis`,
the value has type `VrpOperatorAnalysis` and is immutable. Operators may use
it through `CandidateContext.base_evaluation`; they may also derive the same
targets from the base and instance. A value of `None` means Operators use the
fallback rules below.

`VrpOperatorAnalysis` is a snapshot of the evaluator calculations available
for Operator guidance. It contains `is_complete: bool`, an optional
`hard_failure: HardFeasibilityFinding | None`, and the following collections:

```text
HardFeasibilityFinding(
    kind: Literal['static_constraint', 'scenario_constraint', 'compliance_rule'],
    message: nonempty string,
    scenario_id: ScenarioId | None,
)
```

The record identifies the first hard requirement that stopped evaluation.
`scenario_id` is `None` for a plan-level check and identifies the scenario for
a scenario-dependent check. `message` states the violated requirement and
includes relevant entity identifiers and the measured violation when those
values are available. A complete analysis has `hard_failure=None`; a partial
analysis caused by hard infeasibility has exactly one such record.

| Field | Contract |
|---|---|
| `scenario_evaluations` | Complete `VrpScenarioEvaluation` traces for scenarios reached by evaluation. A completed feasible evaluation contains one trace per ScenarioBank scenario. Trace fields and nullability are defined in `multiday_vrp_input_format.md` §12.2. |
| `resource_findings` | `ResourceFinding` records for availability, capacity, overlap, driver-rule, or HOS checks reached by evaluation; records may include zero-violation findings. |
| `scenario_objective_costs` | For a feasible, fully evaluated plan, one `ScenarioObjectiveCost` for each objective and scenario, containing the scenario-level value before scenario aggregation. |

`is_complete` is true when evaluation completes all hard checks, scenario
calculations, penalties, and objective aggregation. The model stops objective
aggregation at its first hard-feasibility failure (§1.8 of
`multiday_vrp.md`), so analysis for such a plan has `is_complete = false`,
includes the triggering `hard_failure` record, and has empty
`scenario_objective_costs`. For a transfer-storage failure, the evaluator
finishes scenario simulation to collect storage excess and its contributing
transfer steps across scenarios; it does not continue rule or objective
aggregation. For other hard failures, analysis contains the records reached
before that stop. Operators use positive findings present in a partial
analysis and use their documented fallback when the records needed for a
target are unavailable. They treat a missing finding in partial analysis as
unknown, not as a zero finding. Each scenario trace contains the route stops
and arc traversals needed by the built-in Operators and extension callbacks;
its schema is defined once in the input-format document cited above.

`ResourceFinding` contains `kind`, `resource_id: str`,
`scenario_id: ScenarioId | None`, `vehicle_id: VehicleId | None`,
`day_index: positive integer | None`, `activity_ref: ActivityRef | None`,
`measure: finite nonnegative float`,
`penalty_by_objective: tuple[(ObjectiveId, finite float), ...]`, and
`transfer_step_ref: TransferStepRef | None`. The optional
`transfer_step_ref` identifies a transfer step contributing to a
`transfer_storage` finding. `kind` is one
of `vehicle_availability`, `personnel_availability`,
`duty_overlap`, `cargo_capacity`, `transfer_storage`, `tool_capacity`,
`driver_work`, `driving_time`, `break`, `rest`, or `hos`. `measure` is the
nonnegative amount of the finding in the model's declared unit. The
`penalty_by_objective` tuple contains entries only for objectives assigned the
finding's category. Its guidance values use the objective-specific
penalty-function output after category scaling and before penalty aggregation.
They support finding-guided
Operator selection; the configured penalty aggregator determines the actual
objective penalty total.

`ScenarioObjectiveCost` contains `objective_id: ObjectiveId`,
`scenario_id: ScenarioId`, and finite `value: float`. A finding-guided
Operator with a configured `objective_id` targets an occurrence or resource
with the largest corresponding positive `penalty_by_objective` value when the
base analysis is complete. This is the per-finding guidance value defined
above, including when a custom penalty aggregator combines findings
nonlinearly. Exact ties are resolved uniformly. Compliance
violations have a separate measure-based rule: §4.24 selects uniformly among
distinct hard-finding keys represented by applicable positive findings, then
uses the largest scenario `measure` for that key as the target. The key is
`kind`,
`resource_id`, `vehicle_id`, `day_index`, and `activity_ref`; scenario ID is
excluded so scenario findings for the same resource can be compared. Internal
repair trials with a matching key rank by their largest known `measure`, lower
preferred. A complete assessment with no record for the selected key resolves
it with measure zero. A partial assessment without that key leaves its measure
unknown, not zero, and is unranked. When at least one trial has a known score,
the Operator selects the lowest-scoring trials, with ties resolved uniformly;
when all trials are unranked, it uses a uniform fallback among otherwise
eligible trials. Measures from different keys or kinds are never compared.
When the base analysis is absent or has no applicable finding, the Operator
uses its stated uniform target fallback. An Operator that selects by objective
value evaluates its complete candidate plans with the shared assessment
helper from §2.1.

## 3. Generic Operators

Generic Operators are available for every validated instance profile. Their
move eligibility still depends on the current plan and instance data. The
generic catalog includes vehicle-route assignment, route order, route paths,
timing, and driver assignment. Feature-specific Operators are added when the
corresponding profile features are active.

### 3.1 Vehicle-route assignment and order Operators

Vehicle-route assignment Operators change which vehicles perform service
work over one or more days by adding or removing active `VehicleDayRoute`
records. The vehicle and day on every retained route remain the authoritative
assignment. Tour-boundary markers continue to divide the tours within each
route.

For these moves, an atomic package unit contains the complete package chain:
pickup, delivery, and every transfer operation. An intervention or other
non-package service occurrence is an atomic activity unit. Add an undirected
link between atomic units for each declared precedence or co-location
relationship; a connected component of this graph is a **repair unit**. All
route-stop occurrences of a repair unit move together, preserving package
custody and declared relationships. A package pickup whose pickup node is the
resulting route's starting-depot node belongs in that route's initial depot
pickup block, before the route leaves the depot. The shared assessment checks
the complete plan after each proposed unit placement.

#### Window-compatible package-cluster reassignment

This Operator changes package-to-vehicle assignments while preserving the set
of active vehicles. It is registered when `restrictive_service_windows` is
active and at least two vehicles can serve package chains. The factory
registers its unranked variant and one objective-targeted variant for each
selected objective. The move remains eligible when
`exact_active_vehicle_count` is supplied. Every completed candidate keeps the
same active vehicle ID set as its base plan.

An eligible repair unit is a complete package chain, including its pickup,
delivery, transfer operations, and any activity units linked through declared
precedence or co-location relationships. The unit is movable when at least
one assignment to a different currently active vehicle preserves vehicle
eligibility and the complete chain structure. A transfer-bearing chain is
movable only when a complete remapping preserves its ordered transfer
sequence and custody. Independent non-package activities remain with their
current vehicle in this Operator; their assignment has separate Operators.

For each candidate trial, select a movable package-chain repair unit uniformly
as the cluster seed. A cluster is the set of repair units whose vehicle
assignments are reconsidered together; its units may be assigned to different
active vehicles. Grow the cluster to at most `k` repair units. At each growth
step, sample complete assignment proposals for additional movable repair
units, with destinations among vehicles already active in the base plan. Each
proposal assigns every occurrence of its repair unit and inserts its stops at
legal route positions. Depot pickups join the destination route's initial
pickup block; delivery and transfer stops retain their required precedence
and custody order. Sample without replacement using the shared
`RandomSource`, inspecting no more than
`VrpOperatorCatalogSettings.max_candidate_alternatives` proposals for a
growth step. The seed itself is first reassigned to a different active
vehicle. Units outside the growing cluster retain their base assignments.
Each proposal changes the assignment of the selected repair unit and leaves
every package fully assigned in the candidate plan. Assess each resulting
complete plan under the shared application evaluator, including route costs,
capacity, service-window penalties, and all other declared objectives. These
assessments provide the network-cost and time-window evidence used to grow and
rank the cluster; the Operator derives network proximity from the instance
graph and its declared objective functions.

For an objective-targeted registration, rank each cluster extension and its
assignment by the target-first Pareto ranking defined in §3.1. For an
unranked registration, use the all-objective Pareto ranking. When the
objective declaration contains a dedicated time-window-violation objective,
retain only assignments with the lowest value of that objective among the
bounded alternatives for the call; this retains zero-violation assignments
whenever one is available and otherwise retains the least-violating ones.
Compare those values with the declared time-window objective's
`ObjectiveFunction.compare` operation.
Keep a bounded beam of alternative partial reassignments. At each growth step,
expand the beam with the best-ranked sampled assignment proposals, resolving
ties uniformly. Different seeds, destinations, and insertion positions can
therefore produce distinct complete candidate plans. The engine receives
only complete plans; each is processed through the ordinary candidate
pipeline. Record the complete plan after each successful growth step, so a
call with size `k` can return candidates with modification counts from `1`
through `k`. Collapse structurally identical plans before returning the list.

Every returned plan preserves the base plan's active vehicle ID set, service
coverage state, and package-chain relationships, and meets the requested
feasibility guarantee. The candidate builder rejects a partial assignment if
it cannot place a selected repair unit or would leave a base-active vehicle
inactive. This preserves an exact active vehicle count as well as the active
fleet itself. The default catalog uses the shared
`max_candidate_alternatives` bound, and the returned candidate list is limited
by `CandidateContext.max_candidates`. Distinct candidates follow the
registration's objective/Pareto ranking, with random tie resolution through
the shared `RandomSource`.

**Size.** k is the maximum number of package-chain repair units whose vehicle
assignment may change in one candidate plan.

**Modification count.** m is the number of distinct package-chain repair
units assigned to a different vehicle in the completed plan. A chain moved
across multiple route records counts once. Returned candidates each change at
least one assignment; a call with no reassignment candidate returns an empty
candidate list with `m=0`.

**C101-25 illustration.** A time-window-compatible assignment of the 25
customers to three active vehicles is `20, 24, 25, 23, 22, 21`;
`5, 3, 7, 8, 10, 11, 9, 6, 4, 2, 1`; and
`13, 17, 18, 19, 15, 16, 14, 12`. The route loads are `110`, `160`, and
`190` against capacity `200`; the route distances are `36.3`, `59.2`, and
`95.8`, totaling `191.3`, with zero time-window penalty. This assignment
illustrates the route clusters this Operator should be able to generate; it
serves as a candidate-plan reference, not as a prescribed search initializer.
Relative to the reported `359.8` plan, fourteen package-chain vehicle
assignments change. A single call from that plan therefore needs `k >= 14` to
produce the complete reassignment; the default maximum size can instead build
the grouping through several successful partial reassignment calls.

#### Spatial/time-window route-cluster rebuild

**Status:** implemented and registered in the default catalog.

The package-cluster Operator above grows a set of package-chain repair units
and reassigns them incrementally. This Operator makes a different move:
rebuilding the route partition from customer geography and time-window
structure, so a block of related customers can move together even when they
currently belong to several routes.

The implemented first version applies to a single day with one route per
active package-serving vehicle, complete pickup/delivery chains, and pickups
at each route's starting depot. Plans with transfers, intervention or other
service stops, assigned teams, post-service waits, breaks, tour-end markers,
or multiple routes for one vehicle return no candidates.

For a supported base plan, let `K` be the number of active vehicles. Form
customer groups independently of the base plan's route assignments, using
`K` as the target group count. The normalized input stores node IDs and travel
times rather than coordinates, so use symmetrized shortest travel time as the
spatial-proximity measure. For each directed arc, use the minimum travel time
across scenarios and time boundaries to form this static clustering measure;
the complete evaluator still uses actual scenario and departure-time data.
Pair affinity adds the least lateness from either possible delivery order to
that spatial measure. Each pair check includes service duration and travel
time. Vehicle capacity is a hard group-merge constraint; candidate assignment
also checks vehicle/package compatibility, group custody, full route capacity,
and that every active vehicle remains in use. The complete evaluator checks
cumulative route schedules. A bounded set of distinct groupings is generated
by varying agglomerative merge tie-breaking.

For each grouping, candidate vehicle mappings preserve the active vehicle set
and respect vehicle/package compatibility. Among the bounded sampled eligible
mappings for each selected group subset, the Operator chooses mappings with
the lowest estimated static route travel time. For a candidate that
consolidates `k` groups, remove those groups' package chains from their current
routes and rebuild the affected routes. Units in unselected groups keep their
base assignments. Rebuild each affected route with regret insertion: evaluate
each pending package-chain unit at every delivery position, using total
estimated time-window lateness, minimum route slack, and static travel time.
A unit with the greatest difference between its two best insertion scores is
inserted first; ties and equal-cost positions use the shared random source.
At `k = K`, rebuild the complete route partition. Return a bounded batch of
structurally distinct complete plans to the normal candidate pipeline. The
shared evaluator decides their actual time-window penalties, capacity, route
costs, and full objective vectors.

Here `k` is the number of whole customer groups selected for consolidation,
bounded by `min(K, max_neighborhood_size)`. The modification count `m` is the
number of selected groups with at least one package-chain repair unit assigned
to a different vehicle, so `m <= k`. Return a candidate only when at least one
repair unit changes vehicle; route-order-only candidates belong to 2-opt. This
definition makes a full `K`-group regrouping possible without requiring `k` to
count every package moved. Keep this Operator separate from route-order
optimization so its assignment effect can be measured directly.

The default catalog registers the ordered composite `spatial/time-window
route-cluster rebuild -> 2-opt` as a separate candidate choice, both unranked
and for each selected objective target. The 2-opt stage receives each rebuilt
plan and returns a bounded batch of route-order alternatives. Compare the
cluster-only choice, one 2-opt stage, and an additional sequential 2-opt pass
as separate experiment settings; keep the component calls and candidate counts
visible in run provenance and statistics. The 2-opt component refines route
order and does not change vehicle assignment.

**C101-25 diagnostic.** The benchmark reference plan groups customers as
`1..11`, `12..19`, and `20..25`, with loads `160`, `190`, and `110` against
capacity `200`. Its time-window-feasible route order is
`5, 3, 7, 8, 10, 11, 9, 6, 4, 2, 1`,
`13, 17, 18, 19, 15, 16, 14, 12`, and
`20, 24, 25, 23, 22, 21`. Use this plan to inspect whether the Operator finds
these groups and orders; they are benchmark references, not hard-coded rules
or a guaranteed result. Check that the evaluator reproduces distance `191.3`
and zero time-window penalty on the reference plan. Keep that known plan out
of any search run used to measure whether the algorithm discovers it.

For each pending repair unit, the current regret construction evaluates all
legal delivery insertion positions against the partial route order. Its score
is lexicographic: total estimated time-window lateness, negative minimum route
slack, then static travel time. It inserts the unit with the greatest
best-versus-second-best score difference and resolves equal scores with the
shared random source. The operator rebuilds complete candidate plans and the
normal evaluator remains authoritative for feasibility and objective values.

#### Vehicle-route consolidation

**Move.** Form ordered pairs of active vehicle-day routes on the same calendar
day and assigned to distinct vehicles. The first route is the source and the
second is the receiving route. A pair is eligible when the source route has at
least one repair unit and every repair unit touched by that route is wholly
contained in it. Randomly order eligible pairs without replacement and
consider at most `VrpOperatorCatalogSettings.max_candidate_alternatives`
pairs for one consolidation move. If no pair is eligible, return an empty candidate list.

The best-insertion and regret-insertion catalog specifications use the same
pair and differ in the unit-order rule defined in §3.1. For each pair, start
with a beam containing the base plan. Move one repair unit at a time from the
source route into the receiving route. A placement assigns every stop in the
repair unit to legal positions while preserving its internal order, package
custody, service coverage, and declared relationships. A package pickup at the
receiving vehicle's starting-depot node joins its initial depot pickup block.
Source tour-end markers leave with the source route. Each insertion position
determines the destination tour using the receiving route's existing depot
markers. Those markers remain attached to their depot stops; insertion
preserves the receiving route's tour boundaries. Rebuild affected `RouteLeg`
values with §2.2 and recompute the
downstream scenario schedule, capacity findings, time-window penalties, and
other assessment results for every placement.

During construction, units awaiting relocation remain on the source route, so
each beam state is a complete plan. The source route is removed only after all
its repair units have been placed. The receiving route keeps its vehicle, day,
driver, start time, and overnight depot. Its depot-pickup block, stop types,
activity and transfer references, team assignments, waits, breaks, and valid
tour markers are preserved on their depot stops. Source vehicle-day routes on
other days retain their stored decisions; the shared assessor checks any
resulting overnight continuity and vehicle-wide effects.

When `exact_active_vehicle_count` is declared, a consolidation pair is
eligible only if removing the source vehicle-day route leaves that vehicle
active on another day. This preserves the required count. For a one-day
instance with a fixed active-vehicle count, the factory omits both
vehicle-route consolidation specifications because every completed
consolidation would reduce the active-vehicle count.

For every service or transfer-operation stop whose route position changes,
rebase `pre_service_wait` to retain its base plan's probability-weighted mean
service start when possible. Identify an activity stop by `ActivityRef`, and a
transfer-operation stop by `TransferStepRef` and role. Let `target[u]` be the
weighted mean service start for stop `u` in
`CandidateContext.base_evaluation.analysis`, and let `arrival[u]` be its
weighted mean arrival after waits already assigned to preceding stops have
been applied. Set `pre_service_wait[u] = max(0, target[u] - arrival[u])`, then
recalculate downstream scenario arrivals before processing the next stop.
Scenario weights come from the instance's `ScenarioBank`. When a service-start
trace is absent, retain that stop's current wait. Depot-only-stop timing fields
remain attached to their stops.

For every pair, §3.1 bounds placement proposals per repair unit and the beam
width. These are per-step bounds; the same limit also bounds the number of
source/receiver pairs considered for one consolidation move. A beam branch
ends when none of its pending units has a legal placement in its sampled
proposals. The completed plan is a consolidation neighbor when it meets the
requested feasibility condition. A consolidation may improve `vehicles_used`
when the source vehicle has no remaining active route anywhere in the planning
horizon.

Across considered route pairs, an unranked registration orders complete
neighbors on the all-objective nondominated set uniformly. An
objective-targeted registration orders complete neighbors with the best
target-objective value and Pareto-nondominated values on the other objectives
uniformly. The returned list follows this order and is truncated at
`CandidateContext.max_candidates`. Pair ordering, insertion-position sampling,
beam ties, and list ordering use the shared random source. If no construction
completes, return an empty candidate list.

**Size.** k is the maximum number of vehicle-route consolidations applied
sequentially to the working plan.

**Modification count.** m is the number of source route records removed by
completed consolidations. One complete source-route consolidation counts as
one, regardless of how many stops its route contains.

#### Vehicle-elimination destroy-and-repair

The factory registers vehicle-elimination specifications only when
`variable_active_vehicle_count` is active. This Operator removes all routes
of one vehicle after redistributing its service units, so its defining move
reduces the number of active vehicles. The exact-count constraint in
`multiday_vrp.md` §1.4 makes that move ineligible.

**Move.** At each candidate trial, select uniformly an eligible active
vehicle. Collect every repair
unit with at least one route-stop occurrence on one of that vehicle's routes,
including all occurrences of those units elsewhere in the plan. The selected
vehicle is eligible when this collection is nonempty. Depot-only stops and
tour-boundary markers belong to the source route structure and leave with its
route records.

The best-insertion and regret-insertion specifications use the unit-order
rules in §3.1. For each repair unit, a complete placement assigns all its
activity occurrences to one or more existing vehicle-day routes assigned to
other active vehicles. Package-chain placements preserve pickup, delivery,
transfer operations, and custody using §4.13. Independent activity units are
inserted at route positions. Relationship components are placed as one unit,
with all their members represented in the same complete plan. Preserve each
unit's service-coverage state, activity references, transfer records,
pre-service and post-service waits, break durations, and intervention team
assignments.

Move one complete repair unit atomically from its current route assignments
to its selected receiving route or routes. Units awaiting relocation remain
at their current assignments, so every intermediate beam state is a complete
plan. Receiving routes keep their vehicle, day, driver, start time, and
overnight depot. Rebuild affected paths using §2.2 and reassess the complete
plan, including capacity, time-window penalties, vehicle and personnel
availability, package custody, transfer storage, and work-rule findings. The
shared insertion-strategy contract in §3.1 bounds the beam and the legal
placements considered at each step.

Before assessing a beam state, remove any vehicle-day route that has no
remaining service-unit stop. This applies to source and receiving vehicles;
its depot-only stops and tour markers leave with that route record. After the
last collected repair unit is reassigned, every route record for the selected
vehicle has therefore left the completed plan.

Each candidate trial starts from the call's base plan and uses an independent
beam. It applies up to `k` vehicle eliminations in sequence, reassessing
eligibility after each completed elimination. Retain the complete plan when
it meets the requested feasibility condition. If a later elimination has no
completed construction, the trial may still return the complete plan produced
by its earlier eliminations; if the trial completed none, it produces no
proposal. The Operator generates distinct trials up to the internal alternative
limit, then orders their complete plans by the registration's unranked or
objective-targeted rule below. Each proposal's `m` is the number of vehicle
IDs eliminated in that proposal.

For every trial, after each successful elimination recompute structurally
eligible source vehicles and receiving routes from that trial's working plan.
The shared evaluator checks all vehicle, driver, capacity, service-window,
route, depot, transfer, and work-rule requirements for every completed
alternative. An unranked registration returns the trial results on the
nondominated set in a uniformly randomized order. An objective-targeted
registration orders results by the best target-objective value and
Pareto-nondominated values on the other objectives, resolving ties uniformly,
following §3.1. Return the ordered list up to
`CandidateContext.max_candidates`.

**Size.** k is the maximum number of distinct vehicle IDs eliminated
sequentially in the call.

**Modification count.** m is the number of vehicle IDs whose route records
are removed after all their service units have been reassigned.

#### Vehicle-route split

When `exact_active_vehicle_count` is declared, the destination vehicle
must already have an active route on another day. This keeps the number of
distinct active vehicles unchanged. For a one-day instance with a fixed
active-vehicle count, the factory omits this specification because every
completed split would add an active vehicle.

**Move.** At each candidate trial, select uniformly an active vehicle-day
route with at least two
movable service units and at least one vehicle with no active route on that
day. A package-chain unit from §3.1 moves atomically with its pickup,
delivery, transfer steps, and paired transfer operations. An activity unit
moves with its route stop. Sample a nonempty proper subset of the route's
ordered service units uniformly without replacement; the subset forms the new
route and the complement stays in the existing route. Preserve source order
within each group and all dependent activity and custody relationships in the
complete candidate. Depot-only stops remain with the existing route. Choose
the new vehicle from those with no active route on that day. Package pickup
stops whose pickup node equals a resulting route's starting-depot node form
that route's initial depot pickup block. Other moved stops preserve their
order from the source route. Rebuild both routes' paths using §2.2 and sort
the resulting `VrpSolution.routes` by day and instance vehicle order.

For this Operator, a package-chain unit is eligible for partition when all of
its pickup, delivery, and transfer-operation stops, including both operations
for every transfer step, are contained in the selected vehicle-day route. A
package chain with service stops on another route stays together on its
existing route assignments during this move.

The new route uses the source route's `start_time`. Its overnight depot is the
source route's overnight depot when that depot is eligible for the selected
vehicle; otherwise the Operator samples from that vehicle's eligible depots.
The Operator samples a driver who is authorized and qualified for the vehicle.
Personnel availability contributes its declared penalty to assessment and
candidate ranking. Rebase service and transfer-operation
`pre_service_wait` fields for the two resulting routes by the rule in
**Vehicle-route consolidation**, using the base plan's service starts for the
corresponding stops. The new route's first leg starts at the selected
vehicle's derived starting depot for that day. Any later active route for that
vehicle is updated to use the new overnight-depot continuity and its affected
paths are rebuilt. The full-plan assessor checks vehicle authorization,
driver qualification, depot continuity, service coverage, package custody,
capacity, tour limits, work rules, and the requested feasibility guarantee.
Vehicle and personnel availability contribute their declared penalties. The
Operator discards alternatives that fail the requested feasibility guarantee.

One candidate trial performs up to `k` splits sequentially on its private
working plan, starting from the base; a later candidate trial starts from the
same base. Sample distinct trials under the bound in §2.1. The unranked
variant returns assessed alternatives in uniformly randomized generation
order. An objective-targeted variant orders alternatives by ascending value
for its registered objective, resolving ties uniformly. Partition, vehicle,
driver, and depot alternatives use the shared random source. Each call returns
up to `CandidateContext.max_candidates` proposals. If no alternative meets
the requested feasibility condition, return an empty candidate list.

**Size.** k is the maximum number of vehicle-route splits applied sequentially
to the working plan.

**Modification count.** m is the number of new active vehicle-day route
records created by completed splits. One route split counts as one,
independent of the number of service units assigned to the new route.

#### Route-order Operators

Route-order Operators change ordered `RouteStop` sequences. Unless a move says
otherwise, the route's vehicle, day, start time, and overnight depot remain
fixed. A move may relocate any tagged stop variant; the evaluator assesses
service coverage, precedence, package custody, capacity, depot, and personnel
requirements for the complete candidate.

The generic `Relocate` may move one stop between existing active routes,
including different days, when the activity's input eligibility permits the
destination day. It retains the participating route records and their vehicle
assignments. Vehicle-route consolidation, vehicle-route split, and
vehicle-elimination destroy-and-repair handle changes to active route records
and vehicle use. The other route-order moves use routes on the same day unless
their definition explicitly says otherwise. A route-order candidate preserves
each stop's type-specific fields. It rebuilds only legs whose endpoints
change, using §2.2. A transfer stop may move independently while retaining
its `TransferStepRef`; the resulting custody order is assessed by the shared
feasibility rule.

Each candidate trial begins with the supplied base. It samples eligible
targets and move positions through the shared `RandomSource`, then applies the
selected move units to that trial's private plan according to the Operator's
size definition. Each individual move attempt inspects at most
`VrpOperatorCatalogSettings.max_candidate_alternatives` complete alternatives;
a trial may make up to `k` sequential attempts. Structurally invalid,
unchanged, infeasible-under-request, and repeated plans are skipped. These six
Operators have independent target mode, so their remaining candidates follow
randomized generation order. The table names the alternative sampled by each
Operator and the resulting list order.

| Operator | Candidate alternative sampled for a trial | Candidate-list order |
|---|---|---|
| 2-opt | A route and a valid inclusive stop-index pair; apply up to `k` reversals sequentially within the trial. | Randomized trial-generation order. |
| Relocate | A source stop occurrence and destination route/insertion index; apply up to `k` relocations sequentially within the trial. | Randomized trial-generation order. |
| Or-opt chain relocation | A length-`k` contiguous stop block and destination route/insertion index. | Randomized trial-generation order. |
| Exchange | A pair of distinct stop occurrences; apply up to `k` exchanges sequentially within the trial. | Randomized trial-generation order. |
| 2-opt* suffix exchange | A pair of same-day routes and one cut index for each; apply up to `k` suffix exchanges sequentially within the trial. | Randomized trial-generation order. |
| SWAP* free-insertion exchange | A cross-route stop pair and one insertion index in each route after removal; apply up to `k` pair exchanges sequentially within the trial. | Randomized trial-generation order. |

#### 2-opt

**Move.** Choose one `VehicleDayRoute` with at least two stops and indices
`0 <= i < j < len(stops)` such that `j - i + 1 >= 2`. Reverse the inclusive
slice `stops[i:j + 1]`. Keep the route's start and overnight depots fixed.
Rebuild every leg incident to the reversed slice. A trial is ineligible if
any changed endpoint pair has no path under §2.2 or the resulting sequence is
unchanged.

For one call, sample up to
`max(CandidateContext.max_candidates,
VrpOperatorCatalogSettings.max_candidate_alternatives)` distinct first-reversal
targets without replacement, capped by the number available. Later reversals
within a trial choose uniformly from the targets available in the current
working plan. The call stops when it returns `CandidateContext.max_candidates`
distinct plans or exhausts this bounded first-target sample. The
alternative-inspection limit applies to each reversal attempt, not to the
whole call.

**Size.** k is the number of 2-opt reversals applied sequentially to the
working copy.

**Modification count.** m is the number of completed reversals. Invalid
directed paths or moves that fail the requested feasibility condition do not
count.

#### Relocate

**Move.** Select one stop occurrence and one insertion index `q` in a route's
stop list, where `q` ranges from zero through the current stop count. Remove
the occurrence, adjust `q` for removal when source and destination are the
same route, and insert it at `q`. Its type-specific data and stop-level timing
fields travel with it. A tour-end marker stays attached to its depot stop; a
marker that would no longer end a depot visit makes the trial ineligible.
Rebuild affected legs. Exclude a trial whose final solution is structurally
identical to the working plan.

**Size.** k is the maximum number of individual stop relocations applied
sequentially.

**Modification count.** m is the number of completed relocations. One
relocation counts as one even when its feasibility depends on related stops
elsewhere in the plan.

#### Or-opt chain relocation

**Move.** Select one route and a contiguous inclusive block `[i, j]` of
`stops`, with `0 <= i <= j < len(stops)`. Remove the block and insert it at
index `q` in the same or another active route, preserving its internal order.
The block length must equal `k`; an infeasible or unchanged trial is rejected
under §2.1. Rebuild affected legs and preserve each tour-end marker's
relationship to its depot visit.

**Size.** k is the number of stop occurrences in the one block moved by the
call. A size-one move has the same plan change as Relocate.

**Modification count.** m = k when the block is moved successfully; otherwise
the call returns an empty candidate list.

#### Exchange

**Move.** Select two distinct stop occurrences, possibly from one route or
from two routes on the same day, and exchange their positions. A trial is
eligible when both resulting route sequences retain valid tour-end markers.
Rebuild affected legs and exclude a trial whose final stop sequences equal
those of the working plan, even if path rebuilding selects different road
legs.

**Size.** k is the number of pairwise exchanges applied sequentially.

**Modification count.** m is the number of completed exchanges. One exchange
counts as one move.

#### 2-opt* suffix exchange

**Move.** Select two distinct active routes on the same day and cut indices
`i` and `j`, where each cut position counts the stops retained in that
route's prefix and ranges from zero through its stop count. Exchange the
suffixes beginning at those positions, preserving each suffix's order. Keep
both routes' vehicle, day, start depot, and overnight depot fixed. A trial is
eligible when tour-end markers remain attached to valid depot visits. Rebuild
the two connecting and closing paths. Discard a trial when its final stop
sequences match their starting sequences, even if path rebuilding selects
different road legs.

**Size.** k is the number of suffix exchanges applied sequentially.

**Modification count.** m is the number of completed suffix exchanges,
independent of suffix length.

#### SWAP* free-insertion exchange

**Move.** Select one stop from each of two distinct routes on the same day,
remove both, then independently choose an insertion index from zero through
the destination route's new stop count and insert each stop in the other
route. For each inserted stop, exclude the insertion index equal to that stop's
former index in its source route. Exclude an unchanged pair result and, after
the sequential exchanges, a trial whose final stop sequences match their
starting sequences. Preserve valid tour-end markers and rebuild affected legs.

**Size.** k is the number of cross-route stop-pair exchanges applied
sequentially.

**Modification count.** m is the number of completed pair exchanges. One
pair counts as one move.

### 3.2 Route-path Operators

These Operators implement the always-active directed-route-path feature.
They replace paths between fixed consecutive stops while leaving the stop
sequence unchanged.

#### Route-leg reroute

**Move.** Select one `RouteLeg` and replace its arc sequence with a different
loopless directed path between the same endpoint nodes using §2.2. Exclude the
current path from alternatives. Without an `objective_id`, return the sampled feasible plans in a
uniformly randomized order. With an `objective_id`, order complete plans by
ascending value for that objective, resolving ties uniformly. Return up to
`CandidateContext.max_candidates` proposals. The
application evaluator remains responsible for the final recorded evaluation.

**Size.** k is the maximum number of route legs rerouted sequentially.
After a successful reroute, the first proposal in that step's returned order
becomes the working plan for the next selected leg. Return the alternatives
from the final successful step; each includes the earlier committed reroutes.

**Modification count.** m is the number of legs whose selected arc sequence
was replaced by a different sequence. Every returned proposal has the same m.
k counts changed legs, not arcs.
Randomized path selection uses the shared `RandomSource`.

#### Objective-specific shortest-path Operator

Register one instance for each declared `ObjectiveId`. For each selected
route leg, use Dijkstra when the factory's `additive_path_costs` registry
contains an entry for the Operator's objective. Dijkstra returns a path that
exactly minimizes the registered additive cost; equal-cost ties use the shared
random source. If a sampled path equals the current path, skip that path
alternative. When the registry has no entry for the objective, sample loopless
paths using §2.2, exclude the current path, assess each complete plan under
all scenarios, and order feasible samples by the value returned by the
selected `ObjectiveFunction`. Thus a standard-deviation objective ranks
complete candidate plans by their declared standard deviation. Resolve
equal-valued plans uniformly and return up to
`CandidateContext.max_candidates` proposals. The evaluator assesses each
returned candidate against the complete objective vector.

**Size.** k is the maximum number of distinct route legs whose paths are
replaced sequentially. Each search uses the selected objective and the
current working plan, including timing effects from earlier replacements.
After a successful reroute, the first proposal in that step's ordered result
becomes the working plan for the next selected leg. Return alternatives from
the final successful step; each includes earlier committed reroutes.

**Modification count.** m is the number of selected legs whose arc sequence
differs from its previous sequence. Each returned proposal has the same m.

**Context use.** `CandidateContext.base_evaluation` supplies the base's stored
`EvaluationRecord`; `base_evaluation.analysis` supplies
`VrpOperatorAnalysis` when available. Otherwise the Operator chooses eligible
legs uniformly. Application-bound objective calculation uses the shared plan
assessment helper in §2.1. `CandidateContext.random_source` orders eligible
legs and resolves equal-value ties. A call that cannot produce a candidate
meeting its feasibility request returns an empty candidate list.

### 3.3 Timing adjustment

**Move.** The registration supplies a positive `time_step` in the
`ScenarioBank` time unit. Select uniformly among changeable route `start_time`,
stop `pre_service_wait`, post-service `wait`, and `break_duration` fields.
Select `-time_step` or `+time_step` with equal probability. A proposal is
eligible only when it remains in the field's declared domain and preserves
`0 <= break_duration <= wait`. Recalculate scenario arrival, service,
departure, and return times when assessing the trial.

Each candidate trial starts independently from the supplied base and applies
up to `k` effective timing changes sequentially. Generate up to
`VrpOperatorCatalogSettings.max_candidate_alternatives` complete trials and
assess each finished plan against the requested feasibility condition. Skip
infeasible plans, unchanged plans, and plans duplicating an earlier result.
Return the remaining distinct plans in randomized generation order up to
`CandidateContext.max_candidates`.

**Size.** k is the maximum number of timing fields changed sequentially.

**Modification count.** m is the number of successfully applied timing
changes. A later move may change a field changed earlier in the same call.

### 3.4 Driver reassignment

**Move.** Select uniformly among active vehicle-day routes that have at least
one eligible alternative driver, then select uniformly an authorized person
whose driving skills include all skills required by that vehicle. Replace the
route's `driver_id`; the new driver joins the route crew, and the old driver
leaves it unless assigned to an intervention team on that route. The shared
assessment checks seat capacity, driver availability, and the driver's full
duty timeline. A route-driver pair already present is ineligible.

Generate up to
`VrpOperatorCatalogSettings.max_candidate_alternatives` complete assignment
trials. Each trial starts independently from the supplied base and applies up
to `k` route-driver changes sequentially to distinct routes. Assess each
finished plan against the requested feasibility condition. Skip infeasible
plans, unchanged plans, and plans duplicating an earlier result. Return the
remaining distinct plans in randomized generation order up to
`CandidateContext.max_candidates`.

**Size.** k is the maximum number of route-driver assignments changed
sequentially.

**Modification count.** m is the number of routes assigned a different
driver. `CandidateContext.random_source` selects among eligible routes and
qualified drivers.

## 4. Feature-specific Operators

The feature names and activation criteria below follow
multiday_vrp.md §1.9. The factory in §1.1 uses `VrpFeatureProfile` to include
each feature-specific Operator when its associated feature is active. When a
move family depends on multiple features, it is included when its stated
activation conditions hold and appears once even when several active features
make it applicable. Different strategies or objective targets may use
distinct `OperatorId`s and feedback histories.

A feature may have no eligible move for a particular base. In that case the
Operator returns an empty candidate list. The profile controls which Operator registrations
exist; the current plan controls which moves can be applied.

### 4.1 Within-day time-dependent travel

**Departure-time profile adjustment.** Register one Operator per selected
`ObjectiveId` and positive `time_step`. Select a route leg with at least one
arc whose entry-time travel value changes when an upstream `start_time`,
`pre_service_wait`, or post-service `wait` changes by `time_step`. Select
uniformly one such leg, then one such changeable field upstream of it, and
propose plus or minus `time_step`. A candidate trial applies up to `k` such
changes sequentially to a private copy of the base. Generate at most 32
distinct complete trials, each starting from the base, and retain those that
meet the requested feasibility condition. Return them in ascending selected-
objective order, resolving ties uniformly, up to
`CandidateContext.max_candidates`. If no eligible field or candidate plan
exists, return an empty candidate list. The Operator derives entry-time
changes by propagating each proposal through its complete scenario bank;
`VrpOperatorAnalysis` may identify likely targets, but the final ranking
always uses complete plan assessment.

**Size.** k is the maximum number of route start or stop-wait decisions
changed sequentially.

**Modification count.** m is the number of changed timing decisions.

### 4.2 Scenario uncertainty

**Scenario-differential reroute.** Register one Operator per `ObjectiveId`.
For each leg, calculate the range of scenario travel durations as the largest
minus the smallest summed arc travel duration on that leg. Select uniformly
among legs with a positive range, then sample up to 32 replacement paths under
§2.2. A candidate trial changes up to `k` distinct legs sequentially on a
private copy of the base. Assess each resulting complete plan in every
scenario and retain qualifying distinct plans. If all leg ranges are zero,
select a leg uniformly and use the same candidate generation. Return
qualifying candidates in ascending selected-objective order, resolving ties
uniformly, up to `CandidateContext.max_candidates`. If no different path is
available, return an empty candidate list.

**Size.** k is the maximum number of distinct route legs rerouted.

**Modification count.** m is the number of route legs whose paths change.

**Scenario-differential timing adjustment.** Register one Operator per
`ObjectiveId` and positive `time_step`. Select uniformly a route `start_time`,
stop `pre_service_wait`, or post-service `wait` whose resulting arrival or
travel cost differs across scenarios. A candidate trial applies up to `k`
plus or minus `time_step` changes to distinct eligible timing fields on a
private copy of the base, keeping each value in its domain. Generate at most
32 distinct complete trials, assess each in every scenario, and retain
qualifying plans. Return them in ascending selected-objective order,
resolving ties uniformly, up to `CandidateContext.max_candidates`. When no
scenario-sensitive field exists, select from any changeable timing field and
use the same candidate rule. Return an empty candidate list if no valid
alternative is produced.

**Size.** k is the maximum number of timing decisions changed.

**Modification count.** m is the number of timing decisions changed.

### 4.3 Network-event exposure

**Event-objective route reroute.** Register one Operator per declared event
objective. Its registration uses the objective's event-type set and event
counting rule. Select uniformly among legs containing at least one counted
event occurrence; if none contains one, select any leg. Sample up to 32
different paths under §2.2, substitute each into a complete plan, and rank by
the selected objective's `ObjectiveFunction`. Return eligible plans in
ascending objective order, resolving ties uniformly, up to
`CandidateContext.max_candidates`. The complete assessment recounts all event
occurrences, including repeated traversal of an event arc or node.

**Size.** k is the maximum number of distinct route legs rerouted.

**Modification count.** m is the number of legs whose arc sequence changes.
`CandidateContext.random_source` orders eligible legs and resolves ties.

### 4.4 Multi-day planning

**Driver swap or cycle.** For each candidate trial, select a cycle length `r`
uniformly from `2..min(k, number_of_active_routes)` and select `r` distinct
active routes uniformly. Assign each route the driver of the next route in
the sampled cycle, with the last route receiving the first route's driver.
Keep all route, vehicle, day, stop, depot, and intervention-team decisions
fixed. Reject an assignment vector that violates representation rules or the
requested feasibility guarantee. A trial may apply successive cycles to its
private working plan until it reaches `k` changed assignments; each new trial
starts from the base. Sample up to 32 distinct complete assignment trials and
return qualifying plans in randomized generation order, up to
`CandidateContext.max_candidates`. If no alternative remains, return an empty
candidate list.

**Size.** k is the maximum number of route-driver assignments in the one
cycle applied by a move. A call may apply successive cycles to its working
plan until it reaches k changed assignments.

**Modification count.** m is the number of routes assigned a different
driver. `CandidateContext.random_source` selects routes and cycle order.

**Driver schedule destroy-and-repair.** Select `min(k, n_active_routes)` active
routes uniformly and temporarily remove their driver assignments in an
internal partial plan. Draw at most 32 complete driver-assignment vectors without
replacement. Each vector assigns every selected route an authorized driver
who satisfies the vehicle's driving skills. Assess the full affected driver
duty timelines and the complete plan. If the registration names an
`objective_id`, order valid complete vectors by ascending value for that
objective, breaking ties uniformly; otherwise order vectors satisfying the
requested feasibility condition uniformly. Return up to
`CandidateContext.max_candidates` complete-plan proposals. A vector identical
to the base assignments is ineligible.

**Size.** k is the number of route-driver assignments removed and jointly
reassigned in one repair move.

**Modification count.** m is the number of selected routes assigned a
different driver. If all retain their former driver, return an empty
candidate list.

### 4.5 Overnight-depot choice

**Overnight-depot change.** Select one active vehicle-day route uniformly and
select one different `DepotId` from that vehicle's eligible overnight depots.
Change `overnight_depot_id`; the next active route's start depot changes by
depot continuity, including across intervening inactive days. Rebuild the
affected route endpoint legs. Each alternative depot assignment creates a
complete candidate from the base. If the registration names an
`objective_id`, return qualifying plans in ascending objective value,
resolving ties uniformly; otherwise return them in randomized sample order.
Sample at most 32 complete alternatives and return up to
`CandidateContext.max_candidates` proposals. A route with one eligible depot
has no move.

**Size.** k is the maximum number of vehicle-day depot assignments changed
sequentially.

**Modification count.** m is the number of routes assigned a different
overnight depot.

**Multi-day depot reassignment.** Select one vehicle uniformly and then a
contiguous subsequence of its active routes whose length is between 2 and
`min(k, n_active_routes_for_vehicle)`. When k is less than 2, return an empty
candidate list.
Jointly assign a different eligible overnight depot to at least one route in
that subsequence. Keep drivers, crews, stops, and tour markers fixed; derive
each later route's starting depot from the prior active route's new ending
depot and rebuild all affected endpoint paths. Each depot vector defines a
complete candidate trial from the base; sample at most 32 distinct vectors.
An optional registered `objective_id` orders qualifying plans by ascending
objective value, resolving ties uniformly; without it, return qualifying
plans in randomized sample order. Staff depot continuity is checked by the
complete assessment like every other personnel rule. Return up to
`CandidateContext.max_candidates` proposals.

**Size.** k is the maximum number of consecutive active-route depot
assignments jointly reconsidered for one vehicle.

**Modification count.** m is the number of reconsidered routes whose final
overnight depot differs from the working plan immediately before the move.
`CandidateContext.base_evaluation.analysis` may
guide seed and linked-route selection; the shared `RandomSource` resolves ties.

### 4.6 Multiple tours per day

The `split_tour` and `merge_tours` Operators make the two directions of a tour
boundary change explicit. Both are registered when
`multiple_tours_per_day` is active, with an unranked variant and an
objective-targeted variant for each selected objective. The unranked variant
returns eligible complete candidates in a uniformly randomized order. An
objective-targeted variant orders eligible candidates by ascending value of
its registered objective; ties are resolved uniformly. Each Operator assesses
the complete candidate plans and applies the requested feasibility guarantee,
returning up to `CandidateContext.max_candidates` proposals.

**Split tour.** Eligible locations belong to active vehicle-day routes whose
current tour count is below `max_tours_per_day[vehicle_id]`. A location is
either an existing intermediate depot visit whose final stop has
`tour_end = false`, or an active route leg and physical depot node distinct
from both leg endpoints, with sampled paths from the leg origin to the depot
and from the depot to the leg destination. For an existing visit, set
`tour_end = true` on its final stop. For a route-leg location, replace the
leg with a sampled path-pair alternative and insert a `DepotOnlyStop` at that
depot node with `pre_service_wait = 0`, `wait = 0`, `break_duration = 0`, and
`tour_end = true`. Sample eligible locations and path-pair alternatives
without replacement, generating at most `max_candidate_alternatives`
complete candidate plans for one move unit. The shared assessment checks the
resulting paths, tour limit, timing, capacity, and all other instance
constraints. The unranked variant returns eligible assessed
candidates in a uniformly randomized order; an objective-targeted variant
orders eligible candidates by ascending registered objective value, resolving
ties uniformly. Each call returns up to
`CandidateContext.max_candidates` proposals.

**Size.** k is the maximum number of tour boundaries added sequentially.

**Modification count.** m is the number of tour boundaries added. A split
that inserts a depot stop and marks it as a boundary counts as one.

**Merge tours.** For each eligible intermediate depot visit whose final stop
has `tour_end = true`, construct one alternative with that marker set to
false. Keep the depot visit, its timing and break fields, route-stop order,
and paths unchanged; this move merges the adjacent tours while preserving the
depot stop's other purposes. Sample at most `max_candidate_alternatives`
distinct boundary alternatives without replacement and assess each complete
plan against the requested feasibility guarantee. The unranked variant
returns eligible assessed candidates in a uniformly randomized order; an
objective-targeted variant orders candidates by ascending registered objective
value, resolving ties uniformly. Return up to
`CandidateContext.max_candidates` proposals.

**Size.** k is the maximum number of tour boundaries removed sequentially.

**Modification count.** m is the number of tour boundaries removed.

#### Depot visits, dwell, and tool segments

Depot-visit Operators are registered when at least one of these features is
active: `multiple_tours_per_day`, `tool_capacity_reservations`, or
`work_driving_rest_hos_rules`. Their objective-targeted variants rank complete
candidate plans under the selected `ObjectiveFunction`; the unranked variant
selects uniformly among eligible complete candidates. The two move directions
are separate registrations so their attempt and feedback statistics remain
distinct.

**Depot-visit insertion.** An eligible target is a pair of an active route
leg and a physical depot node distinct from both leg endpoints for which §2.2
returns at least one path from the leg origin to the depot and at least one
path from the depot to the leg destination. A physical depot node is one
unique value in the image of `node_of_depot`; co-located `DepotId` values
produce one insertion target. Select one eligible pair uniformly. Generate
complete path-pair alternatives from the two bounded path sets, sampling
without replacement and assessing at most
`max_candidate_alternatives` complete alternatives for this move unit. A
candidate replaces the selected leg with those two paths and inserts a
`DepotOnlyStop` at the depot node, with `pre_service_wait = 0`, `wait = 0`,
`break_duration = 0`, and `tour_end = false`. This creates a destination-side
depot visit and a new depot-to-depot segment boundary. Timing Operators can
add dwell or schedule a break there. The split-tour Operator can mark this
depot visit as a tour boundary in the same move.
Exclude a candidate that is structurally identical to the working plan. The
shared assessment applies the requested feasibility condition, including
tool-capacity states and daily return feasibility.

**Size.** k is the maximum number of depot-only stops inserted sequentially.

**Modification count.** m is the number of inserted depot-only stops.

**Depot-visit removal.** An eligible target is a `DepotOnlyStop` in an
intermediate depot-visit block with at least one sampled replacement path from
the preceding route location to the following route location. A stop in the
route's initial start-depot block or final overnight-depot block is
ineligible; an intermediate visit to either of those physical depot nodes
remains eligible. Select one eligible stop uniformly. Generate at most
`max_candidate_alternatives` complete replacement plans without replacement.
Each removes the stop and replaces its two incident legs with one sampled
path. The stop's `wait` and `break_duration` are removed with it. When the
removed stop carries `tour_end` and another stop remains in the same maximal
depot-visit block, move the marker to the last remaining stop in that block.
When it is the block's sole stop, removal also removes that tour boundary.
Reassess the full route, including tool-segment loading, unloading, and
capacity states.

**Size.** k is the maximum number of depot-only stops removed sequentially.

**Modification count.** m is the number of removed depot-only stops.

### 4.7 Restricted vehicle availability

**Availability-guided timing adjustment.** Register a positive `time_step`
and an `objective_id` bound to `vehicle_availability`. Select the vehicle
route with the largest `vehicle_availability` penalty contribution for that
objective, using
`base_evaluation.analysis`; if no such finding is available, choose a route
uniformly. For that route, propose plus/minus `time_step` changes to
`start_time`, `pre_service_wait`, and post-service `wait` fields that remain
in their domains. Assess up
to 32 complete plans and return eligible plans in ascending selected-objective
order, breaking ties uniformly, up to `CandidateContext.max_candidates`.

**Size.** k is the maximum number of route timing decisions changed.

**Modification count.** m is the number of timing decisions changed.

**Availability-guided stop relocation.** Select a stop occurrence on a route
with a positive `vehicle_availability` finding; when analysis is absent, pick
any stop on a route. Draw up to 32 destination-route and insertion-index
pairs uniformly. Keep the stop's type-specific fields and rebuild affected
legs. The complete assessment recalculates availability for the source and
destination vehicles. If an `objective_id` is registered, return eligible
candidates in ascending objective-value order; otherwise return them in a
uniformly randomized order, up to `CandidateContext.max_candidates`.

**Size.** k is the maximum number of stop relocations.

**Modification count.** m is the number of completed stop relocations.

### 4.8 Potentially binding cargo/tool capacity

**Capacity-aware package-chain relocation.** Calculate each package's load
contribution on every `LoadIntervals` state. Select a state with the largest
positive capacity excess, then select a loaded package with the largest weight
in that state; resolve ties uniformly. When all states are within capacity,
select uniformly among packages with the largest weight. Remove that package's pickup,
delivery, and paired transfer-operation stops into an internal partial plan.
Reinsert its custody chain as a whole, preserving its service-coverage
choices, transfer-step count, and facility choices.

During insertion, update the package's onboard state at each pickup, delivery,
transfer, and depot tool-change completion. Check the capacity formula in
multiday_vrp.md §3.3 for every `LoadIntervals` state, including other packages,
assigned tools, and consecutive operations at the same time in route order.
Check vehicle compatibility and transfer-facility storage. Assess scenario
timing and precedence under the model definition. Generate insertion proposals
by selecting compatible vehicle-day routes and stop positions uniformly,
while maintaining chain order, up to
`VrpOperatorCatalogSettings.max_candidate_alternatives`. Rank all completed
qualifying candidates first by their maximum capacity excess across all
`LoadIntervals`, lowest first. If an `objective_id` is registered, use its
value to break ties; otherwise resolve ties uniformly. Return the ordered
list up to `CandidateContext.max_candidates`; every proposal is a final
complete candidate.

**Size.** k is the number of package chains selected for relocation and
reinsertion.

**Modification count.** m is the number of selected chains whose final route
assignment, stop positions, or transfer-operation positions differ from the
working plan immediately before their move. If none changes, return an empty
candidate list.

### 4.9 Potentially binding personnel seat capacity

**Seat-aware intervention-team reassignment.** Select a route whose crew size
equals its vehicle's seat capacity; when none exists, select a route with the
largest crew. Select an intervention on that route uniformly. Generate up to
32 distinct replacement teams from authorized personnel whose qualifications
cover the intervention's requirements and whose team size meets its minimum.
Update `team_member_ids`, derive the route crew from the driver and all
intervention teams, and reject a team that makes the crew exceed seat
capacity. A registered `objective_id` orders complete candidates by ascending
objective value; without one, order candidates meeting the feasibility
request uniformly. Return up to `CandidateContext.max_candidates` proposals.

**Size.** k is the maximum number of intervention teams changed.

**Modification count.** m is the number of intervention stops assigned a
different team.

### 4.10 Package–vehicle compatibility

**Compatibility-guided package-chain repair.** Select uniformly among package
requests whose current carrier sequence includes an incompatible vehicle. If
all current carrier sequences are compatible, select a package with the fewest
compatible vehicles. Remove its pickup, delivery, and paired transfer stops
into an internal partial plan. Preserve service-coverage choices,
transfer-step count, and facility choices. For each candidate trial, sample up
to 32 distinct carrier sequences using compatible vehicles only; the sender
and receiver at every transfer must be distinct and compatible. For each
sequence, sample insertion positions that preserve custody order and rebuild
affected `TransferStep` and route records. Assess the complete repair, then
repeat for up to `k` package chains, updating the trial's working plan after
each successful repair. Each trial starts from the base. A registered
`objective_id` ranks complete candidate plans by ascending objective value;
without one, return qualifying plans in randomized generation order. Return
eligible complete plans up to `CandidateContext.max_candidates`.

**Size.** k is the number of package chains selected for joint repair.

**Modification count.** m is the number of selected chains whose ordered
carrier sequence or pickup, delivery, or transfer-operation positions differ
from the working plan immediately before repair. The shared `RandomSource`
resolves ties among equally eligible chains and carrier sequences.

### 4.11 Package transfer operations

**Transfer-step change.** Register this Operator in exactly one mode:
`add`, `remove`, or `reassign`. In `add` mode, select a package with fewer than
its maximum transfers and insert a paired transfer-out/transfer-in after a
pickup or transfer-in and before the next transfer-out or delivery; when
delivery is omitted, insertion may follow the last completed custody event.
The sender and receiver are distinct compatible vehicles, and the facility
belongs to the package's eligible facility set. In `remove` mode, select one
existing transfer step, delete both operations, and update the carrier
sequence from that step onward so the downstream custody chain continues on
the preceding carrier. Specifically, the package's carrier immediately before
the removed step—the removed step's sender, or the pickup carrier for the
first step—remains its carrier up to the next custody boundary. If the
immediately following `TransferStep` exists, change that step's sender to this
preceding carrier and move its transfer-out stop to that carrier's existing
route on the same day and at the same facility; keep its receiver and
transfer-in stop fixed. Keep every subsequent transfer step and its stops
unchanged. If this makes the next transfer's sender and receiver the same
vehicle, discard the alternative. If delivery is the next custody boundary,
move the delivery stop to the preceding carrier's existing route on the same
day. If the required route or an eligible insertion does not exist, discard
the alternative. When delivery is omitted and no later transfer exists, the
package remains with the preceding carrier through the end of its modeled
custody chain and no downstream stop moves. Rebuild the affected carrier
sequence, paired stop references, and route paths. In
`reassign` mode, select one existing step and change its facility, receiver,
or operation positions while preserving custody order. Its sender remains the
previous custody carrier; changing that sender requires a linked change to
the preceding step and is handled by package-chain repair. Update the
`TransferStep` and paired stop records atomically, renumber later steps, and
rebuild affected route paths. The complete candidate must preserve all
package precedence and transfer constraints and meet the call's feasibility
request. Each valid alternative creates a complete candidate from the base.
Sample at most 32 valid complete alternatives. When an `objective_id` is
registered, return qualifying candidates in ascending objective value,
resolving ties uniformly. Otherwise return them in randomized sample order.
Return up to `CandidateContext.max_candidates` proposals.

**Size.** k is the maximum number of package transfer steps added, removed,
or changed sequentially.

**Modification count.** m is the number of selected transfer-step change units
successfully applied. One added or removed step counts once; dependent carrier,
route, and paired-stop updates are part of that selected change unit.

### 4.12 Transfer-facility storage

**Storage-aware transfer assignment.** Select the transfer step that
contributes to the largest facility-storage excess across scenarios. If every
facility is within capacity, select any transfer step uniformly. Reassign the
step to a different facility in the
package's eligible facility set, update both operation-stop nodes, and rebuild
their incident paths. Keep vehicles, days, and stop positions fixed. Rank
complete candidate plans first by their maximum storage excess across
all facilities and scenarios, lowest first. A registered
`objective_id` breaks ties; without one, ties are resolved uniformly. Return
the ordered candidates up to `CandidateContext.max_candidates`; discard plans
that fail the requested feasibility condition.

**Size.** k is the maximum number of transfer steps reassigned sequentially.

**Modification count.** m is the number of selected transfer decisions whose
facility changes.

### 4.13 Package pickup and delivery

**Package-chain destroy-and-repair.** Select `min(k, n_eligible_chains)`
distinct package requests uniformly, remove their complete custody chains into
an internal partial plan, and preserve each chain's current coverage choices,
transfer count, and facility choices. For each of at most 32 repair trials,
reinsert chains one at a time in stable package-ID order. For a chain, select
uniformly one complete placement assignment from the stable ordered set of
eligible assignments, using the shared random source. An assignment places
each currently served pickup, delivery, and transfer operation on a compatible
vehicle-day route and at a stop position. It retains the chain's transfer
count and facility choices, places each transfer-out and paired transfer-in
at that facility on distinct compatible vehicles, and preserves the ordered
custody sequence. If any chain has no complete placement assignment, discard
that repair trial. Rebuild affected `RouteLeg` and `TransferStep` records.
Assess feasibility and objectives only after all selected chains have been
reinserted, producing a complete `VrpSolution`.
Keep completed trials that meet the requested feasibility condition. An
`objective_id` orders qualifying complete trials by ascending objective value,
resolving ties uniformly; without one, return them in randomized sample
order. Each trial starts from the same base and reinserts the selected chains
as one complete plan. Return up to `CandidateContext.max_candidates` plans.

**Size.** k is the maximum number of package requests selected for removal
and reinsertion; the call selects at most the number of eligible package
chains in the plan.

**Modification count.** m is the number of selected package requests whose
service occurrence, route assignment, stop order, or transfer plan differs
from the plan immediately before reinsertion. A selected request restored
identically contributes zero. Only packages with at least one served activity
or transfer operation are eligible for removal. If no selected request
changes, return an empty candidate list.

### 4.14 Positive service durations

**Service-order impact repair.** Register the dedicated time-window-violation
`objective_id`. For each positive-duration activity occurrence, sum the
downstream service-window penalty contributions for that objective across
later stops on the same route, weighted by scenario probabilities. Select an
occurrence with the largest sum; if every sum is zero, select uniformly among
positive-duration occurrences. Generate up to 32
relocations of that activity to eligible positions on its current or another
eligible route, preserving its type-specific data and dependent references.
Reassess each complete plan and return eligible plans in ascending selected-
objective order, breaking ties uniformly, up to
`CandidateContext.max_candidates`. Service durations remain fixed at their
instance values.

**Size.** k is the maximum number of activity occurrences relocated.

**Modification count.** m is the number of activity occurrences whose final
route or stop position changes.

**Duration-impact timing adjustment.** Register an `objective_id` and
positive `time_step`. Select uniformly a route start or stop wait that occurs
before a positive-duration service. Generate plus/minus `time_step` values
within the field domain, reassess the complete plans, and return eligible plans
in ascending selected-objective order, breaking ties uniformly, up to
`CandidateContext.max_candidates`. If no such timing field
exists, return an empty candidate list.

**Size.** k is the maximum number of timing decisions changed.

**Modification count.** m is the number of timing decisions changed.

### 4.15 Optional service omission

**Service-coverage change.** The factory includes this Operator only when
`service_coverage_mode = penalized_omission`. Each candidate trial selects
one required activity whose current occurrence count is zero or one. If
present, remove its
`ActivityStop`. Removing a package pickup also removes its delivery and every
transfer step, as required by `multiday_vrp.md` §3.2. Removing a delivery
leaves the package on its final carrier through the horizon. If absent, insert
one `ActivityStop` at a sampled eligible vehicle-day and route position,
preserving the package's existing custody chain. Rebuild affected paths; the
evaluator calculates omission penalties. Each trial starts from the base;
sample eligible activities, routes, and positions in randomized order and
return qualifying distinct plans in that order up to
`CandidateContext.max_candidates`.

**Size.** k is the maximum number of activity coverage decisions changed.

**Modification count.** m is the number of activity coverage states changed.
A pickup toggle that also removes dependent delivery and transfer records
counts as one package-pickup coverage change.

### 4.16 Restrictive service windows

The service-wait adjustment, route-start shift, schedule compliance repair,
activity relocation, route-leg rerouting, and time-window-focused
destroy-and-repair Operators register the dedicated time-window-violation
`objective_id` defined in `multiday_vrp.md` §1.8. The wait,
start-time, and schedule-repair variants also register a positive `time_step`.
The three window-prioritized route-search Operators in this section register
one unranked choice and objective-targeted choices for the selected objectives.
They require the dedicated time-window-violation objective in the objective
declaration, even when that objective is not among `objective_targets`.
The service-wait adjustment, route-start shift, activity relocation,
route-leg rerouting, and time-window-focused destroy-and-repair Operators use
`CandidateContext.base_evaluation.analysis.scenario_evaluations` to locate the
activity stop with the largest maximum per-scenario `guidance_value` for their
`objective_id`. Each trace stop's `window_assessments_by_objective` contains
the assessment for that dedicated objective. These fields are defined in §2.3
and the input-format document. Exact
ties are resolved uniformly. When the analysis is absent or contains no
positive guidance value, these Operators choose uniformly among eligible
windowed activities. Their selected objective ranks complete candidate plans
using the shared assessment helper. A service occurrence is identified by
its `ActivityRef`, route, and stop index in the base plan. When complete base
analysis is available, schedule compliance repair selects its route by the raw
time-window violations defined below; without base analysis, it selects among
routes with eligible timing changes as specified below. Its complete-plan
ranking follows the window-first rule below, with its `objective_id` applied
after the whole-plan time-window-violation value.

Time-window penalties are values in their dedicated objective, so
`ensure_feasible=True` still requires all hard requirements in every scenario
while allowing a positive window penalty.

**Window-first candidate selection.** Apply this rule to every completed
candidate considered by the time-window penalty-search Operators below. The
tour-search Operators use the separate strict acceptance and
objective-targeted ranking rules defined below. For the penalty-search
Operators, retain only candidates with the lowest value of the dedicated
time-window-violation objective among the complete alternatives considered in
this call, using that objective's declared `ObjectiveFunction.compare`
operation.
When any considered candidate has zero penalty, this step retains zero-penalty
candidates only. When every considered candidate has a positive penalty, it
retains those with the smallest penalty. Then apply the Operator
registration's usual target-first Pareto or all-objective Pareto ranking to
the retained candidates, using the remaining objectives. Resolve any final
ties uniformly with the shared `RandomSource`.

The rule applies after the `ensure_feasible` request has filtered hard-
feasibility results. A window violation contributes the application-defined
penalty, as specified in `multiday_vrp.md` §3.4 and the input-format document;
the dedicated objective is zero exactly when all included time-window
violations are zero. The rule ranks only the bounded, complete alternatives
generated and assessed by the current call. A positive minimum therefore
means the least violation found in that call's candidate set, rather than a
claim about the entire neighborhood. If the call produces no complete
candidate, it returns an empty candidate list under the common Operator contract.

**Tour-search objective and feasibility contract.** The three tour-search
specifications in §1.2 register objective-targeted variants, one for each
selected objective other than the dedicated time-window-violation objective.
Use the target `ObjectiveFunction` to score and compare insertion alternatives
and complete candidates. This binds the search to the declared objective's
units and direction: a distance objective uses distance, while a driving-time
or route-duration objective uses its own declared measure. When the objective
provides a route-level contribution, the search can use its contribution or
delta; otherwise it compares completed candidates through the shared
complete-plan assessment. Do not prioritize an independent distance or time
surrogate.

After rebuilding network paths and assessing a complete candidate, reject it
if the modified route has any service-window violation in any evaluated
scenario. Apply this strict filter regardless of `ensure_feasible`, because
time-window penalties are otherwise soft objectives in this model. This filter
guarantees the changed route's compliance; it does not certify unchanged routes
in the rest of the plan. Rank survivors with the selected objective's declared
`ObjectiveFunction.compare` operation and the registration's normal
objective-targeted candidate-ranking policy. Resolve final ties uniformly.

**Objective-guided regret tour reinsertion.** Keep package assignments fixed.
The initial implementation scope is an eligible route containing complete
pickup/delivery pairs, with all pickups at the route's start depot, pickups
before deliveries, and no transfers, interventions, post-service waits, or
intermediate tour boundaries. Select one eligible route and remove up to `k`
delivery stops. Reinsert them one at a time at positions that preserve a
feasible earliest-time schedule for the route. Compute regret from the selected
objective's best and second-best feasible insertion outcomes; give a stop with
only one feasible position priority. Recalculate planned pre-service waits,
rebuild route paths, and apply the strict scenario-level acceptance filter
above. This version uses greedy regret insertion without backtracking or an
embedded 2-opt phase.

The bounded insertion neighborhood follows the generalized-insertion idea but
does not claim global optimality. Its delivery-only eligibility avoids
reordering tours with interleaved pickups, transfer precedence, or other
service types.

- **Size:** k is the maximum number of delivery stops removed and reinserted.
- **Modification count:** m is the number of selected delivery stops whose
  positions changed; m is at most k.

**Objective-guided backtracking tour construction.** Use the same fixed-
assignment route scope and strict scenario-level acceptance rule. Build a
delivery order by feasible insertion, checking whether each remaining delivery
still has at least one feasible insertion position. When an insertion leaves
an unplaceable remainder, undo recent insertions and explore another position
or delivery choice, subject to a registered search bound. Complete and assess
each surviving route order, then rank by the selected objective. This version
adds bounded lookahead and backtracking to the regret version's greedy choice;
it trades additional evaluations for fewer dead ends.

- **Size:** k is the maximum number of deliveries that may be pending in the
  route reconstruction neighborhood.
- **Modification count:** m is the number of delivery positions changed in the
  accepted complete route.

**Objective-guided window-feasible delivery edge exchange.** Start from an
eligible route and consider improving reversals or relocations within its
delivery order. Use forward and backward time-window slack to screen 2-opt or
Or-opt candidates, then verify each completed move against every scenario
before acceptance. Rebuild the route paths and rank feasible improvements by
the selected objective. Generic 2-opt already exists; this specification is
distinct because time-window compliance is a hard filter for each accepted
route move and the objective target determines improvement.

- **Size:** k is the maximum number of consecutive delivery stops changed by
  one edge-exchange move.
- **Modification count:** m is the number of delivery stops whose positions
  changed; m is at most k.

**Service-wait adjustment.** Select a targeted occurrence and change its
`pre_service_wait` to one of the following values: its current value plus or
minus `time_step`, or `max(0, ell - arrival)` or
`max(0, u - service_duration - arrival)` for a scenario arrival and one of
the activity's window alternatives `[ell, u]`. Retain values in the field
domain, remove duplicates, and sample at most 32 distinct alternatives. The
candidate scoring determines which direction reduces the selected objective.

- **Size:** k is the maximum number of `pre_service_wait` fields changed.
- **Modification count:** m is the number of effective
  `pre_service_wait`-field changes.

**Route-start shift.** Select a route containing a targeted occurrence.
Generate candidate values from the current `start_time` plus or minus
`time_step`, and from `start_time + delta` for each
`delta = ell - service_start[s]` and
`delta = u - service_finish[s]`, using every observed scenario `s` and
applicable window `[ell, u]`. These deltas are proposal values; recalculate
scenario timing for each candidate. Keep values in the route's allowed start
interval, deduplicate, and sample at most 32. Rank complete plans by the
registered objective.

- **Size:** k is the maximum number of route start times changed.
- **Modification count:** m is the number of routes with changed `start_time`.

**Time-window schedule compliance repair.** Keep the selected route's service
order, route assignment, road paths, depot visits, and all non-timing fields
fixed. The timing fields eligible for change are the route's `start_time` and
the `pre_service_wait` of each non-depot stop. The Operator may change at
most k distinct timing fields.

When `CandidateContext.base_evaluation` contains complete base analysis,
calculate the window-violation score defined below for each route using its
windowed activities and all scenarios. Select uniformly among routes with the
lexicographically greatest positive score. When every route score is zero,
select uniformly among routes with at least one eligible timing field. For
every windowed activity, alternative window `[ell, u]`, and scenario trace on
that route, calculate
`delta_start = ell - service_start` and
`delta_finish = u - service_finish`. For each eligible timing field,
generate candidate values from its current value plus or minus `time_step`
and from its current value plus each calculated delta.

When the context has no complete base analysis, including the second
component call in a composition, do not assess the input plan or derive
scenario-dependent deltas from it. Select uniformly among routes with at
least one eligible timing field and generate candidate values only by adding
or subtracting `time_step` from the field's current value. This mode lets the
Operator repair a composition's intermediate plan without evaluating that
intermediate value.

In both modes, deduplicate proposed values, retain those in the field's valid
domain, and recalculate each generated complete schedule because time-
dependent travel can change downstream times. The shared `VrpPlanAssessor`
may assess generated schedule alternatives for internal ranking; it receives
the proposed schedules, not the composition's intermediate input plan. The
generic engine submits each final schedule in the composite result's order.

Generate at most
`VrpOperatorCatalogSettings.max_candidate_alternatives` schedule proposals.
First remove fields with no valid non-current proposed value; `n_fields` is
the number of fields that remain. For each proposal, choose uniformly a
number r from 1 through `min(k, n_fields)`, choose r distinct eligible timing
fields uniformly, and choose one non-current proposed value uniformly for
each selected field. Discard unchanged or duplicate complete assignments.
All draws use the shared `RandomSource`. Discard assignments that fail the
requested hard-feasibility condition.

For a complete candidate plan, the violation amount for one windowed activity in one
scenario is the minimum, over its alternative windows `[ell, u]`, of
`lower_violation(service_start, ell) + upper_violation(service_finish, u)`.
Its window-violation score is the pair (1) the number of activity-scenario
pairs with a positive violation amount and (2) the scenario-probability-
weighted sum of those amounts. Use this score to identify routes for repair.
Rank completed candidate plans using the common window-first candidate
selection rule above, then the registration's objective ranking. The base
plan is context for choosing repair targets; the generated alternatives form
the candidate list. Return the highest-ranked qualifying plans first, up to
`CandidateContext.max_candidates`. A zero score means every windowed
activity meets one of its allowed windows in every scenario. An empty
candidate list records that the bounded search inspected by this call found
no qualifying plan.

- **Size:** k is the maximum number of timing fields changed in the selected
  route.
- **Modification count:** m is the number of changed `start_time` and
  `pre_service_wait` fields.

**Time-window-guided activity relocation.** Select the targeted activity
occurrence and move its `ActivityStop` to an insertion position on the same or
another eligible vehicle-day route. Preserve the stop's type-specific fields
and all package and relationship references; discard any insertion that
violates solution representation rules. Rebuild affected paths, then rank up
to 32 complete plans by the registered objective.

- **Size:** k is the maximum number of activity occurrences relocated.
- **Modification count:** m is the number of selected occurrences whose final
  route or stop positions differ from the working plan immediately before the
  move.

**Time-window-guided route-leg rerouting.** Select uniformly one leg on the
target activity's route from the route start through the leg immediately
preceding the activity. Sample at most 32 different loopless paths under
§2.2, substitute each into a complete plan, and rank by the registered
objective value. Recalculate all scenario arrival and service times for each
candidate.

- **Size:** k is the maximum number of route legs rerouted.
- **Modification count:** m is the number of legs whose path changes.

**Time-window-focused destroy-and-repair.** Select `min(k, n_targeted)`
distinct served activity occurrences in descending order of their maximum
per-scenario penalty for the registered objective, resolving ties uniformly.
Remove those stops into an internal partial plan. Generate up to 32 complete
repair trials. In each trial, reinsert the removed activities one at a time in
the selected order, choosing an eligible route and insertion position
uniformly for each activity. Discard a trial if an activity has no eligible
insertion. Assess feasibility and objectives only after all selected
activities are reinserted. Keep completed trials that meet the requested
feasibility condition and return the completed plans in ascending registered
objective order, breaking ties uniformly, up to `CandidateContext.max_candidates`.
If no trial completes, return an empty candidate list.

- **Size:** k is the number of activity occurrences removed and reinserted.
- **Modification count:** m is the number of selected occurrences whose service
  occurrence, route, stop order, or transfer plan changes.

**Time-window regret-based service reinsertion.** Consider served service
units that contain at least one time-windowed activity. A package request is
one atomic package-chain unit; an intervention or other independent service
is one activity unit. Each candidate trial starts from the base. For each
eligible unit, remove its current occurrence or chain from that trial's
working copy and form complete placements in the same or another active
route. An activity-unit placement inserts its stop at one route position. A
package-chain placement follows the custody-preserving chain placement rule
in §4.13. Sample up to
`VrpOperatorCatalogSettings.max_candidate_alternatives` distinct complete
placements without replacement using the shared random source, in stable
route and stop order before randomization. Consider only placements that
change the unit's current route or stop positions. Rebuild affected paths and
assess each complete plan. Retain alternatives that satisfy the requested
feasibility condition and have a valid VRP objective value: a finite Python
`float` or `math.inf` for a hard-infeasible plan.

For each unit with at least one retained alternative, let `f1` and `f2` be
the lowest and second-lowest objective values among its alternatives, under
the registered objective's ordering. This is a bounded regret measure over
the sampled complete plans for that unit. Finite VRP objective values are Python
`float` values and smaller values are preferred; `math.inf` represents a
hard-infeasible plan under `multiday_vrp.md` §1.8. A unit with exactly one
retained alternative has priority over units with two or more; among such
single-alternative units, choose the lowest `f1`, breaking ties uniformly.
For a unit with multiple alternatives, regret is `f2 - f1` when both values
are finite, `math.inf` when `f1` is finite and `f2` is `math.inf`, and zero
when both are `math.inf`. Choose the unit with greatest regret, then the
lowest `f1`, resolving remaining ties uniformly with the shared random
source. The selected unit's best alternative is the one with value `f1`; ties
among equally valued placements are resolved uniformly. Apply the selected
unit's best alternative and repeat for at most k distinct units, recomputing
alternatives and regret after each move. Skip a unit with no retained
alternative for the current step. Repeat the bounded construction with new
random samples and tie breaks to form further distinct candidate plans from
the base, considering at most
`VrpOperatorCatalogSettings.max_candidate_alternatives` complete trials.
Order completed plans by the common window-first rule and then the
registration's objective rule, resolving remaining ties uniformly. Return up
to `CandidateContext.max_candidates` plans; if no unit can be moved in a
trial, that trial contributes no proposal.

- **Size:** k is the maximum number of distinct service units relocated.
- **Modification count:** m is the number of selected units whose route or
  stop positions changed.

The slack-guided Or-opt move follows time-window-aware k-interchange and
Or-opt principles [5]. The free-insertion exchange adapts SWAP* neighborhoods
[3, 7]. The route-consolidation ejection chain draws on the ejection-pool
approach to route minimization [6].

#### Window slack used by the focused route moves

For a windowed activity occurrence `a` in scenario `s`, let `start[a,s]` and
`finish[a,s]` be its service start and finish in the scenario trace. For each
allowed window `[ell, u]`, its margin is
`min(start[a,s] - ell, u - finish[a,s])`. Its scenario slack is the greatest
margin over the activity's allowed windows. Its plan slack is the smallest
scenario slack across the ScenarioBank. A value below zero by more than the
numeric feasibility tolerance in `multiday_vrp.md` §1 identifies a violation;
a smaller slack identifies a more time-critical occurrence. Values within
the tolerance of zero count as zero slack. A repair unit's slack is the
smallest plan slack among its windowed activity occurrences. If a required
trace value is unavailable, that occurrence has unknown slack and is ordered
uniformly with other unknown occurrences.

#### Slack-guided Or-opt chain relocation

**Move.** Use the block and insertion semantics of the generic Or-opt chain
relocation in §3.1. Select only blocks containing a time-windowed activity.
When complete base analysis is available, order eligible blocks by the
smallest plan slack among their windowed activities, lowest first. Break ties
uniformly. Without complete base analysis, choose uniformly among eligible
blocks. Sample at most
`VrpOperatorCatalogSettings.max_candidate_alternatives` distinct insertion
proposals using the shared random source. Rebuild affected paths and assess
each complete plan. Apply the common window-first candidate selection rule in
this section, then the registration's objective ranking.

**Size.** k is the number of stop occurrences in the one block moved by the
call, as for generic Or-opt.

**Modification count.** m = k when the block is moved successfully;
otherwise the call returns an empty candidate list.

#### Window-prioritized SWAP* free-insertion exchange

**Move.** Use the free-insertion exchange structure of the generic SWAP*
Operator in §3.1, with each selected stop generalized to its complete repair
unit from §3.1. Select two repair units whose complete occurrences are
contained in distinct vehicle-day routes for the same day, with at least one
unit containing a time-windowed activity. Remove both units, then place each
unit in the other route using the applicable legal-placement rule in §3.1
and §4.13. Preserve all package chains and declared relationships.
When complete base analysis is available, prioritize pairs containing a unit
with the lowest plan slack; break ties uniformly. Without complete base
analysis, choose eligible pairs uniformly. Generate at most
`VrpOperatorCatalogSettings.max_candidate_alternatives` complete alternatives
per attempted exchange. Rebuild affected paths, assess every complete plan,
and apply the common window-first candidate selection rule in this section,
then the registration's objective ranking. A candidate must also satisfy the
requested hard-feasibility condition.

**Size.** k is the maximum number of repair-unit pair exchanges applied
sequentially to the working plan.

**Modification count.** m is the number of completed repair-unit pair
exchanges.

#### Window-prioritized ejection-chain route consolidation

**Move.** An eligible source is a vehicle-day route containing at least one
complete repair unit, with every repair unit touched by it wholly contained
in that route. Its receiving routes are all active routes on the same day
assigned to other vehicles. Sample up to
`VrpOperatorCatalogSettings.max_candidate_alternatives` eligible source
routes without replacement, in stable route order before randomization.
Generate a consolidation alternative for each sampled source route and
reassign all its repair units so that route can be removed. While constructing
that alternative, retain at most `CandidateContext.max_candidates` partial
plans between unit-reassignment steps. Preserve each unit's package custody,
precedence and co-location requirements, service coverage, and internal order.

For a selected source route, consider its repair units in ascending order of
the number of sampled direct placements that meet the requested hard-
feasibility condition across the receiving routes; sample at most
`min(VrpOperatorCatalogSettings.max_candidate_alternatives,
CandidateContext.max_candidates)` direct placements per unit, using one
sampled path per changed leg for this feasibility count. Break ties by lowest
repair-unit slack, then uniformly. Units with unknown slack join the tie group
and are ordered uniformly. When complete base analysis is unavailable, order
units uniformly. Recompute this order for every retained partial plan after
each completed unit reassignment.
For a pending unit, first consider its legal insertion positions in the
receiving routes. Also consider bounded
ejection chains: insert the pending unit in one receiving route, move one
repair unit already on that route to another receiving route, and continue
moving displaced units until the last receiving route accepts its unit
directly. The chain may displace at most
`VrpOperatorCatalogSettings.max_ejection_chain_depth` existing repair units.
Receiving routes within one chain are distinct.
The complete chain reassigns every affected unit and the pending source unit;
the source route is removed only after all its units have been reassigned.
Each insertion position and complete chain is a proposal and counts toward
`VrpOperatorCatalogSettings.max_candidate_alternatives` for that construction
step. Rebuild affected paths and use the shared assessor to check complete
alternatives, including capacity, time-window penalties, vehicle and
personnel requirements, and package custody. An incomplete internal chain is
kept as search state; the Operator creates a candidate only after the chain
is complete.

Complete candidates are filtered by the requested hard-feasibility
condition, then ranked by the common window-first candidate selection rule in
this section and the registration's objective ranking. Thus a zero-window-
penalty consolidation is selected whenever the generated candidate set
contains one; otherwise the selected consolidation has the smallest
time-window-violation objective among the generated alternatives. If no
complete consolidation is generated, return an empty candidate list.

**Size.** k is the maximum number of source vehicle-day routes consolidated
sequentially in one call.

**Modification count.** m is the number of source route records removed
after all their repair units have been reassigned.

All service-window Operators use the shared assessment and random-source
contracts. The analysis-guided moves use `VrpOperatorAnalysis` as described
above. Their move units are specified individually; time-window-focused
destroy-and-repair moves activity occurrences, while regret-based service
reinsertion and the new exchange/consolidation moves operate on complete
repair units.

### 4.17 Interventions

**Intervention relocation.** Select one served intervention uniformly and
sample up to 32 distinct insertion positions across its eligible vehicle-day
routes. Keep its intervention ID and `team_member_ids`. Recompute each
affected route's crew from its driver and all assigned intervention teams;
discard a position that violates representation rules. Rebuild affected paths
and assess every scenario. A registration may
name an `objective_id`; when present, order complete candidates by ascending
objective value, otherwise order candidates meeting the feasibility request
uniformly. Return up to `CandidateContext.max_candidates` proposals.

**Size.** k is the number of intervention activity occurrences relocated.

**Modification count.** m is the number of interventions whose route or stop
position changes.

Intervention-team assignment is defined under the qualifications/team-size
feature (§4.21), and tool-aware placement is defined under the
tool-capacity-reservation feature (§4.18).

### 4.18 Tool-capacity reservations

**Tool-reservation-guided intervention relocation.** For every depot-to-depot
segment, calculate the total weight of its union of required tools. Select an
intervention contributing tools to a segment with the largest capacity excess;
if no segment has excess, select an intervention whose tools contribute the
largest segment tool weight. Sample up to 32 insertion positions on eligible
vehicle-day routes. Each position yields a candidate trial from the base. For
each proposal, assign the intervention to the segment containing its new
stop, recompute that segment's tool union, rebuild affected paths, and assess
all scenarios. Rank candidates first by the maximum tool weight over all
segments, lowest first. If an `objective_id` is registered, use its value to
break ties; otherwise break ties uniformly. Discard plans that fail the
requested feasibility condition and return qualifying plans in this ranked
order up to `CandidateContext.max_candidates`.

**Size.** k is the maximum number of intervention activity occurrences
relocated sequentially.

**Modification count.** m is the number of interventions placed on a
different route or depot-to-depot segment.

This Operator is registered for the tool-capacity-reservations feature. It
is the only definition of this move in the catalog.

### 4.19 Additional activity relationships

**Relationship-guided service repair.** For a precedence requirement `(a,b)`,
select it when `b` is served and `a` is absent, or when `a` does not finish
before `b` in every scenario. Insert a missing prerequisite or relocate `a` to
a route position before `b`; if the pair is on different routes, also sample
eligible route assignments and let full scenario timing determine precedence.
For co-location, compare the fixed service nodes. Equal nodes satisfy the
relation for every placement. Different nodes cannot be reconciled by route
ordering: when omission is permitted, include alternatives omitting `a` and `b`; otherwise
that pair has no candidate alternative.
Each generated candidate preserves package custody, rebuilds paths, and is
ranked by an optional registered `objective_id`; without one, order candidates uniformly
without replacement. Return up to `CandidateContext.max_candidates` proposals. Apply one selected
relationship repair per move unit, then update the working plan before
selecting the next pair.

**Size.** k is the number of declared relationship pairs repaired.

**Modification count.** m is the number of selected relationship pairs whose
member's route, order, or coverage state changes. Target selection uses the
instance requirements and, when available, the corresponding evaluator
findings. A co-location mismatch with mandatory service has no repair move.

### 4.20 Driving-skill restrictions

**Skill-guided driver schedule repair.** Select uniformly among active routes
whose current driver lacks one or more vehicle-required skills. If all routes
have qualified drivers, select a route with the fewest authorized qualified
drivers. Generate up to 32 alternatives by replacing its driver with another
authorized person satisfying all required skills. Keep route, depot, stop,
and intervention-team decisions fixed; recalculate crew membership, seat
capacity, availability, and the person's full duty timeline. An optional
`objective_id` orders qualifying complete candidates by ascending value,
resolving ties uniformly; without one, return qualifying plans in randomized
sample order. Return up to `CandidateContext.max_candidates` proposals.

**Size.** k is the maximum number of route-driver assignments changed
sequentially.

**Modification count.** m is the number of selected routes assigned a
different driver. If every selected route retains its original driver, return
an empty candidate list.

### 4.21 Intervention qualifications or team-size restrictions

**Intervention-team reassignment.** Select one intervention `ActivityStop`
uniformly. Enumerate personnel subsets from `U` that cover `Q_required`, have
at least `team_size_min` members, and satisfy qualification and seat-capacity
requirements when assigned to the route. Each such subset is a candidate
regardless of personnel availability; the evaluator records any resulting
availability penalty. The assigned team becomes part of that route's derived
crew. Draw up to 32 distinct subsets uniformly; replace `team_member_ids` with
one subset and assess the complete plan, including all other interventions
whose crew changes as a result. An optional `objective_id` ranks complete
candidates; without one, order candidates meeting the feasibility request
uniformly. Return eligible plans up to `CandidateContext.max_candidates`.

**Size.** k is the maximum number of intervention teams changed.

**Modification count.** m is the number of intervention stops assigned a
different team.

### 4.22 Restricted personnel availability

**Personnel-assignment repair.** Register an `objective_id` bound to
`personnel_availability`. Select a person-duty pair with the largest
`personnel_availability` penalty contribution for that objective; if analysis
is unavailable, choose uniformly among duties
whose assigned person has a restricted calendar. If the person is the driver,
propose authorized, vehicle-qualified replacement drivers. If the person is
an intervention-team member, propose qualified replacement team subsets that
preserve the minimum team size. Sample at most 32 distinct complete
replacements and reassess every affected duty and route crew. Return qualifying
plans in ascending value for the registered personnel-availability objective,
resolving ties uniformly, up to `CandidateContext.max_candidates`.

**Size.** k is the number of person-duty assignments changed.

**Modification count.** m is the number of person-duty assignments changed.

**Personnel-calendar timing adjustment.** Register a positive `time_step`
and `objective_id`. Select a route with a positive personnel-availability
finding, or any route assigned to a person with restricted availability when
analysis is absent. Propose plus/minus `time_step` changes to its `start_time`,
`pre_service_wait`, and post-service `wait` fields within their domains.
Assess up to 32 complete candidates and
return eligible candidates in ascending selected-objective order, breaking
ties uniformly, up to `CandidateContext.max_candidates`.

**Size.** k is the maximum number of timing decisions changed.

**Modification count.** m is the number of timing decisions changed.

### 4.23 Staff depot continuity

**Staff-continuity schedule repair.** Select a person and one of that person's
consecutive assigned duties whose starting depot differs from the prior duty's
ending depot (or from `staff_initial_depot` for the first duty). If the person
is the driver, replace that route's driver with an authorized, vehicle-
qualified person whose preceding and following duties satisfy depot
continuity. If the person is an intervention-team member, replace that member
with a qualified person whose duty endpoints satisfy continuity. Sample up
to 32 replacements; keep all vehicle routes and depot decisions fixed. Assess
all affected duties and route crews. A registered `objective_id` ranks
complete candidates; without one, order candidates meeting
the requested feasibility condition uniformly. Return up to
`CandidateContext.max_candidates` proposals.

**Size.** k is the maximum number of driver or intervention-team assignments
changed sequentially.

**Modification count.** m is the number of personnel assignments changed.

### 4.24 Work, driving, rest, or HOS rules

**Violation-guided assignment repair.** Register an `objective_id`. Under
`penalty` treatment and a complete base analysis, select the person-duty
finding with the largest penalty contribution for that objective. Under
`compliance` treatment, use the hard-finding measure rule in §2.3, considering
only positive findings for rules selected for compliance. The finding's
`resource_id` identifies the affected person; populated vehicle, day, and
activity fields identify the affected duty. Prioritize replacement
assignments that reduce the selected key's largest known `measure`. For a
driver duty, generate replacement authorized drivers with required vehicle
skills; for an intervention-team duty, generate replacement qualified team
subsets of sufficient size. Keep routes, times, and stops fixed. Sample up to
32 alternatives. Rank complete feasible candidates by the selected objective,
resolving ties uniformly; for infeasible intermediate trials, use only the
selected key's measure rule to order repair trials. Return qualifying
proposals in that order up to `CandidateContext.max_candidates`, and include
only proposals that meet the call's feasibility request. When
analysis has no applicable finding, select a person-duty pair uniformly and
use the same role-specific alternatives.

**Size.** k is the maximum number of person-duty assignments changed.

**Modification count.** m is the number of person-duty assignments changed.

**Violation-guided timing repair.** Register an `objective_id` and positive
`time_step`. Under `penalty` treatment and a complete base analysis, select a
route attached to the largest driver-work, driving-time, break, rest, or HOS
penalty contribution for that objective. Under `compliance` treatment, use the
hard-finding measure rule in §2.3, considering only positive findings for
rules selected for compliance. The finding's resource, vehicle, day, and
activity fields identify the implicated duty and route; if several routes
belong to the duty, select one uniformly. Use a uniform route fallback when no
matching finding exists. Generate timing alternatives by adding or
subtracting `time_step` from the route start and stop waits. For a break
finding, also propose increasing `break_duration` by `time_step` and increase
the same stop's `wait` to at least the new break duration. Keep values within
their domains and assess the full horizon and all scenarios. Under compliance
treatment, use the targeted `measure` to guide internal repair trials for an
infeasible base. Return qualifying complete plans in ascending selected-
objective order, resolving ties uniformly, up to
`CandidateContext.max_candidates`.

**Size.** k is the maximum number of timing fields changed.

**Modification count.** m is the number of timing fields changed.

### 4.25 Multiple objectives

**Objective-targeted activity relocation.** Register one Operator per
`ObjectiveId`. Select one served activity uniformly, sample up to 32
alternative route/insertion positions, and assess each complete plan against
the full objective vector. Return eligible plans in ascending registered-
objective order, breaking ties uniformly, up to
`CandidateContext.max_candidates`. This adds an objective-focused service-order
move to the per-objective route-path Operators in §3.2.

**Size.** k is the maximum number of activity relocations applied
sequentially.

**Modification count.** m is the number of activity occurrences moved.
Each returned proposal is evaluated against the entire objective vector.

**Pareto trade-off route move.** Its registration fixes one `move_family`:
`path`, `timing`, or `activity`. Select uniformly one active route and
generate up to 32 complete alternatives using the corresponding generic move
definition. From those alternatives, retain the ones
nondominated by the standard componentwise minimization relation over the
complete objective vectors, then return them in a uniformly randomized order
up to `CandidateContext.max_candidates`. Exact duplicate objective vectors
remain separate alternatives. The Operator returns these proposals even when
the base dominates every sampled alternative; archive admission remains the
search engine's decision.

**Size.** k is the maximum number of route-decision units applied
sequentially. Each call fixes one `move_family` (`path`, `timing`, or
`activity`) in its registration, so its size scale is stable.

**Modification count.** m is the number of route-decision units applied.

### 4.26 Scenario aggregation

**Aggregation-contribution route repair.** Register an `objective_id` and a
fixed `move_family`, either `path` or `timing`. For each scenario `s`, compute
the selected objective after removing `s` and renormalizing the remaining
scenario probabilities. Its influence is the absolute difference from the
objective value on the full bank. Compute influence only when the base
analysis is complete and every leave-one-out result is finite. Select a
scenario with maximum influence, breaking ties uniformly; otherwise select a
scenario uniformly. For each route leg, sum its arc travel times within each
scenario using the matching `VrpScenarioEvaluation` trace. Use legs with
complete records for all scenarios and select one with the largest absolute
difference between its
travel time in the selected scenario and its probability-weighted mean across
scenarios. If no leg has complete records, or all differences are zero,
select any leg uniformly.
When `move_family = path`, sample up to 32 path replacements using §2.2. When
`move_family = timing`, sample up to 32 upstream timing changes using §3.3.
Assess complete plans on the full scenario bank and return eligible candidates
in ascending selected-objective order, breaking ties uniformly, up to
`CandidateContext.max_candidates`.

**Size.** k is the maximum number of route-leg path changes when
`move_family = path`, or timing-field changes when `move_family = timing`.

**Modification count.** m is the number of changed route legs or timing
fields, according to `move_family`.

### 4.27 Planar spatial support

**Daganzo-inspired coordinate strip-sweep route rebuild.** Register one
unranked variant and one variant per selected `ObjectiveId`. This Operator
uses the planar node coordinates to construct geographically coherent
delivery orders and route partitions. Daganzo's analysis of tour length by
zone shape motivates the strip geometry and exploration of strip widths; the
complete VRP evaluator determines the actual network-based costs and
feasibility of every resulting plan (Daganzo, 1984; §6).

The Operator applies to one day and a group of distinct vehicles whose routes
start at the same physical depot node. The start node is derived from the
instance's depot mapping and the vehicle's initial-depot or overnight-depot
continuity data. Each selected route is a single depot-to-depot tour whose
stops are package pickup and delivery activities. Every package in the group
is picked up at the common start node, delivered during the selected day, and
carried by one vehicle throughout. Each stop has an empty team, zero
`break_duration`, and `tour_end=False`; its `pre_service_wait` and `wait`
values travel with that activity when the Operator changes its route or
position. Each route finishes at its existing overnight depot. A plan may
contain other days and routes; those records remain unchanged.
Eligible route groups are formed by
`(day_index, derived_start_depot_node_id)`. The Operator chooses a group with
the largest `min(k, number_of_routes)` value, ordering groups by day,
depot-node ID, and instance vehicle order before random tie resolution. It
then selects
`r = min(k, number_of_routes_in_group)` routes uniformly without replacement
from that group. If no group is eligible, it returns an empty candidate list.

For the selected routes, treat each package delivery as one geographic point
at its delivery node. Let `n` be the number of packages and `A` the area of
the convex hull of their coordinates. For positive `A`, define the reference
strip width `w0 = sqrt(A / n)`. Use eight sweep orientations
`theta = j * pi / 8`, for `j = 0, ..., 7`; five width multipliers
`{0.5, 0.75, 1, 1.5, 2}`; two lateral offsets `{0, w/2}`; and both sweep
directions. Project coordinates onto axes rotated by `theta`. For a selected
width `w`, assign each point to strip
`floor((lateral_coordinate - minimum_lateral_coordinate - offset) / w)`.
Visit strips in increasing strip index, points within even-indexed strips in
increasing longitudinal coordinate, and points within odd-indexed strips in
decreasing longitudinal coordinate. Resolve coordinate ties by
`(delivery_node_id, package_id)`. Reverse the complete sweep order for the
opposite direction. These finite grids are catalog choices for exploring
strip geometries, inspired by Daganzo's strip-based construction.

When `A = 0` and at least two distinct delivery coordinates exist, use one
strip and order points along the line through the farthest pair of delivery
coordinates in increasing projected coordinate. Resolve ties between
farthest pairs by sorting each pair's endpoints by
`(delivery_node_id, package_id)`, then selecting the lexicographically first
pair. Orient the line from the endpoint with the smaller `(x, y)` coordinate
to the other endpoint, then reverse that order for the opposite direction.
If all delivery coordinates coincide, use the stable
`(delivery_node_id, package_id)` order and its reverse. These cases retain a
well-defined candidate order when the delivery geometry is collinear or
co-located.

Partition each sweep order into `r` nonempty contiguous blocks by choosing
`r - 1` cut positions. Assign the blocks to the selected vehicle IDs using a
permutation. A complete alternative is the tuple consisting of the selected
sweep orientation, width and offset, sweep direction, cut positions, vehicle
assignment, and sampled directed paths for changed consecutive stop pairs.
Sample distinct complete alternatives without replacement through the shared
`RandomSource`, inspecting at most
`VrpOperatorCatalogSettings.max_candidate_alternatives`. Build each route
with its package pickups ordered by the source key
`(instance_vehicle_order, base_pickup_position, package_id)`, where
`base_pickup_position` is the pickup stop's index in its source route's
`stops` tuple, followed by its block's deliveries in sweep order and travel to
its existing overnight depot.
Build changed route legs under §2.2, reusing a base leg path whenever the same
directed endpoint pair remains consecutive in the same vehicle route. Preserve
each selected route's `vehicle_id`, `day_index`, `driver_id`, `start_time`,
and `overnight_depot_id`; replace only its stops and corresponding legs. The
full shared plan assessor checks each complete plan; capacity, compatibility,
time-window penalties, all other objectives, and the requested feasibility
condition use the authoritative application rules. Keep the selected route
set, package coverage, and all untouched plan records fixed. Collapse
canonical duplicate plans before returning candidates. The common Operator
contract in §2 determines objective-specific ranking, unranked ordering, and
the output limit.

**Size.** `k` is the maximum number of same-day route records jointly rebuilt.
The call selects `r = min(k, number_of_routes_in_the_selected_eligible_group)`
routes.

**Modification count.** `m` is the number of selected vehicle routes whose
package-to-vehicle assignment, ordered package-delivery sequence, or directed
leg path sequence differs from the base. Return only plans with `1 <= m <= k`;
if no sampled complete plan changes a selected route, return an empty
candidate list.

When a feature has several active objectives or feature instances, register
separate Operators only when their move strategy or target history is
intentionally distinct. A feature gate never creates duplicate registrations
of the same Operator.

## 5. Feasibility-restoration capability

The `feasibility_restoration` registration flag tells the search whether an
Operator may be selected when its base is infeasible and its call requests
`ensure_feasible=True`. A `True` registration accepts an infeasible base and
handles the full move sequence as one internal repair attempt. It returns an
ordered list containing only candidates that the shared assessment helper
confirms satisfy every hard requirement in every scenario. If its bounded
move search finds no such plan, it returns an empty candidate list. The flag
guarantees the feasibility of every successful proposal; the bounded search
can return no candidate for an infeasible base outside the Operator's repair
scope.

The feasibility request governs complete alternatives generated from the
base. The Operator uses the infeasible base to construct repair moves, assesses
each complete alternative, and includes a proposal only when its assessment
confirms feasibility. The repair call starts from its supplied base whether
the base is feasible or infeasible.

A `False` registration is ineligible as a standalone choice for a restoration
iteration. Every call it receives still follows the generic `ensure_feasible`
result guarantee. It may be the first Operator in a restoration-capable
composition: the composition is eligible by the second Operator's flag, and
its first call receives `ensure_feasible=False`, as defined in the generic
algorithm design.

The current default catalog assigns the following values. Every
objective-targeted registration generated from one specification inherits
that row's flag.
The family name in the first column applies to every matching specification
ID in §1.2. In particular, the `add`, `remove`, and `reassign` transfer-step
specifications share the `Transfer-step change` row; the three Pareto
trade-off families share the `Pareto trade-off route move` row; and the two
aggregation-contribution families share the `Aggregation-contribution route
repair` row.
“Restoration focus” identifies the hard-feasibility areas the move is designed
to address; the shared assessment checks the complete hard-feasibility
contract for every successful result.

| Operator specification | `feasibility_restoration` | Restoration focus |
|---|---:|---|
| 2-opt | `False` | Feasible-base route-order search |
| Relocate | `False` | Feasible-base stop relocation |
| Or-opt chain relocation | `False` | Feasible-base stop-chain relocation |
| Exchange | `False` | Feasible-base stop exchange |
| 2-opt* suffix exchange | `False` | Feasible-base route exchange |
| SWAP* free-insertion exchange | `False` | Feasible-base route exchange |
| Vehicle-route consolidation, best insertion | `False` | Feasible-base route reassignment, capacity, route-timing, and assignment feasibility |
| Vehicle-route consolidation, regret insertion | `False` | Feasible-base route reassignment, capacity, route-timing, and assignment feasibility |
| Vehicle elimination, best insertion | `False` | Feasible-base multi-route reassignment and vehicle deactivation |
| Vehicle elimination, regret insertion | `False` | Feasible-base multi-route reassignment and vehicle deactivation |
| Vehicle-route split | `False` | Feasible-base route reassignment, capacity, route-timing, and assignment feasibility |
| Window-compatible package-cluster reassignment | `False` | Feasible-base package-to-vehicle reassignment, capacity, package custody, route timing, and service-window assessment while preserving the active fleet |
| Spatial/time-window route-cluster rebuild | `False` | Feasible-base route regrouping with package custody, vehicle compatibility, capacity, and schedule assessment |
| Daganzo-inspired coordinate strip-sweep route rebuild | `False` | Feasible-base geographic ordering and route partitioning with package custody, capacity, route paths, and schedule assessment |
| Route-leg reroute | `True` | Directed-path and daily return feasibility |
| Objective-specific shortest-path Operator | `True` | Directed-path and daily return feasibility |
| Timing adjustment | `True` | Route timing and daily return feasibility |
| Driver reassignment | `True` | Driver authorization, skills, and assignment feasibility |
| Departure-time profile adjustment | `True` | Scenario-dependent route timing and daily return feasibility |
| Scenario-differential reroute | `True` | Scenario-dependent route paths and daily return feasibility |
| Scenario-differential timing adjustment | `True` | Scenario-dependent timing and daily return feasibility |
| Event-objective route reroute | `False` | Feasible-base event-objective search |
| Driver swap or cycle | `True` | Driver qualification and assignment feasibility |
| Driver schedule destroy-and-repair | `True` | Driver qualification and schedule feasibility |
| Overnight-depot change | `True` | Eligible depot and route-continuity feasibility |
| Multi-day depot reassignment | `True` | Multi-day depot-continuity feasibility |
| Split tour | `True` | Tour-count, route-timing, and daily return feasibility |
| Merge tours | `True` | Tour-count, route-timing, and daily return feasibility |
| Depot-visit insertion | `True` | Depot-to-depot tool-capacity, route timing, and work-rule feasibility |
| Depot-visit removal | `True` | Depot-to-depot tool-capacity, route timing, and work-rule feasibility |
| Availability-guided timing adjustment | `False` | Feasible-base availability-penalty search |
| Availability-guided stop relocation | `False` | Feasible-base availability-penalty search |
| Capacity-aware package-chain relocation | `True` | Vehicle cargo and tool-capacity feasibility |
| Seat-aware intervention-team reassignment | `True` | Crew seat-capacity feasibility |
| Compatibility-guided package-chain repair | `True` | Package–vehicle compatibility feasibility |
| Transfer-step change | `True` | Package custody and transfer feasibility |
| Storage-aware transfer assignment | `True` | Transfer-facility storage feasibility |
| Package-chain destroy-and-repair | `True` | Package coverage, custody, precedence, and capacity feasibility |
| Service-order impact repair | `True` | Route order and daily return feasibility |
| Duration-impact timing adjustment | `True` | Route timing and daily return feasibility |
| Service-coverage change | `True` | Coverage and dependent package-chain feasibility in omission mode |
| Service-wait adjustment | `False` | Feasible-base service-window penalty search |
| Route-start shift | `False` | Feasible-base service-window penalty search |
| Time-window schedule compliance repair | `False` | Feasible-base search for a schedule with fewer window violations across scenarios |
| Time-window-guided activity relocation | `False` | Feasible-base service-window penalty search |
| Time-window-guided route-leg rerouting | `False` | Feasible-base service-window penalty search |
| Time-window-focused destroy-and-repair | `False` | Feasible-base service-window penalty search |
| Time-window regret-based service reinsertion | `False` | Feasible-base service-window-aware insertion and route reassignment |
| Objective-guided feasible-tour regret reinsertion | `False` | Feasible-base package-tour improvement ranked by the selected objective with strict changed-route window compliance across scenarios |
| Objective-guided backtracking tour construction | `False` | Feasible-base package-tour construction with bounded lookahead, backtracking, objective ranking, and strict changed-route window compliance |
| Objective-guided feasible delivery edge exchange | `False` | Feasible-base delivery-order edge exchanges ranked by the selected objective and checked for strict changed-route window compliance |
| Slack-guided Or-opt chain relocation | `False` | Feasible-base service-window-prioritized block relocation |
| Window-prioritized SWAP* free-insertion exchange | `False` | Feasible-base service-window-prioritized exchange |
| Window-prioritized ejection-chain route consolidation | `False` | Feasible-base route consolidation with bounded ejections and window-prioritized placement |
| Intervention relocation | `True` | Intervention-day and route feasibility |
| Tool-reservation-guided intervention relocation | `True` | Tool-capacity feasibility |
| Relationship-guided service repair | `True` | Declared precedence and co-location feasibility |
| Skill-guided driver schedule repair | `True` | Vehicle driving-skill feasibility |
| Intervention-team reassignment | `True` | Intervention qualification, team-size, and crew feasibility |
| Personnel-assignment repair | `True` | Personnel assignment and hard work-rule feasibility |
| Personnel-calendar timing adjustment | `False` | Feasible-base personnel-availability penalty search |
| Staff-continuity schedule repair | `True` | Staff depot-continuity feasibility |
| Violation-guided assignment repair | `True` | Compliance-treated work, driving, rest, and HOS feasibility |
| Violation-guided timing repair | `True` | Compliance-treated work, driving, rest, and HOS feasibility |
| Objective-targeted activity relocation | `False` | Feasible-base objective search |
| Pareto trade-off route move | `False` | Feasible-base multiobjective search |
| Aggregation-contribution route repair | `False` | Feasible-base scenario-aggregation search |

The generic route-leg reroute and timing-adjustment registrations are always
present and restoration-capable. They give every default catalog at least one
restoration choice; feature-gated registrations add repair choices when their
features are active. These calls return an empty candidate list when their bounded move search finds
no fully feasible plan.

All Operators follow the generic callback contract for invalid results,
`ensure_feasible`, state, random draws, and empty results; see
multiobjective-vns.md §§5.9 and 6.6. Infeasible excursion behavior is defined
in [multiobjective-vns-algorithm.md](multiobjective-vns-algorithm.md).

## 6. Literature

1. Potvin, J.-Y., and J.-M. Rousseau. 1995. “An Exchange Heuristic for
   Routeing Problems with Time Windows.” *Journal of the Operational Research
   Society* 46(12): 1433–1446.
   [https://doi.org/10.1057/jors.1995.204](https://doi.org/10.1057/jors.1995.204).
   Introduces 2-opt* and reports strong results from combining it with Or-opt
   for time-window routing.
2. Ropke, S., and D. Pisinger. 2006. “An Adaptive Large Neighborhood Search
   Heuristic for the Pickup and Delivery Problem with Time Windows.”
   *Transportation Science* 40(4): 455–472.
   [https://doi.org/10.1287/trsc.1050.0135](https://doi.org/10.1287/trsc.1050.0135).
   Develops an adaptive large-neighborhood heuristic for paired pickup and
   delivery requests, with search heuristics selected according to historical
   performance.
3. Vidal, T. 2020. “Hybrid Genetic Search for the CVRP: Open-Source
   Implementation and SWAP* Neighborhood.” Technical report, PUC-Rio.
   [https://w1.cirrelt.ca/~vidalt/papers/HGS-CVRP-2020.pdf](https://w1.cirrelt.ca/~vidalt/papers/HGS-CVRP-2020.pdf).
   Describes relocate, swap, 2-opt, 2-opt*, and SWAP* neighborhoods, including
   the free-insertion exchange used here as inspiration.
4. Solomon, M. M. 1987. “Algorithms for the Vehicle Routing and Scheduling
   Problems with Time Window Constraints.” *Operations Research* 35(2):
   254–265. [https://doi.org/10.1287/opre.35.2.254](https://doi.org/10.1287/opre.35.2.254).
   Reports strong results for insertion heuristics that account for both
   geographical and temporal placement criteria.
5. Savelsbergh, M. W. P. 1985. “Local Search in Routing Problems with Time
   Windows.” *Annals of Operations Research* 4(1–4): 285–305.
   [https://doi.org/10.1007/BF02022044](https://doi.org/10.1007/BF02022044).
   Develops time-window-aware k-interchange and Or-opt moves, using schedule
   slack information to check feasibility and guide moves.
6. Nagata, Y., and O. Bräysy. 2009. “A Powerful Route Minimization Heuristic
   for the Vehicle Routing Problem with Time Windows.” *Operations Research
   Letters* 37(5): 333–338.
   [https://doi.org/10.1016/j.orl.2009.04.006](https://doi.org/10.1016/j.orl.2009.04.006).
   Uses an ejection pool and insertion to reduce routes; this catalog adopts
   the displacement-chain idea while ranking complete plans by their
   time-window penalty.
7. Kool, W., J. Olde Juninck, E. Roos, K. Cornelissen, P. Agterberg, J. van
   Hoorn, and T. Visser. 2022. “Hybrid Genetic Search for the Vehicle Routing
   Problem with Time Windows: a High-Performance Implementation.” 12th DIMACS
   Implementation Challenge Workshop.
   [Paper and project page](https://wouterkool.github.io/publication/hgs-vrptw/).
   Combines relocate, 2-opt*, and SWAP* moves and filters candidate move pairs
   using spatial and temporal proximity.
8. Daganzo, C. F. 1984. “The Length of Tours in Zones of Different Shapes.”
   *Transportation Research Part B: Methodological* 18(2): 135–145.
   [https://doi.org/10.1016/0191-2615(84)90027-4](https://doi.org/10.1016/0191-2615(84)90027-4).
   Analyzes how zone geometry affects tour length and presents a simple
   tour-building strategy; the coordinate strip-sweep Operator adapts its
   spatial grouping idea to generate complete VRP candidate plans.
9. Gendreau, M., A. Hertz, G. Laporte, and M. Stan. 1998. “A Generalized
   Insertion Heuristic for the Traveling Salesman Problem with Time Windows.”
   *Operations Research* 46(3): 330–335.
   [https://doi.org/10.1287/opre.46.3.330](https://doi.org/10.1287/opre.46.3.330).
   Builds a feasible tour by inserting vertices while checking feasibility of
   the remaining route, with backtracking where needed, then improves it by
   successive removal and reinsertion of all vertices. This is the closest
   literature match for the backtracking tour-construction Operator.
10. Savelsbergh, M. W. P. 1992. “The Vehicle Routing Problem with Time
    Windows: Minimizing Route Duration.” *ORSA Journal on Computing* 4(2):
    146–154. [https://doi.org/10.1287/ijoc.4.2.146](https://doi.org/10.1287/ijoc.4.2.146).
    Develops efficient feasibility and improvement checks for edge-exchange
    moves under time windows, reducing the average feasibility-check effort
    from linear to constant time. This supports fast move screening inside a
    feasible edge-exchange Operator; it is not a route-construction heuristic.
11. Potvin, J.-Y., and J.-M. Rousseau. 1993. “A Parallel Route Building
    Algorithm for the Vehicle Routing and Scheduling Problem with Time
    Windows.” *European Journal of Operational Research* 66(3): 331–340.
    [https://doi.org/10.1016/0377-2217(93)90221-8](https://doi.org/10.1016/0377-2217(93)90221-8).
    Builds routes in parallel and uses a generalized regret measure to select
    the next customer for insertion, informing the regret-based construction
    variant.
