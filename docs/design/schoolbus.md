# Simplified school-bus routing: formal model and design example

This document defines a school-bus routing model and illustrates the objects
in the [multiobjective VNS architecture](multiobjective-vns.md) with that
model. The examples focus on application data, interfaces, and object
responsibilities.

The callable signatures and shared record schemas follow architecture §6.6;
this document supplies the school-bus-specific values and examples.

## 1. Formal problem

### 1.1 Scope and data

Let `S = {1, ..., n}` with `n ≥ 1` be the students, `o` the bus depot, and
`h` the school.
Each student `i` has one fixed pickup point `p[i]`. All students require one
seat. Buses are identical, each with an integer capacity `Q ≥ 1` seats, and
each bus performs one morning trip from the depot through its pickup points to
the school. A bus route ends at the school.

For every pair of relevant locations `u` and `v`, let `d[u,v]` be a finite,
nonnegative real road distance from `u` to `v`. The distance data may be
directional.

### 1.2 Route plans, feasibility, and objectives

A route is a nonempty ordered sequence of student identifiers. A route plan
`R` is an unordered multiset of `b` routes, where `b` is a nonnegative integer. It preserves each route's ordered
student visits and the multiplicity of route entries in an infeasible
representation. The plan is feasible when:

- Every student appears in exactly one route.
- Each route contains at most `Q` students.

Route plans are immutable by contract while the package retains or exposes
them, as required by architecture §5.1. An Operator that changes a mutable
route structure works on its own copy and returns a new candidate plan.

Every route begins at `o`, visits its students' pickup points in sequence, and
ends at `h`. For route `r = (i[1], ..., i[q])`, its distance is

```text
L(r) = d[o,p[i[1]]]
     + sum(j = 1 to q-1) d[p[i[j]],p[i[j+1]]]
     + d[p[i[q]],h]
```

The number of buses used is `b`, one for each nonempty route. The total travel
distance is `D(R) = sum(r in R) L(r)`.

For student `i[k]` in position `k` of route `r = (i[1], ..., i[q])`, define
their onboard distance from pickup to school as

```text
A(i[k], r) = sum(j = k to q-1) d[p[i[j]],p[i[j+1]]]
           + d[p[i[q]],h]
```

For a nonempty assignment set, the maximum student ride distance is
`M(R) = max(r in R) max(1 ≤ k ≤ q) A(i[k], r)`. It measures the longest
planned onboard distance among assigned students and serves as a
distance-based proxy for ride time. Each student's distance sum begins at
their pickup and ends at the school. For an empty route collection, define
`M(R) = 0`, since no student has an onboard distance.

```text
f(R) = (bus_count(R), total_distance(R), max_ride_distance(R))
     = (b, D(R), M(R))
```

The ordered `ObjectiveDeclaration` contains `ObjectiveFunction` objects for
`bus_count`, `total_distance`, and `max_ride_distance`. Each compares its
numeric values in exact ascending order with zero tolerance, returns `less`,
`equal`, or `greater`, uses
`__str__()` for a readable objective name, and formats values for reports. For
example, the names can be “Bus count,” “Total route distance,” and “Maximum
ride distance.” The package treats every objective as a minimization
objective, so the application returns these values directly. For a well-formed
plan, all three objective values are available even when a capacity or
coverage constraint is violated. The empty route collection has objective
vector `(0, 0, 0)` under the convention above, and its coverage violation
still makes it infeasible. Feasibility determines eligibility for the Pareto
set. Distance-value formatting can include the unit used by the application;
the objective name identifies whether the value is total distance or maximum
ride distance.

For feasible plans `R1` and `R2`, `R1` dominates `R2` when each of its
objective values is less than or equal to the corresponding value for `R2`,

```text
bus_count(R1) ≤ bus_count(R2)
total_distance(R1) ≤ total_distance(R2)
max_ride_distance(R1) ≤ max_ride_distance(R2)
```

with a strict inequality in at least one objective. The model's Pareto set is
the set of feasible plans maximal under this dominance relation.

### 1.3 Illustrative instance

Use four students, capacity `Q = 2`, depot `o = (0,0)`, and school
`h = (4,0)`. Student pickup points are:

| Student | Pickup point |
|---|---|
| 1 | `(1,0)` |
| 2 | `(2,1)` |
| 3 | `(3,0)` |
| 4 | `(2,-1)` |

For this small example, road distance is Manhattan distance:
`d[u,v] = |x[u]-x[v]| + |y[u]-y[v]|`.
The application can identify these data, route endpoints, capacity, distance
meaning, and objective declaration as `schoolbus-four-student-example-v2`.

| Plan | Routes | Route distances | Maximum ride distance | Assessment | Objective vector |
|---|---|---:|---:|---|---|
| `Rα` | `(1,2)`, `(4,3)` | `6 + 6` | `5` | Feasible | `(2,12,5)` |
| `Rβ` | `(1,3)`, `(2,4)` | `4 + 8` | `5` | Feasible | `(2,12,5)` |
| `Rγ` | `(1,2)`, `(3)`, `(4)` | `6 + 4 + 6` | `5` | Feasible | `(3,16,5)` |
| `Rδ` | `(1,2,3)`, `(4)` | `6 + 6` | `5` | Infeasible: first route exceeds capacity | `(2,12,5)` |
| `Rε` | `(1,3)`, `(2)`, `(4)` | `4 + 6 + 6` | `3` | Feasible | `(3,16,3)` |

`Rα` and `Rβ` have equal objective vectors while representing different
student-to-route assignments. `Rγ` is dominated by either feasible two-bus
plan. `Rε` uses more buses and more total distance than `Rα` and `Rβ`, while
keeping every student's ride at most 3 distance units rather than 5. `Rδ` has
a well-formed route representation, but violates the capacity constraint.

## 2. Application representation and evaluation

The application represents a solution as an unordered multiset of ordered
routes, each containing student identifiers. A representation is well-formed
when every route is a nonempty sequence and every entry refers to a student in
the instance. The evaluator reports a well-formed plan as feasible when every
student appears exactly once and each route respects capacity. An unknown
student identifier or malformed route structure is an `invalid` application
finding; omitted or repeated known students and over-capacity routes are
`infeasible` findings under this model.

The required `ApplicationEvaluator` returns representation validity,
feasibility, and the objective vector as separate fields in one
`ApplicationEvaluation`. For a well-formed, nonempty plan such as `Rδ`, it can
return all three objective values even when a capacity constraint is violated;
the assessment remains `infeasible`. A malformed plan with an unknown pickup
identifier has an `invalid` assessment, and its route-based objective values
may be unavailable. That is a completed application finding. A callback
exception before an evaluation is returned leaves the assessment unavailable
and produces a `ComponentFailure`. A returned value that violates the evaluator
contract produces a `ComponentFailure` alongside the application assessment
when the finding is determinable. The diagnostic categories and general
handling rules follow architecture §5.11. This separates the evaluator's
judgment about a plan from callback and contract problems.

An invalid initial route plan receives the application finding alone. An
invalid final plan returned by an Operator also violates the representation
guarantee in architecture §5.9 and receives an Operator `ComponentFailure`.
Within a composition, the first Operator's intermediate plan remains internal
and serves as the second Operator's base. The evaluator processes the final
plan. The intermediate has no application assessment, identity, registry
record, or candidate outcome; the final plan follows the same Operator
contract as a standalone result.

An empty route collection is a well-formed but coverage-infeasible plan. The
evaluator reports `representation_valid=True`, `feasible=False`, and objective
vector `(0, 0, 0)`. Its assessment is `infeasible`, with no evaluator
contract-failure diagnostic.

The initial-solution set must include at least one valid, feasible, evaluable
plan. Either `Rα` or `Rβ` is a concrete example. The
[algorithm design](multiobjective-vns-algorithm.md) specifies the error
behavior when an initial set fails this requirement.

## 3. Architecture concepts in the school-bus model

### 3.1 Application values and callbacks

| Architecture concept | School-bus interpretation |
|---|---|
| `Solution` | A route plan: its assigned students and the visit order within each route. |
| `Initial solutions` | Caller-supplied route plans such as `Rα` or `Rβ`; §2 gives the required starting-solution condition. |
| `ObjectiveDeclaration` | The ordered objective functions `(bus_count, total_distance, max_ride_distance)` from §1.2, each using ascending numeric comparison and a readable name and value format. |
| `ObjectiveFunction` | Compare two values in ascending numeric order; return `less`, `equal`, or `greater`; display the objective name with `__str__()` and format values for reports. |
| `Objective vector` | For `Rα` and `Rβ`, `(2,12,5)`; each position follows the declaration. |
| `ApplicationEvaluator` | The single callback defined in §2, following the deterministic-results contract in architecture §5.6. |
| `ApplicationEvaluation` | The findings returned by the callback in §2. |
| `ApplicationAssessment` | The derived category; school-bus cases are in §2 and shared derivation is in architecture §5.6. |
| `ProblemInstanceId` | `schoolbus-four-student-example-v2`, defined in §1.3, identifies the locations, capacity, route-validity and feasibility rules, distance data, and three objective meanings used by the evaluator and operators. |
| `SolutionIdentity` | Route-collection order is irrelevant and within-route order is significant. Reordering `(1,2)` and `(4,3)` leaves `Rα` identical; `Rα` and `Rβ` remain distinct. |
| `SolutionId` | The engine-assigned opaque reference for one route-plan identity; `Rα` and `Rβ` have separate identifiers despite equal objective vectors. |
| Identity lookup hint | An optional hint can summarize the unordered multiset of ordered routes; the route-plan equivalence rule remains authoritative. |
| `SolutionCodec` | Preserve and restore identity-resolved registry route plans, including invalid representations, when a checkpoint is prepared; see architecture §8.1. |

### 3.2 Neighborhoods and candidate proposals

An operator works with the complete route plan as its solution value. Its
application data—pickup points, road distances, capacity, and school/depot
locations—are bound by the application as described in architecture §5.8.
`CandidateContext` supplies the selected base, requested neighborhood size,
the `ensure_feasible` request, operator-owned state, and the shared
`RandomSource` used by Operators, policies, and engine sampling.

| Architecture concept | School-bus interpretation |
|---|---|
| `Base` | A route plan supplied to an operator; it may be feasible, such as `Rα`, or infeasible, such as `Rδ`. |
| `Neighborhood` | The set of route plans an operator considers reachable from a base at a given size. A size can represent a number of students to relocate or route positions to reorder. |
| `Operator` | An application function such as `relocate_student`, `reorder_route`, `merge_routes`, or `destroy_repair`, with a stable `operator_id`; examples follow in the operator catalog. |
| Feasibility-restoration operator | `repair_capacity`, registered with the capability to work from an infeasible base and return a feasible plan on a successful call with `ensure_feasible=True`. |
| `CandidateContext` | For example, the base `Rδ`, size `k=2`, the feasibility request, this Operator's continuation state, and the shared `RandomSource`. The source seed and checkpoint continuation follow architecture §6.6 and §8.1. |
| `Candidate` | A proposed route plan, optionally carrying immutable-by-contract metadata such as the moved student and source/destination route, following architecture §5.4. |
| `OperatorResult` | The requested size `k`, actual modification count `m` on that operator's declared scale, and a candidate when one was generated. For `repair_capacity`, `k=2` and `m=1` can describe a partially realized request. A zero count records zero achieved modifications and an empty candidate field. |
| `CandidateOutcome` | The report for one initial submission or Operator proposal. An initial outcome identifies the `initial` source and has `choice_id=None`. An Operator-produced candidate records the tagged `ChoiceId` defined in architecture §6.6. `component_position=None` for a standalone Operator and `second` for a composition, identifying the Operator that produced the submitted candidate. The outcome also records the base, requested size, actual count, candidate metadata, `SolutionId` when identity resolves, processing status, available application assessment, component failures, and search disposition when known. |

The evaluator may include student-to-route assignments, route loads, and each
student's ride distance in `ApplicationEvaluation.analysis`. Operators receive
that immutable value through `CandidateContext.base_evaluation`. A route
rebalancing Operator can use it to target students with long rides or routes
near capacity; the evaluator remains authoritative for the candidate's
objective values and feasibility.

#### Operator catalog

The operators below preserve the route representation and assign every known
student exactly once. A call with `ensure_feasible=True` returns a candidate
only when the complete plan satisfies the capacity constraint as well. Each
operator declares a finite maximum size and supports every integer `k` from 1
through that maximum; a particular base may have no move at a supported size,
which is reported as `m=0` with no candidate. The meaning of `m` belongs to the
operator's own scale.

| Operator | Size `k` and count `m` | Feasibility behavior and example |
|---|---|---|
| `relocate_student` | `k` is the maximum number of students to relocate; `m` counts students actually placed at a different route or position. Maximum size: `n`. | With `ensure_feasible=True`, choose insertions that respect `Q`. With `False`, moving student 1 from `(1,2)` to `(4,3)` in `Rα` produces `(2)`, `(4,3,1)`, a valid but over-capacity plan (`k=1`, `m=1`). |
| `exchange_students` | `k` is the maximum number of disjoint cross-route student exchanges; `m` counts completed exchanges. Maximum size: `max(1, floor(n/2))`. | Swapping students 2 and 4 in `Rα` produces `(1,4)`, `(2,3)` with both route sizes unchanged (`k=1`, `m=1`). An `ensure_feasible=True` result satisfies capacity. |
| `or_opt_block` | `k` is the maximum length of a consecutive student block to relocate; `m` counts selected block members whose route or position changed. Maximum size: `n`. | Moving a block of two consecutive students between routes is an Or-opt-style move (`k=2`, up to `m=2`). This lets one Operator expose single-student and larger block moves through its size parameter. |
| `reorder_route` | `k` is the maximum number of consecutive route positions considered for reordering; `m` counts positions whose visit order changed. Maximum size: `n`. | A 2-opt-style reversal changes `(4,3)` to `(3,4)` in `Rα` (`k=2`, `m=2`). It preserves student assignment and route loads while changing route distance and ride distances. At `k=1`, no order change is possible, so the result has `m=0` and no candidate. |
| `two_opt_star` | This Operator has maximum size `1`; `k=1` requests one exchange of route tails after selected cut points, and `m` is `1` when the exchange is applied or `0` when no valid exchange is available. | Exchanging the tails after the first student in each route of `Rα` produces `(1,3)`, `(4,2)` (`k=1`, `m=1`). The student assignments remain complete and each route remains nonempty; capacity is checked when requested. |
| `split_route` | `k` is the maximum number of routes to split; `m` counts completed splits. Maximum size: `max(1,n-1)`, with `m=0` when `n=1`. | Splitting `(2,4)` in `Rβ` into `(2)` and `(4)` produces `Rε` (`k=1`, `m=1`). The objectives change from `(2,12,5)` to `(3,16,3)`: one additional bus and distance units trade for a shorter maximum ride. |
| `merge_routes` | `k` is the maximum number of route merges; `m` counts completed merges. Maximum size: `max(1,n-1)`, with `m=0` when `n=1`. | Merging the singleton routes `(3)` and `(4)` in `Rγ` into `(3,4)` reduces bus count by one and respects `Q=2` (`k=1`, `m=1`). With `ensure_feasible=True`, a merge is returned only when the complete plan remains feasible. |
| `repair_capacity` | `k` is the maximum number of students selected for relocation from overloaded routes; `m` counts selected students whose final route or position changed. Maximum size: `n`. | Registered with the feasibility-restoration capability for the catalog's capacity-infeasible plans. From `Rδ`, moving student 3 from `(1,2,3)` to `(4)` yields `Rα` with `k=2`, `m=1`, and `ensure_feasible=True`. |
| `destroy_repair` | `k` is the number of students selected for removal and reinsertion; `m` counts selected students whose final route or position changed. Maximum size: `n`. | One Operator call removes the selected students, reinserts them, and returns one final plan. From `Rα`, selecting students 1 and 4 and reinserting them gives `(2,1)`, `(3,4)` with `k=2`, `m=2`. A capacity-aware variant returns feasible plans for `ensure_feasible=True` and may be registered with restoration capability. |

`random_destroy_repair`, `long_arc_destroy_repair`, and
`high_ride_destroy_repair` are useful separate catalog entries: they differ in
which students they select for removal, then use an insertion strategy that
considers route distance, capacity, or onboard ride distance. Their separate
`OperatorId` values distinguish the registered components. Each standalone
selection has a tagged `choice_id`, which identifies its outcomes and
feedback. Candidate metadata can retain the selected students and their
insertion locations for event reporting.

This catalog adapts patterns from school-bus and vehicle-routing research.
Spada, Bierlaire, and Liebling study a fixed-fleet school-bus routing and
scheduling problem with service-level objectives. Their peer-reviewed article
compares tabu search and simulated-annealing variants and reports a slight
advantage for simulated annealing that explores infeasible solutions. The
related 2003 conference version describes a neighborhood that relocates one
student from one bus to another, with both a capacity-feasible form and a
relaxed form that can produce capacity violations. These ideas motivate
`relocate_student` and its feasibility-request examples; the present model
focuses on fixed pickup points, route plans, bus count, total distance, and
maximum ride distance. See [1] for the move details and [2] for the heuristic
comparison.

In their discussion of a school-bus OVRP solver, Bögl, Doerner, and Parragh
report VNS neighborhoods including cross-exchange, sequence ruin and reroute,
random ruin and reroute, 2-opt, Or-opt, and 2-opt*. Their transfer-aware routing
framework also uses arc removal and reinsertion to perturb route structure.
The examples here adapt route-level versions to the fixed-pickup model above
[3]. Ropke and Pisinger describe an ALNS portfolio of competing subheuristics
whose use reflects historical performance, motivating separately identified
destroy-and-repair variants [4].

The destroy and repair phases of `destroy_repair` are internal to that one
application-provided `Operator` call; the engine receives one `OperatorResult`
and records one outcome for the selected choice. A composition such as
`reorder_route(relocate_student(R))` is a one-step ordered pair of Operators
from the original catalog; both receive the same requested `k`. The intermediate
plan from `relocate_student` serves as the input base to `reorder_route`; its
modification count appears in the first Operator-result event. The composition
calls `relocate_student` with `ensure_feasible=False` and passes the
composition's feasibility request to `reorder_route`. Only the final plan
returned by `reorder_route` is processed as a candidate and assessed by the
application.
The composition's tagged `choice_id` identifies the ordered pair of source
`OperatorId` values.
Its modification count is the smaller of its component counts, so it reaches
`m=k` only when both components reach `k`.
An `OperatorGroup` containing `relocate_student`, `exchange_students`, and
`destroy_repair` gives a soft hint for selecting among compositions under
`performance_matching`. The engine first selects the standalone or composition
category using `composition_selection_probability`; uniform feedback assigns
equal probabilities within the selected category, independent of group
membership. The algorithm design defines the category probability,
composition selection, and the remaining result-handling rules.

The modification counts are meaningful with their choice identities and
requested sizes: one exchange, one route merge, and one relocated student are
different units. The structured `ExecutiveSummary` defined in architecture
§6.6 groups modification data by tagged `choice_id: ChoiceId` and requested
size `k`. Its tag distinguishes a standalone Operator choice from an ordered
composition. The summary reports archive admissions and other search
outcomes for each choice separately.

If an operator proposes a route collection whose route order differs from an
already registered `Rα`, `SolutionIdentity` resolves the existing
`SolutionId`. Its `CandidateOutcome` records a duplicate disposition, and the
engine uses `Rα`'s stored `EvaluationRecord` for the proposal occurrence.

### 3.3 Registry, archive, and active pool

| Architecture concept | School-bus interpretation |
|---|---|
| `SolutionRecord` | One identified route plan, including its payload, `EvaluationRecord`, current archive status when applicable, and candidate-scoped operator contract failures. |
| `EvaluationRecord` | The single stored processing status, `ApplicationEvaluation`, derived assessment, and evaluation diagnostics for that route plan. |
| `SolutionRegistry` | One record per distinct identity-resolved route plan, including invalid, infeasible, and failed evaluations. Duplicate route-order representations refer to the existing record. |
| `Archive` | The known feasible route plans currently admitted under the archive policy. `Rα`, `Rβ`, and `Rε` can all appear: the first two have equal objective vectors, while `Rε` gives a shorter maximum ride. |
| `ArchivePolicy` | Determine archive membership from feasibility and the three objective values using the declared dominance relation. |
| `ArchiveStatus` | Classify a feasible record as `Pareto optimal`, `Dominated`, or `Rejected`; `Rejected` marks a former archive member displaced by a later update. |
| `SearchDisposition` | Describe proposal handling: `duplicate` for a route-list reordering, `dominated` for a proposed `Rγ`, or `archived` when a proposal such as `Rε` enters the archive. |
| `Active pool` | The default pool can contain dominated feasible `Rγ` and, in relaxed mode, infeasible `Rδ`; cleanup retains the current Pareto plans. Eligibility and cleanup are defined in algorithm design §2. |
| `ExplorationPolicy` | Choose a subset of engine-eligible route plans while retaining every current Pareto archive member. |
| `BaseSelectionPolicy` | In the relaxed-mode example, infeasible `Rδ` is the current base for `repair_capacity` during its excursion. The shared eligibility and selection rules are defined in algorithm design §2. |

Consequently, the archive can omit model-level Pareto plans that the search has not yet visited.

The public cleanup call and maintenance result follow architecture §7.

### 3.4 Coordination, policies, services, and reporting

| Architecture concept | School-bus interpretation |
|---|---|
| `MultiobjectiveVNS` | Coordinate candidate processing, identity lookup, and evaluation dispatch through `Executor`; coordinate policies and continuation; expose search and maintenance operations; and construct their results. |
| `MultiobjectiveVNSConfig` | Hold built-in per-invocation search-iteration and evaluation-request limits; bus capacity remains application problem data from §1.3. |
| `BudgetPolicy` | Allow the next search iteration or stop the invocation early; the engine enforces every configured ceiling, including evaluation-request limits. |
| `FeedbackPolicy` | Maintain separate statistics for standalone route Operators and ordered compositions, using each choice's modification-count scale; state changes follow architecture §6.4 and algorithm design §2. The engine selects a category first; `OperatorGroup` membership provides soft hints among compositions under `performance_matching`. |
| `Executor` | Invoke the route-plan evaluator for an engine-dispatched request; the `SolutionRegistry` stores each resulting `EvaluationRecord` for reuse. See architecture §§5.5–5.6 and §6.6. |
| `RandomSource` | Supply the one shared random stream for route Operators, policy decisions, and engine sampling. Its configured seed and checkpoint continuation follow architecture §6.6 and §8.1. |
| `SearchState` | The routing search's opaque, immutable continuation snapshot; use the read-only queries in architecture §7 to inspect Pareto, active, and visited route plans. |
| `Checkpoint` | A complete snapshot at one committed transition, following algorithm design §2. |
| `CheckpointStore` | Atomically save and restore snapshots; compatibility checks use the problem identifier and registered component identities/configurations from architecture §5.8. |
| `AlgorithmEvent` | Record route proposals, assessments, archive and pool changes, feedback, failures, checkpoints, and termination; events from one committed transition share its `commit_sequence`. |
| `EventObserver` | Receive those ordered events for display or external monitoring. |
| `RunLogger` | Write the ordered event stream to a structured `.events.jsonl` file and a human-readable `.summary.txt` file under the invocation's log directory. |
| `RunLog` | The detailed ordered record of the invocation's algorithm events. |
| `ExecutiveSummary` | Use the structured summary record defined in architecture §6.6, with route Operator and composition statistics grouped by choice and requested size. |
| `SolutionFormatter` | Optionally describe a final bus plan in the human-readable summary, for example by listing each bus's route and assigned students. The structured event file uses the plan's `SolutionId` and outcome details. |
| `RunResult` | Expose the final search state, committed candidate outcomes in processing order, termination reason, log and summary paths, diagnostics, and the latest successful checkpoint reference. |
| `MaintenanceResult` | Return the updated checkpoint after active-pool cleanup or feedback reset, with its log artifacts and latest successful checkpoint reference. |
| `Search` | The continuing history of visited route plans, which can span run invocations. |
| `Run invocation` | One `run()` call with its own budget and result. |
| `Engine` | Coordinate route-plan processing and maintenance, and own shared search state. |
| `Service` | Perform supporting operational tasks, such as evaluation, execution, and checkpoint persistence. |
| `Policy` | Own a replaceable search rule, such as archive membership, base selection, or operator feedback. |
| `ComponentFailure` | See the shared failure contract in architecture §5.11; the distinction between an application `invalid` finding and a callback or contract failure is illustrated in §2. |
| `ProcessingStatus` | See the shared definition in architecture §5.7 and duplicate handling in the algorithm's candidate-processing contract. General component-failure classification follows architecture §5.11. |
| `ResumeCompatibilityError` | Report a continuation mismatch, such as resuming with the old two-objective declaration or its `ProblemInstanceId` after adding maximum ride distance. |
| `multiobjective_vns()` | Construct and invoke the search using the application components and starting route plans described above. |

## 4. Public interface and design checks

The public construction and continuation inputs follow architecture §7. The
objects in §§2–3 show how those inputs bind to route plans, route data, and
search state.

The example exercises three design boundaries: route-plan identity remains
distinct from objective-vector equality (`Rα` and `Rβ`); application findings
separate representation validity from feasibility (`Rδ`); and the third
objective creates the ride-distance trade-off shown by `Rε`. The object tables
above map these examples to the shared contracts.

The simplified model fits the existing object contracts. A richer version can
add multiple schools, student-selected pickup stops, bus types, time windows,
maximum ride times, or walking constraints through its application data,
evaluator, and operator contracts while using the same architecture
boundaries.

## Bibliography

1. M. Spada, M. Bierlaire, and T. M. Liebling. “Decision-aid Methodology for
   the School Bus Routing and Scheduling Problem.” Proceedings of the 3rd
   Swiss Transport Research Conference, 2003. [Conference paper](https://www.strc.ch/2003/spada.pdf).
2. M. Spada, M. Bierlaire, and T. M. Liebling. “Decision-Aiding Methodology
   for the School Bus Routing and Scheduling Problem.” *Transportation
   Science*, 39(4):477–490, 2005. [https://doi.org/10.1287/trsc.1040.0096](https://doi.org/10.1287/trsc.1040.0096).
3. M. Bögl, K. Doerner, and S. Parragh. “The School Bus Routing and Scheduling
   Problem with Transfers.” *Networks*, 65(2):180–203, 2015.
   [https://doi.org/10.1002/net.21589](https://doi.org/10.1002/net.21589).
4. S. Ropke and D. Pisinger. “An Adaptive Large Neighborhood Search Heuristic
   for the Pickup and Delivery Problem with Time Windows.” *Transportation
   Science*, 40(4):455–472, 2006.
   [https://doi.org/10.1287/trsc.1050.0135](https://doi.org/10.1287/trsc.1050.0135).
