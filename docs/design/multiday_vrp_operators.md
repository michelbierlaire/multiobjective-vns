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
VRP application; the generic multiobjective-vns package receives only the
resulting `OperatorRegistration` values.

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
) -> tuple[OperatorRegistration, ...]
```

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
| `max_candidate_alternatives` | Positive integer in `1..32`, default `32`; limits alternative proposals considered for one attempted move unit. |
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
when its stated gate is active in `VrpFeatureProfile`. Each generated
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
data. It includes `max_candidate_alternatives` for every generated Operator.
For an Operator that uses the built-in path sampler, it also includes
`max_paths_per_leg` and `max_arc_expansion_steps`. IDs and registration order
are deterministic for fixed inputs; changing a limit included in an Operator's
configuration ID changes that ID. The factory captures each applicable limit
in the generated Operator, where it remains fixed for that registration.
For an objective-specific shortest-path Operator, its configuration ID also
includes the `provider_id` and `configuration_id` from the matching
`additive_path_costs` entry, or records that no such entry was supplied and
the sampled-path fallback applies. Changing or removing the callback
registration therefore changes the generated Operator configuration ID.
The returned tuple is ready for
`MultiobjectiveVNS(operators=...)`. The application may remove registrations,
replace a registration with revised settings and a corresponding new
`configuration_id`, or append custom registrations before constructing the
search. `OperatorGroup` values are a separate input for composition
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
requires at least one listed feature to be active.

| `operator_spec_id` | Operator specification | Target mode | Feature gate | Applicable objectives |
|---|---|---|---|---|
| `vrp.generic.two_opt` | 2-opt | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.relocate` | Relocate | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.or_opt_chain_relocation` | Or-opt chain relocation | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.exchange` | Exchange | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.two_opt_star_suffix_exchange` | 2-opt* suffix exchange | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.swap_star_free_insertion_exchange` | SWAP* free-insertion exchange | Independent | Core, always active | All objectives; no single-objective target |
| `vrp.generic.vehicle_route_consolidation` | Vehicle-route consolidation | Unranked + objective-targeted | Core, always active | All declared objectives |
| `vrp.generic.vehicle_elimination_destroy_and_repair` | Vehicle-elimination destroy-and-repair | Unranked + objective-targeted | Core, always active | All declared objectives |
| `vrp.generic.vehicle_route_split` | Vehicle-route split | Unranked + objective-targeted | Core, always active | All declared objectives |
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
| `vrp.vehicle_availability.guided_timing_adjustment` | Availability-guided timing adjustment | Objective-targeted | `restricted_vehicle_availability` | All declared objectives |
| `vrp.vehicle_availability.guided_stop_relocation` | Availability-guided stop relocation | Unranked + objective-targeted | `restricted_vehicle_availability` | All declared objectives |
| `vrp.cargo_tool_capacity.capacity_aware_package_chain_relocation` | Capacity-aware package-chain relocation | Unranked + objective-targeted | `potentially_binding_cargo_tool_capacity` | All declared objectives |
| `vrp.personnel_seat_capacity.seat_aware_intervention_team_reassignment` | Seat-aware intervention-team reassignment | Unranked + objective-targeted | `potentially_binding_personnel_seat_capacity` | All declared objectives |
| `vrp.package_compatibility.compatibility_guided_package_chain_repair` | Compatibility-guided package-chain repair | Unranked + objective-targeted | `package_vehicle_compatibility` | All declared objectives |
| `vrp.package_transfer.add_transfer_step` | Transfer-step change, `add` mode | Unranked + objective-targeted | `package_transfer_operations` | All declared objectives |
| `vrp.package_transfer.remove_transfer_step` | Transfer-step change, `remove` mode | Unranked + objective-targeted | `package_transfer_operations` | All declared objectives |
| `vrp.package_transfer.reassign_transfer_step` | Transfer-step change, `reassign` mode | Unranked + objective-targeted | `package_transfer_operations` | All declared objectives |
| `vrp.transfer_storage.storage_aware_transfer_assignment` | Storage-aware transfer assignment | Unranked + objective-targeted | `transfer_facility_storage` and `package_transfer_operations` | All declared objectives |
| `vrp.package_requests.package_chain_destroy_and_repair` | Package-chain destroy-and-repair | Unranked + objective-targeted | `package_pickup_delivery` | All declared objectives |
| `vrp.positive_service_duration.service_order_impact_repair` | Service-order impact repair | Objective-targeted | `positive_service_durations` | All declared objectives |
| `vrp.positive_service_duration.duration_impact_timing_adjustment` | Duration-impact timing adjustment | Objective-targeted | `positive_service_durations` | All declared objectives |
| `vrp.optional_service_omission.service_coverage_change` | Service-coverage change | Independent | `optional_service_omission` | All objectives; no single-objective target |
| `vrp.service_windows.service_wait_adjustment` | Service-wait adjustment | Objective-targeted | `restrictive_service_windows` | All declared objectives |
| `vrp.service_windows.route_start_shift` | Route-start shift | Objective-targeted | `restrictive_service_windows` | All declared objectives |
| `vrp.service_windows.activity_relocation` | Time-window-guided activity relocation | Objective-targeted | `restrictive_service_windows` | All declared objectives |
| `vrp.service_windows.route_leg_rerouting` | Time-window-guided route-leg rerouting | Objective-targeted | `restrictive_service_windows` | All declared objectives |
| `vrp.service_windows.destroy_and_repair` | Time-window-focused destroy-and-repair | Objective-targeted | `restrictive_service_windows` | All declared objectives |
| `vrp.service_windows.regret_based_reinsertion` | Time-window regret-based service reinsertion | Objective-targeted | `restrictive_service_windows` | All declared objectives |
| `vrp.interventions.intervention_relocation` | Intervention relocation | Unranked + objective-targeted | `interventions` | All declared objectives |
| `vrp.tool_capacity.tool_reservation_guided_intervention_relocation` | Tool-reservation-guided intervention relocation | Unranked + objective-targeted | `tool_capacity_reservations` | All declared objectives |
| `vrp.activity_relationships.relationship_guided_service_repair` | Relationship-guided service repair | Unranked + objective-targeted | `additional_activity_relationships` | All declared objectives |
| `vrp.driving_skills.skill_guided_driver_schedule_repair` | Skill-guided driver schedule repair | Unranked + objective-targeted | `driving_skill_restrictions` | All declared objectives |
| `vrp.intervention_qualifications.intervention_team_reassignment` | Intervention-team reassignment | Unranked + objective-targeted | `intervention_qualifications_or_team_size` | All declared objectives |
| `vrp.personnel_availability.personnel_assignment_repair` | Personnel-assignment repair | Objective-targeted | `restricted_personnel_availability` and `personnel_availability_differentiation` | All declared objectives |
| `vrp.personnel_availability.personnel_calendar_timing_adjustment` | Personnel-calendar timing adjustment | Objective-targeted | `restricted_personnel_availability` | All declared objectives |
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
destroy-and-repair are separate generic specifications because they define
distinct changes to work assignments and active vehicle use. Consolidation
merges two routes, split partitions one route, and vehicle elimination
redistributes one vehicle's service units across routes assigned to other
vehicles before removing its routes. These vehicle-assignment Operators are
available independently of
`multiple_tours_per_day`; the multiple-tour Operators continue to change tour
boundaries within one vehicle-day route.
Their IDs therefore remain distinct in `OperatorGroup`, feedback statistics,
and checkpoint provenance. Their configuration IDs include the row's fixed
variant and all settings that affect its behavior.

## 2. Shared Operator contract

Each call receives an immutable base `VrpSolution`, a positive integer k, an
`ensure_feasible` request, the Operator's registered state, and the shared
`RandomSource`. The Operator returns a new immutable candidate value or reports
that it produced no candidate. Randomized move selection and ordering use the
shared `RandomSource`.

Every returned candidate is representation-valid under
multiday_vrp_solution_design.md §6. With `ensure_feasible=True`, a successful
call returns a plan that satisfies every hard requirement in every scenario.
With `ensure_feasible=False`, the plan may be operationally infeasible while
retaining a representation-valid solution structure. The engine evaluates a
submitted final candidate under the application contract.

For every Operator, m is an integer from 0 through k, measured on the
Operator's declared scale. Unless an Operator defines k as one move's
magnitude, k is the maximum number of its named move units that the call may
apply. A successful call returns one candidate and m > 0. A call with m = 0
returns no candidate because it produced no move under the requested size and
feasibility requirement; it makes no claim about the full neighborhood. Each
Operator below defines its own unit, so m values across Operators are not
directly comparable. When a feature offers variants with different move units,
register each variant as a separate Operator with one fixed k and m scale.

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
configuration values. Unless a definition explicitly ranks moves by a selected
objective, the Operator selects a target uniformly from the eligible targets,
then an alternative uniformly from that target's eligible alternatives, using
`CandidateContext.random_source`. Identifiers and list positions are ordered
stably before each random choice. Candidate alternatives are sampled without
replacement up to `VrpOperatorCatalogSettings.max_candidate_alternatives` per
attempted move unit (default `32`, configurable from `1` through `32`). Every
catalog definition that says “up to 32 alternatives,” “proposals,” “trials,”
or “complete candidates” uses this setting. A candidate that fails structural
checks or the requested feasibility condition is discarded, and the Operator
draws another alternative until it finds one or reaches the cap. A bounded
move attempt ends after the cap is considered, and the Operator may return a
valid partial result. When a bounded sampler supplies a finite candidate set,
an unranked uniform choice is uniform over the eligible alternatives in that
returned set. The sampler determines the membership of this bounded set.

The candidate cap counts each distinct proposal inspected, including a
proposal rejected by structural or feasibility checks. In a path-sampling
context, “up to 32 paths” refers to
`max_paths_per_leg`, and the traversal limit refers to
`max_arc_expansion_steps`. The candidate cap also limits complete plan
alternatives formed from sampled paths, including one-leg path replacements.
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

The Operator builds a working `VrpSolution` and applies at most `k` effective
move units sequentially. It recomputes eligibility after each accepted unit.
With `ensure_feasible=False`, each retained intermediate plan must be
representation-valid. With `ensure_feasible=True` and a feasible base, each
retained intermediate plan must also satisfy all hard requirements in every
scenario. An infeasible base with `ensure_feasible=True` is handled by an
Operator registered with `feasibility_restoration=True`; its repair is one
atomic move sequence whose completed result is assessed before return.

An internal partial plan is a temporary construction state used only inside an
Operator call. Destroy-and-repair Operators may temporarily remove activities
and their dependent records, but they return only a complete, representation-
valid `VrpSolution`. Each atomic move or completed repair trial becomes one
assessment unit; partial construction states are not assessed as candidate
solutions. The application uses one shared pure plan-assessment helper for the
`ApplicationEvaluator` and Operators that need feasibility or objective
screening. The helper applies the model rules in `multiday_vrp.md` and the same
objective declarations. Operator-internal local checks guide construction;
the engine submits and records only the Operator's final candidate. A
completed trial that fails the requested feasibility guarantee is discarded.
If the Operator cannot retain any effective move, it returns `m=0` and no
candidate. If it retains fewer than `k` units, `m` reports that count under
the Operator's declared scale.

An objective-ranked Operator evaluates at most
`VrpOperatorCatalogSettings.max_candidate_alternatives` eligible alternatives
for one target, chooses the one with the smallest value under its registered
`ObjectiveFunction`, and breaks exact objective ties uniformly with the shared
random source. The Operator then proceeds to its next move unit from that
working plan. A multiobjective Operator uses the Pareto rule explicitly
defined for that Operator. A registration that uses objective ranking must
name its `ObjectiveId`; Operators that have no objective ranking use the
uniform selection rule above.

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
unranked moves, selection is uniform among feasible complete plan alternatives
in the bounded candidate set. When one stop
sequence change creates multiple legs, the Operator generates at most
`VrpOperatorCatalogSettings.max_candidate_alternatives`
distinct complete path assignments, sampled without replacement from the
products of the returned per-leg path sets. Each assignment chooses one
sampled path for every changed leg. These assignments form the finite
candidate set for the move. The Operator assesses them under the requested
feasibility condition and, without objective ranking, chooses uniformly among
the feasible assignments. An objective-ranked Operator scores the feasible
assignments or complete plans it generated.

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
calculates the selected objective across the full scenario bank, and returns
the feasible sampled candidate with the best value. Exact ties are resolved
uniformly among the sampled candidates with that value. This fallback covers
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
calculations, penalties, and objective aggregation. The model stops at its
first hard-feasibility failure (§1.8 of `multiday_vrp.md`), so analysis for
such a plan has `is_complete = false` and contains only records created before
that stop, together with the `hard_failure` record for the triggering check;
its `scenario_objective_costs` is empty. Operators use positive
findings present in a partial analysis and use their documented fallback when
the records needed for a target are unavailable. They treat a missing finding
in partial analysis as unknown, not as a zero finding. Each scenario trace
contains the route stops and arc traversals needed by the built-in Operators
and extension callbacks; its schema is defined once in the input-format
document cited above.

`ResourceFinding` contains `kind`, `resource_id: str`,
`scenario_id: ScenarioId | None`, `vehicle_id: VehicleId | None`,
`day_index: positive integer | None`, `activity_ref: ActivityRef | None`,
`measure: finite nonnegative float`, and
`penalty_by_objective: tuple[(ObjectiveId, finite float), ...]`. `kind` is one
of `vehicle_availability`, `personnel_availability`,
`duty_overlap`, `cargo_capacity`, `transfer_storage`, `tool_capacity`,
`driver_work`, `driving_time`, `break`, `rest`, or `hos`. `measure` is the
nonnegative amount of the finding in the model's declared unit. Penalty
guidance values use the objective-specific penalty-function output after
category scaling and before penalty aggregation. They support finding-guided
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

For these moves, a package-chain unit and its associated route-stop
occurrences move together. A package pickup whose pickup node is the
resulting route's starting-depot node belongs in that route's initial depot
pickup block, before the route leaves the depot. Other package-chain stops
retain their relative order from the source route. The shared assessment
checks pickup-before-delivery precedence, custody, and all other plan
relationships.

#### Vehicle-route consolidation

**Move.** Form the ordered pairs of active vehicle-day routes that share a
calendar day and belong to distinct vehicles. Randomly order the pairs without
replacement. Let `n_pairs` be the number of eligible ordered pairs. Consider
the first `min(n_pairs, max_candidate_alternatives)` pairs for each
consolidation move. Divide the candidate-alternative limit across those pairs
as evenly as possible: each pair receives the integer quotient, and the first
pairs in the randomized order receive one additional slot for each remainder.
For each pair, sample up to its allocated number of distinct order-preserving
interleavings uniformly without replacement. Unused slots from a pair with
fewer available interleavings are not reassigned. The cap counts every sampled
pair/interleaving proposal, including proposals later discarded by checks.
This bounds complete proposals while sampling several source/destination
choices. If there are no eligible pairs, return `m = 0`.

Within each pair, the first route is the source and the second is the
destination. Merge their ordered stop sequences into the destination route.
First combine their initial depot pickup blocks: append the source block after
the destination block, preserving the order within each block. A pickup enters
this block when its pickup node equals the destination vehicle's derived
starting-depot node; pickups at other nodes remain in their route's remaining
stop sequence. The source route record is removed, and the destination route
keeps its vehicle, day, driver, start time, and overnight depot. Rebuild all
legs of the merged route using §2.2. Retain source and destination stop types,
activity references, transfer references, team assignments, post-service
waits, breaks, and valid tour markers. Discard alternatives whose resulting
tour count exceeds the destination vehicle's limit or whose full plan fails
the requested feasibility condition. Assess all sampled pair/interleaving
proposals in their sampled order. If no alternative meets these conditions,
the call returns `m = 0`.

For every service or transfer-operation stop in the changed route, rebase
`pre_service_wait` to retain its base plan's probability-weighted mean service
start when possible. Identify an activity stop by its `ActivityRef`, and a
transfer-operation stop by its `TransferStepRef` and role. Let `target[u]` be
the weighted mean service start for stop `u` in
`CandidateContext.base_evaluation.analysis`, and let `arrival[u]` be the
weighted mean arrival for the candidate route after the waits already assigned
to preceding stops have been applied. Set
`pre_service_wait[u] = max(0, target[u] - arrival[u])`, then recalculate
downstream scenario arrivals before processing the next stop. Scenario weights
come from the instance's `ScenarioBank`. When a service-start trace is absent,
retain that stop's current wait. Depot-only-stop timing fields remain attached
to their stops. The full-plan assessor evaluates the resulting plan and its
objective vector.

Removing the source route makes that vehicle-day inactive. Its later
vehicle-day routes retain their stored decisions; their derived starting
depots and paths are rebuilt as needed to satisfy overnight continuity. The
full-plan assessor checks any affected vehicle, driver, package, facility,
and work-rule relationships. A consolidation can reduce the `vehicles_used`
objective when its source vehicle has no remaining active route anywhere in
the planning horizon.

The unranked variant chooses uniformly among accepted complete alternatives
from all considered pairs. An objective-targeted variant chooses the accepted
alternative with the lowest value for its registered objective, resolving
ties uniformly. It therefore ranks candidate pairs as well as their stop
interleavings. Pair ordering, interleaving sampling, and tie-breaking use the
shared random source and the bounded alternative limit from §2.1.

**Size.** k is the maximum number of vehicle-route consolidations applied
sequentially to the working plan.

**Modification count.** m is the number of source route records removed by
completed consolidations. One complete source-route consolidation counts as
one, regardless of how many stops its route contains.

#### Vehicle-elimination destroy-and-repair

**Move.** Select uniformly an eligible active vehicle. For this Operator, a
package request is one atomic package-chain unit containing its pickup,
delivery, and transfer operations; each intervention or other non-package
activity is one activity unit. Connect these atomic units by the declared
precedence and co-location relationships. Each connected component is one
repair unit, so every atomic unit belongs to exactly one repair unit. A source
vehicle is eligible when at least one repair unit has an occurrence on one of
its routes. Depot-only stops and tour-boundary markers belong to the source
route structure and are removed with its route records.

Collect the eligible vehicle's repair units, including every route-stop
occurrence of their package chains and relationship components elsewhere in
the plan. Remove all collected occurrences from every route and remove the
selected vehicle's route records into an internal partial plan. Rebuild the
affected routes and construct repair trials that reinsert every collected
repair unit into existing vehicle-day routes assigned to other vehicles
active in the working plan. A repair may distribute a unit's activities and
package chains across multiple receiving vehicles. Place each package chain
with the custody-preserving chain placement rule in §4.13. Insert each
independent activity at one position in an existing receiving route. For a
relationship component, sample placements for all its member units within the
same trial, in randomized member order; assess the component only as part of
the complete plan. Preserve each unit's service-coverage state, package
custody, relationship requirements, activity references, transfer records,
pre-service and post-service waits, break durations, and intervention team
assignments. Set `tour_end = false` on every reinserted stop because tour
boundaries are decisions of the removed route. Receiving-route drivers, start
times, and overnight depots remain fixed. Rebuild affected paths using §2.2
and assess the complete plan.

Each trial orders the collected units randomly and samples eligible receiving
routes and insertion positions with the shared random source. The trial is
complete only after every collected unit has been assigned. Attempt at most
`VrpOperatorCatalogSettings.max_candidate_alternatives` repair trials for one
source-vehicle elimination; an attempt that cannot place every unit still
uses one slot. Assess only complete plans. Discard trials that fail
representation checks or the requested feasibility condition. The unranked variant chooses
uniformly among accepted trials. An objective-targeted variant chooses the
accepted trial with the lowest value for its registered objective, resolving
ties uniformly. If no complete trial remains, stop the call and return the
current working plan with the number of earlier completed eliminations; when
none completed, return `m = 0`. Partial plans remain internal; the Operator
returns only a complete plan after all units have been reassigned.

Apply up to k vehicle eliminations sequentially. After each successful
elimination, recompute structurally eligible source vehicles and receiving
routes from the updated working plan. The bounded repair search determines
whether a selected source has a complete placement; failure to find one ends
the call with the completed eliminations retained. The shared evaluator checks
vehicle, driver, capacity, service-window, route, depot, transfer, and
work-rule requirements for every accepted trial.

**Size.** k is the maximum number of distinct vehicle IDs eliminated
sequentially in the call.

**Modification count.** m is the number of vehicle IDs whose route records
are removed after all their service units have been reassigned.

#### Vehicle-route split

**Move.** Select uniformly an active vehicle-day route with at least two
movable service units and at least one vehicle with no active route on that
day. A package-chain unit from §2.1 moves atomically with its pickup,
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

The unranked variant chooses uniformly among assessed alternatives for the
selected route. An objective-targeted variant chooses the alternative with
the lowest value for its registered objective, resolving ties uniformly.
Partition, vehicle, driver, and depot alternatives use the shared random
source and the bounded alternative limit from §2.1. If no alternative meets
the requested feasibility condition, the call returns `m = 0`.

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

#### 2-opt

**Move.** Choose one `VehicleDayRoute` with at least two stops and indices
`0 <= i < j < len(stops)` such that `j - i + 1 >= 2`. Reverse the inclusive
slice `stops[i:j + 1]`. Keep the route's start and overnight depots fixed.
Rebuild every leg incident to the reversed slice. A trial is ineligible if
any changed endpoint pair has no path under §2.2 or the resulting sequence is
unchanged.

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
m = 0 and the call returns no candidate.

#### Exchange

**Move.** Select two distinct stop occurrences, possibly from one route or
from two routes on the same day, and exchange their positions. A trial is
eligible when both resulting route sequences retain valid tour-end markers.
Rebuild affected legs and exclude an unchanged result.

**Size.** k is the number of pairwise exchanges applied sequentially.

**Modification count.** m is the number of completed exchanges. One exchange
counts as one move.

#### 2-opt* suffix exchange

**Move.** Select two distinct active routes on the same day and cut indices
`i` and `j`, where each index ranges from zero through that route's stop
count. Exchange the suffixes strictly after those indices, preserving each
suffix's order. Keep both routes' vehicle, day, start depot, and overnight
depot fixed. A trial is eligible when tour-end markers remain attached to
valid depot visits. Rebuild the two connecting and closing paths.

**Size.** k is the number of suffix exchanges applied sequentially.

**Modification count.** m is the number of completed suffix exchanges,
independent of suffix length.

#### SWAP* free-insertion exchange

**Move.** Select one stop from each of two distinct routes on the same day,
remove both, then independently choose an insertion index from zero through
the destination route's new stop count and insert each stop in the other
route. Exclude the former positions and any unchanged result. Preserve valid
tour-end markers and rebuild affected legs.

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
current path from alternatives. Without an `objective_id`, choose uniformly
among the sampled paths. With an `objective_id`, score each complete plan and
choose the sampled path with the lowest value for that objective. The
application evaluator remains responsible for the final recorded evaluation.

**Size.** k is the maximum number of route legs rerouted sequentially.

**Modification count.** m is the number of legs whose selected arc sequence
was replaced by a different sequence. k counts changed legs, not arcs.
Randomized path selection uses the shared `RandomSource`.

#### Objective-specific shortest-path Operator

Register one instance for each declared `ObjectiveId`. For each selected
route leg, use Dijkstra when the factory's `additive_path_costs` registry
contains an entry for the Operator's objective. Dijkstra returns a path that
exactly minimizes the registered additive cost; equal-cost ties use the shared
random source. If the selected path equals the current path, return `m=0` for
that leg. When the registry has no entry for the objective, sample loopless
paths using §2.2, exclude the current path, assess each complete plan under
all scenarios, and choose the feasible sample with the smallest value returned
by the selected `ObjectiveFunction`. Thus a standard-deviation objective
ranks complete candidate plans by their declared standard deviation. Resolve
equal-valued plans uniformly. The evaluator assesses the returned candidate
against the complete objective vector.

**Size.** k is the maximum number of distinct route legs whose paths are
replaced sequentially. Each search uses the selected objective and the
current working plan, including timing effects from earlier replacements.

**Modification count.** m is the number of selected legs whose arc sequence
differs from its previous sequence.

**Context use.** `CandidateContext.base_evaluation` supplies the base's stored
`EvaluationRecord`; `base_evaluation.analysis` supplies
`VrpOperatorAnalysis` when available. Otherwise the Operator chooses eligible
legs uniformly. Application-bound objective calculation uses the shared plan
assessment helper in §2.1. `CandidateContext.random_source` orders eligible
legs and resolves equal-value ties. A call that cannot produce a candidate
meeting its feasibility request returns `m = 0`.

### 3.3 Timing adjustment

**Move.** The registration supplies a positive `time_step` in the
`ScenarioBank` time unit. Select uniformly among changeable route `start_time`,
stop `pre_service_wait`, post-service `wait`, and `break_duration` fields.
Select `-time_step` or `+time_step` with equal probability. A proposal is
eligible only when it remains in the field's declared domain and preserves
`0 <= break_duration <= wait`. Recalculate scenario arrival, service,
departure, and return times when assessing the trial.

**Size.** k is the maximum number of timing fields changed sequentially.

**Modification count.** m is the number of successfully applied timing
changes. A later move may change a field changed earlier in the same call.

### 3.4 Driver reassignment

**Move.** Select uniformly an active vehicle-day route and an authorized
person whose driving skills include all skills required by that vehicle.
Replace the route's `driver_id`; the new driver joins the route crew, and the
old driver leaves it unless assigned to an intervention team on that route.
The shared assessment checks seat capacity, driver availability, and the
driver's full duty timeline. A route-driver pair already present is ineligible.

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
Operator returns m = 0. The profile controls which Operator registrations
exist; the current plan controls which moves can be applied.

### 4.1 Within-day time-dependent travel

**Departure-time profile adjustment.** Register one Operator per selected
`ObjectiveId` and positive `time_step`. Select a route leg with at least one
arc whose entry-time travel value changes when an upstream `start_time`,
`pre_service_wait`, or post-service `wait` changes by `time_step`. Select
uniformly one such leg, then one such changeable field upstream of it, and
propose plus or minus `time_step`. Keep values within the field's allowed
domain. Generate up
to 32 such complete plans and retain the plan with the smallest selected
objective value; ties are uniform. If no eligible field or candidate plan
exists, return `m=0`. The Operator derives entry-time changes by propagating
each proposal through its complete scenario bank; `VrpOperatorAnalysis` may
identify likely targets, but the final ranking always uses complete plan
assessment.

**Size.** k is the maximum number of route start or stop-wait decisions
changed sequentially.

**Modification count.** m is the number of changed timing decisions.

### 4.2 Scenario uncertainty

**Scenario-differential reroute.** Register one Operator per `ObjectiveId`.
For each leg, calculate the range of scenario travel durations as the largest
minus the smallest summed arc travel duration on that leg. Select uniformly
among legs with a positive range, then sample up to 32 replacement paths under
§2.2. Substitute each path into a complete plan, assess every scenario, and
retain the candidate with the smallest selected objective value; ties are
uniform. If all leg ranges are zero, select a leg uniformly and use the same
candidate ranking. If no different path is available, return `m=0`.

**Size.** k is the maximum number of distinct route legs rerouted.

**Modification count.** m is the number of route legs whose paths change.

**Scenario-differential timing adjustment.** Register one Operator per
`ObjectiveId` and positive `time_step`. Select uniformly a route `start_time`,
stop `pre_service_wait`, or post-service `wait` whose resulting arrival or
travel cost differs across scenarios. Generate
the plus and minus `time_step` alternatives that remain in the field's domain,
assess each complete plan in every scenario, and retain the candidate with the
smallest selected objective value; ties are uniform. When no such field exists,
select any changeable timing field and apply the same ranking. Return `m=0` if
no valid alternative is produced.

**Size.** k is the maximum number of timing decisions changed.

**Modification count.** m is the number of timing decisions changed.

### 4.3 Network-event exposure

**Event-objective route reroute.** Register one Operator per declared event
objective. Its registration uses the objective's event-type set and event
counting rule. Select uniformly among legs containing at least one counted
event occurrence; if none contains one, select any leg. Sample up to 32
different paths under §2.2, substitute each into a complete plan, and rank by
the selected objective's `ObjectiveFunction`. Return the lowest-valued
candidate, resolving ties uniformly. The complete assessment recounts all
event occurrences, including repeated traversal of an event arc or node.

**Size.** k is the maximum number of distinct route legs rerouted.

**Modification count.** m is the number of legs whose arc sequence changes.
`CandidateContext.random_source` orders eligible legs and resolves ties.

### 4.4 Multi-day planning

**Driver swap or cycle.** Select a cycle length `r` uniformly from
`2..min(k, number_of_active_routes)` and select `r` distinct active routes
uniformly. Assign each route the driver of the next route in the sampled
cycle, with the last route receiving the first route's driver. Keep all route,
vehicle, day, stop, depot, and intervention-team decisions fixed. Reject an
assignment vector that violates representation rules or the requested
feasibility guarantee; sample another cycle, up to 32 proposals. If no
alternative remains, return `m=0`.

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
`objective_id`, return the valid vector with the smallest value for that
objective among vectors satisfying the requested feasibility condition;
otherwise choose uniformly from vectors satisfying that condition. The
Operator returns only the complete plan. A vector identical to the base
assignments is ineligible.

**Size.** k is the number of route-driver assignments removed and jointly
reassigned in one repair move.

**Modification count.** m is the number of selected routes assigned a
different driver. If all retain their former driver, return m = 0 and no
candidate.

### 4.5 Overnight-depot choice

**Overnight-depot change.** Select one active vehicle-day route uniformly and
select one different `DepotId` from that vehicle's eligible overnight depots.
Change `overnight_depot_id`; the next active route's start depot changes by
depot continuity, including across intervening inactive days. Rebuild the
affected route endpoint legs. If the registration names an `objective_id`,
sample up to 32 alternative depot choices and return the complete plan with
the lowest value; otherwise choose uniformly from alternatives that meet the
requested feasibility. A route with one eligible depot has no move.

**Size.** k is the maximum number of vehicle-day depot assignments changed
sequentially.

**Modification count.** m is the number of routes assigned a different
overnight depot.

**Multi-day depot reassignment.** Select one vehicle uniformly and then a
contiguous subsequence of its active routes whose length is between 2 and
`min(k, n_active_routes_for_vehicle)`. When k is less than 2, return `m=0`.
Jointly assign a different eligible overnight depot to at least one route in
that subsequence. Keep drivers, crews, stops, and tour markers fixed; derive
each later route's starting depot from the prior active route's new ending
depot and rebuild all affected endpoint paths. Sample at most 32 depot vectors.
An optional registered `objective_id` ranks complete plans; without it, choose
uniformly from candidates that satisfy the requested feasibility. Staff depot
continuity is checked by the complete assessment like every other personnel
rule.

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
chooses uniformly among its eligible complete candidates. An
objective-targeted variant returns the eligible candidate with the lowest
value of its registered objective; ties are resolved uniformly. Each
Operator assesses the complete candidate plan and applies the requested
feasibility guarantee.

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
constraints. The unranked variant chooses uniformly among eligible assessed
candidates; an objective-targeted variant chooses the candidate with the
lowest value for its registered objective, resolving ties uniformly.

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
chooses uniformly among eligible assessed candidates; an objective-targeted
variant chooses the candidate with the lowest value for its registered
objective, resolving ties uniformly.

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
and an `objective_id`. Select the vehicle route with the largest
`vehicle_availability` penalty contribution to that objective, using
`base_evaluation.analysis`; if no such finding is available, choose a route
uniformly. For that route, propose plus/minus `time_step` changes to
`start_time`, `pre_service_wait`, and post-service `wait` fields that remain
in their domains. Assess up
to 32 complete plans and return the one with the lowest selected objective
value, breaking ties uniformly.

**Size.** k is the maximum number of route timing decisions changed.

**Modification count.** m is the number of timing decisions changed.

**Availability-guided stop relocation.** Select a stop occurrence on a route
with a positive `vehicle_availability` finding; when analysis is absent, pick
any stop on a route. Draw up to 32 destination-route and insertion-index
pairs uniformly. Keep the stop's type-specific fields and rebuild affected
legs. The complete assessment recalculates availability for the source and
destination vehicles. If an `objective_id` is registered, retain the
lowest-valued candidate; otherwise select uniformly among candidates meeting
the feasibility request.

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
`VrpOperatorCatalogSettings.max_candidate_alternatives`. Rank
completed candidates first by their maximum capacity excess across all
`LoadIntervals`, lowest first. If an `objective_id` is registered, use its
value to break ties; otherwise break ties uniformly. Discard candidates that
fail the requested feasibility condition. The engine receives only the final
complete candidate.

**Size.** k is the number of package chains selected for relocation and
reinsertion.

**Modification count.** m is the number of selected chains whose final route
assignment, stop positions, or transfer-operation positions differ from the
working plan immediately before their move. If none changes, return m = 0 and
no candidate.

### 4.9 Potentially binding personnel seat capacity

**Seat-aware intervention-team reassignment.** Select a route whose crew size
equals its vehicle's seat capacity; when none exists, select a route with the
largest crew. Select an intervention on that route uniformly. Generate up to
32 distinct replacement teams from authorized personnel whose qualifications
cover the intervention's requirements and whose team size meets its minimum.
Update `team_member_ids`, derive the route crew from the driver and all
intervention teams, and reject a team that makes the crew exceed seat
capacity. A registered `objective_id` ranks complete candidates; without one,
select uniformly among candidates meeting the feasibility request.

**Size.** k is the maximum number of intervention teams changed.

**Modification count.** m is the number of intervention stops assigned a
different team.

### 4.10 Package–vehicle compatibility

**Compatibility-guided package-chain repair.** Select uniformly among package
requests whose current carrier sequence includes an incompatible vehicle. If
all current carrier sequences are compatible, select a package with the fewest
compatible vehicles. Remove its pickup, delivery, and paired transfer stops
into an internal partial plan. Preserve service-coverage choices,
transfer-step count, and facility choices. Sample up to 32 distinct carrier
sequences using compatible vehicles only; the sender and receiver at every
transfer must be distinct and compatible. For each sequence, sample insertion
positions that preserve custody order and rebuild affected `TransferStep`
and route records. Assess the complete repair, then repeat for up to k package
chains, updating the working plan after each successful repair. A registered
`objective_id` ranks candidate carrier sequences for each repair; without one,
choose uniformly among candidates meeting the feasibility request.

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
request.
Sample at most 32 valid alternatives; when an `objective_id` is registered,
choose the lowest-valued complete candidate, otherwise choose uniformly among
candidates meeting the feasibility request.

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
their incident paths. Keep vehicles, days, and stop positions fixed. A
candidate is ranked first by its maximum storage excess across all facilities
and scenarios, lowest first. A registered `objective_id` breaks ties;
without one, ties are uniform. Discard candidates that fail the requested
feasibility condition.

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
Keep completed trials that meet the requested feasibility condition; an
`objective_id` selects the lowest-valued complete trial, otherwise select one
completed trial uniformly. Return only the final complete plan.

**Size.** k is the maximum number of package requests selected for removal
and reinsertion; the call selects at most the number of eligible package
chains in the plan.

**Modification count.** m is the number of selected package requests whose
service occurrence, route assignment, stop order, or transfer plan differs
from the plan immediately before reinsertion. A selected request restored
identically contributes zero. Only packages with at least one served activity
or transfer operation are eligible for removal. If no selected request
changes, return m = 0 and no candidate.

### 4.14 Positive service durations

**Service-order impact repair.** Register an `objective_id`. For each
positive-duration activity occurrence, sum that objective's downstream
service-window penalty contributions across later stops on the same route,
weighted by their scenario probabilities. Select an occurrence with the
largest sum; if every sum is
zero, select uniformly among positive-duration occurrences. Generate up to 32
relocations of that activity to eligible positions on its current or another
eligible route, preserving its type-specific data and dependent references.
Reassess each complete plan and return the candidate with the lowest selected
objective value; ties are uniform. Service durations remain fixed at their
instance values.

**Size.** k is the maximum number of activity occurrences relocated.

**Modification count.** m is the number of activity occurrences whose final
route or stop position changes.

**Duration-impact timing adjustment.** Register an `objective_id` and
positive `time_step`. Select uniformly a route start or stop wait that occurs
before a positive-duration service. Generate plus/minus `time_step` values
within the field domain, reassess the complete plans, and return the one with
the lowest selected objective value; ties are uniform. If no such timing field
exists, return `m=0`.

**Size.** k is the maximum number of timing decisions changed.

**Modification count.** m is the number of timing decisions changed.

### 4.15 Optional service omission

**Service-coverage change.** The factory includes this Operator only when
`service_coverage_mode = penalized_omission`. Select one required activity
whose current occurrence count is zero or one. If present, remove its
`ActivityStop`. Removing a package pickup also removes its delivery and every
transfer step, as required by `multiday_vrp.md` §3.2. Removing a delivery
leaves the package on its final carrier through the horizon. If absent, insert
one `ActivityStop` at a sampled eligible vehicle-day and route position,
preserving the package's existing custody chain. Rebuild affected paths; the
evaluator calculates omission penalties.

**Size.** k is the maximum number of activity coverage decisions changed.

**Modification count.** m is the number of activity coverage states changed.
A pickup toggle that also removes dependent delivery and transfer records
counts as one package-pickup coverage change.

### 4.16 Restrictive service windows

The service-wait adjustment, route-start shift, activity relocation,
route-leg rerouting, and time-window-focused destroy-and-repair Operators
register an `objective_id`. The wait and start-time variants also register a
positive `time_step`. These five Operators use
`CandidateContext.base_evaluation.analysis.scenario_evaluations` to locate the
activity stop with the largest maximum per-scenario `guidance_value` for their
`objective_id`. Each trace stop's `window_assessments_by_objective` contains
one assessment per objective; the Operator reads the matching objective's
record. These fields are defined in §2.3 and the input-format document. Exact
ties are resolved uniformly. When the analysis is absent or contains no
positive guidance value, these Operators choose uniformly among eligible
windowed activities. The selected objective ranks complete candidate plans
using the shared assessment helper. A service occurrence is identified by
its `ActivityRef`, route, and stop index in the base plan.

Time-window penalties are soft objective contributions, so
`ensure_feasible=True` still requires all hard requirements in every scenario
while allowing a positive window penalty.

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
feasibility condition and return the one with the lowest registered objective
value; ties are uniform. If no trial completes, return `m=0`.

- **Size:** k is the number of activity occurrences removed and reinserted.
- **Modification count:** m is the number of selected occurrences whose service
  occurrence, route, stop order, or transfer plan changes.

**Time-window regret-based service reinsertion.** Consider served service
units that contain at least one time-windowed activity. A package request is
one atomic package-chain unit; an intervention or other independent service
is one activity unit. For each eligible unit, remove its current occurrence
or chain from a working copy and form complete placements in the same or
another active route. An activity-unit placement inserts its stop at one
route position. A package-chain placement follows the custody-preserving
chain placement rule in §4.13. Sample up to
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
alternative for the current step. If no unit can be moved, return `m = 0`.

- **Size:** k is the maximum number of distinct service units relocated.
- **Modification count:** m is the number of selected units whose route or
  stop positions changed.

All six service-window Operators use the shared assessment and random-source
contracts. The five analysis-guided Operators use `VrpOperatorAnalysis` as
described above. Their move units are specified individually; time-window-
focused destroy-and-repair moves activity occurrences, while regret-based
service reinsertion moves service units.

### 4.17 Interventions

**Intervention relocation.** Select one served intervention uniformly and
sample up to 32 distinct insertion positions across its eligible vehicle-day
routes. Keep its intervention ID and `team_member_ids`. Recompute each
affected route's crew from its driver and all assigned intervention teams;
discard a position that violates representation rules. Rebuild affected paths
and assess every scenario. A registration may
name an `objective_id`; when present, choose the complete candidate with the
lowest value, otherwise choose uniformly among candidates meeting the
feasibility request.

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
vehicle-day routes. For each proposal, assign the intervention to the segment
containing its new stop, recompute that segment's tool union, rebuild affected
paths, and assess all scenarios. Rank candidates first by the maximum tool
weight over all segments, lowest first. If an `objective_id` is registered,
use its value to break ties; otherwise break ties uniformly. Discard a
candidate that fails the requested feasibility condition.

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
ordering: when omission is permitted, generate one candidate omitting `a`
and one omitting `b`; otherwise this Operator reports `m=0` for that pair.
Each generated candidate preserves package custody, rebuilds paths, and is
ranked by an optional registered `objective_id`; without one, choose
uniformly among candidates meeting the feasibility request. Apply one selected
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
`objective_id` ranks complete candidates; without one, select uniformly among
candidates meeting the feasibility request.

**Size.** k is the maximum number of route-driver assignments changed
sequentially.

**Modification count.** m is the number of selected routes assigned a
different driver. If every selected route retains its original driver, return
m = 0 and no candidate.

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
candidates; without one, choose uniformly among candidates meeting the
feasibility request.

**Size.** k is the maximum number of intervention teams changed.

**Modification count.** m is the number of intervention stops assigned a
different team.

### 4.22 Restricted personnel availability

**Personnel-assignment repair.** Select a person-duty pair with the largest
`personnel_availability` penalty contribution for the registered
`objective_id`; if analysis is unavailable, choose uniformly among duties
whose assigned person has a restricted calendar. If the person is the driver,
propose authorized, vehicle-qualified replacement drivers. If the person is
an intervention-team member, propose qualified replacement team subsets that
preserve the minimum team size. Sample at most 32 complete replacements and
reassess every affected duty and route crew.

**Size.** k is the number of person-duty assignments changed.

**Modification count.** m is the number of person-duty assignments changed.

**Personnel-calendar timing adjustment.** Register a positive `time_step`
and `objective_id`. Select a route with a positive personnel-availability
finding, or any route assigned to a person with restricted availability when
analysis is absent. Propose plus/minus `time_step` changes to its `start_time`,
`pre_service_wait`, and post-service `wait` fields within their domains.
Assess up to 32 complete candidates and
return the candidate with the lowest selected objective value; ties are
uniform.

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
complete candidates; without one, choose uniformly among candidates meeting
the requested feasibility condition.

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
32 alternatives. Rank complete feasible candidates by the selected objective;
for infeasible intermediate trials, use only the selected key's measure rule.
Return a candidate only when it meets the call's feasibility request. When
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
infeasible base; rank complete feasible candidates by the selected objective.

**Size.** k is the maximum number of timing fields changed.

**Modification count.** m is the number of timing fields changed.

### 4.25 Multiple objectives

**Objective-targeted activity relocation.** Register one Operator per
`ObjectiveId`. Select one served activity uniformly, sample up to 32
alternative route/insertion positions, and assess each complete plan against
the full objective vector. Return the candidate with the lowest value for the
registered objective; ties are uniform. This adds an objective-focused
service-order move to the per-objective route-path Operators in §3.2.

**Size.** k is the maximum number of activity relocations applied
sequentially.

**Modification count.** m is the number of activity occurrences moved.
The complete candidate is evaluated against the entire objective vector.

**Pareto trade-off route move.** Its registration fixes one `move_family`:
`path`, `timing`, or `activity`. Select uniformly one active route and
generate up to 32 complete alternatives using the corresponding generic move
definition. From those alternatives, retain the ones
nondominated by the standard componentwise minimization relation over the
complete objective vectors; choose uniformly among that set. Exact duplicate
objective vectors remain separate alternatives. The Operator returns a
selected move even when the base dominates every sampled alternative;
archive admission remains the search engine's decision.

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
Assess complete plans on the full scenario bank and return the candidate with
the lowest selected objective value; ties are uniform.

**Size.** k is the maximum number of route-leg path changes when
`move_family = path`, or timing-field changes when `move_family = timing`.

**Modification count.** m is the number of changed route legs or timing
fields, according to `move_family`.

When a feature has several active objectives or feature instances, register
separate Operators only when their move strategy or target history is
intentionally distinct. A feature gate never creates duplicate registrations
of the same Operator.

## 5. Feasibility-restoration capability

The `feasibility_restoration` registration flag tells the search whether an
Operator may be selected when its base is infeasible and its call requests
`ensure_feasible=True`. A `True` registration accepts an infeasible base and
handles the full move sequence as one internal repair attempt. It returns a
candidate only when the shared assessment helper confirms that the complete
plan satisfies every hard requirement in every scenario. If its bounded move
search finds no such plan, it returns `m=0` and no candidate. The flag
guarantees the feasibility of every successful result; the bounded search can
return no candidate for an infeasible base outside the Operator's repair
scope.

The feasibility request governs complete alternatives generated from the
base. The Operator uses the infeasible base to construct repair moves, assesses
those alternatives, and returns a candidate only when its assessment confirms
feasibility. The repair call starts from its supplied base whether the base is
feasible or infeasible.

A `False` registration is ineligible as a standalone choice for a restoration
iteration. Every call it receives still follows the generic `ensure_feasible`
result guarantee. It may be the first Operator in a restoration-capable
composition: the composition is eligible by the second Operator's flag, and
its first call receives `ensure_feasible=False`, as defined in the generic
algorithm design.

The default catalog assigns the following values. Every objective-targeted
registration generated from one specification inherits that row's flag.
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
| Vehicle-route consolidation | `False` | Feasible-base route reassignment, capacity, route-timing, and assignment feasibility |
| Vehicle-elimination destroy-and-repair | `False` | Feasible-base multi-route reassignment and vehicle deactivation |
| Vehicle-route split | `False` | Feasible-base route reassignment, capacity, route-timing, and assignment feasibility |
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
| Time-window-guided activity relocation | `False` | Feasible-base service-window penalty search |
| Time-window-guided route-leg rerouting | `False` | Feasible-base service-window penalty search |
| Time-window-focused destroy-and-repair | `False` | Feasible-base service-window penalty search |
| Time-window regret-based service reinsertion | `False` | Feasible-base service-window-aware insertion and route reassignment |
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
features are active. These calls can return `m=0` when their bounded move
search finds no fully feasible plan.

All Operators follow the generic callback contract for invalid results,
`ensure_feasible`, state, random draws, and m = 0; see
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
