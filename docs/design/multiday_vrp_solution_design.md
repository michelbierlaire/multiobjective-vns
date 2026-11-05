# Multi-day VRP solution object design

## 1. Purpose and authority

This document defines the application-level objects that represent one
multi-day vehicle-routing plan. It explains which plan data are authoritative,
which views are derived from them, and how Operators create candidate plans
while preserving consistent derived views.

[`multiday_vrp.md`](multiday_vrp.md) remains authoritative for the problem
semantics, decision variables, representation validity, feasibility, and
objective calculations. This document organizes those decisions into a
solution object model. The instance data format is defined in
[`multiday_vrp_input_format.md`](multiday_vrp_input_format.md).

The search package treats an application solution as an opaque value. The
multi-day VRP application supplies its solution identity and codec facilities
to the search engine. The codec contract for this object model is specified in
[`multiday_vrp_codec.md`](multiday_vrp_codec.md).

## 2. Design principles

1. **The route plan is the source of truth.** Each plan decision has one
   authoritative representation. Activity, arc, custody, capacity, and timing
   views are derived from the plan.
2. **A solution contains decisions.** It stores route structure, assignments,
   and planned timing choices that are common across scenarios.
3. **Evaluation results are separate.** Scenario arrival times, feasibility
   findings, and objective values belong to the evaluation record associated
   with the solution.
4. **Solutions are immutable.** An Operator receives a solution as a read-only
   value and returns a new candidate. A focused builder may help construct that
   candidate.
5. **Operational infeasibility remains assessable.** A structurally valid
   candidate may omit or repeat an activity, exceed a capacity, or violate a
   time or personnel rule. The evaluator assesses those conditions according
   to `multiday_vrp.md`.
6. **Derived views can be rebuilt.** Indexes and caches are reproducible
   consequences of the authoritative plan and the fixed instance.

## 3. Object map

The conceptual structure is:

```text
VrpSolution
├── routes: ordered VehicleDayRoute records (active vehicle-days only)
│   ├── stops: ordered RouteStop records
│   │   ├── ActivityStop
│   │   ├── TransferOperationStop
│   │   └── DepotOnlyStop
│   └── legs: ordered RouteLeg records
└── transfer_steps: ordered TransferStep records
```

`VrpSolution` is the application solution value passed to Operators, the
evaluator, the identity service, and the codec. A vehicle-day route holds the
route decisions for one vehicle on one calendar day. A route stop is one
service activity, one transfer operation, or one depot-only stop. A route leg
is the chosen directed road path between adjacent route stops or between a
route endpoint and its nearest stop. A transfer step links the paired route
operations that move one package through a transfer facility.

The records use instance identifiers rather than references to mutable
instance objects. The instance supplies the data needed to resolve every
identifier and determine each activity's node, duration, qualifications, and
other attributes.

## 4. Authoritative solution records

The Python field schema below is authoritative for the immutable records.
Identifier aliases such as `VehicleId`, `NodeId`, and `ActivityId` are
annotations for built-in `str` values, not wrapper objects. Record constructors
require exact built-in scalar types (`str`, `int`, `float`, and `bool`); this
rejects `bool` for integer fields and rejects integer values for float fields.
Ordered collections use exact `tuple` values, and set-valued collections use
exact `frozenset` values. Constructors validate the type of every nested
record and collection member. They enforce types and immutability while the
application evaluator validates numeric finiteness, value domains, reference
resolution, and plan structure. This lets a correctly typed but invalid plan
reach evaluation for an `invalid` assessment.

The records have no constructor defaults: every field is supplied explicitly.
All stop variants carry the common timing fields shown in their definitions.
`ActivityStop.team_member_ids` is a `frozenset[str]` on every activity stop;
pickup and delivery stops use an empty set, while intervention stops carry the
assigned team. Non-finite float values are accepted as float-typed data and
reported as representation-invalid by the evaluator. A representation-valid
solution has finite values in every float field.

```text
ActivityKind = Literal['pickup', 'delivery', 'intervention']
TransferRole = Literal['out', 'in']
RouteStop = ActivityStop | TransferOperationStop | DepotOnlyStop

ActivityRef(
    kind: ActivityKind,
    activity_id: str,
)

TransferStepRef(
    package_id: str,
    step_number: int,
)

VrpSolution(
    routes: tuple[VehicleDayRoute, ...],
    transfer_steps: tuple[TransferStep, ...],
)

VehicleDayRoute(
    vehicle_id: str,
    day_index: int,
    overnight_depot_id: str,
    start_time: float,
    driver_id: str,
    stops: tuple[RouteStop, ...],
    legs: tuple[RouteLeg, ...],
)

ActivityStop(
    activity_ref: ActivityRef,
    team_member_ids: frozenset[str],
    pre_service_wait: float,
    wait: float,
    break_duration: float,
    tour_end: bool,
)

TransferOperationStop(
    transfer_step_ref: TransferStepRef,
    role: TransferRole,
    pre_service_wait: float,
    wait: float,
    break_duration: float,
    tour_end: bool,
)

DepotOnlyStop(
    node_id: str,
    pre_service_wait: float,
    wait: float,
    break_duration: float,
    tour_end: bool,
)

RouteLeg(
    arc_ids: tuple[str, ...],
)

TransferStep(
    package_id: str,
    step_number: int,
    facility_id: str,
)
```

The schema fixes field names, types, and nullability. The rules below define
the instance-dependent meaning and relationships of those fields.

### 4.1 `VrpSolution`

`VrpSolution` contains:

| Field | Meaning |
|---|---|
| `routes` | One `VehicleDayRoute` for each active vehicle-day, in strictly increasing `(day_index, instance vehicle order)` order. |
| `transfer_steps` | The selected package-transfer steps, ordered by package order in the instance and then by step number. |

`VrpSolution` and its nested authoritative records are immutable value
records. Their public constructors enforce the declared Python field types
and immutable collection forms, including tuples for ordered data and
frozensets for set-valued data. Constructor validation establishes a
type-correct record graph and raises `TypeError` for a field with the wrong
runtime type or collection form. The application evaluator performs the
instance-dependent value-domain and structural checks described in §6.

The key `(vehicle_id, day_index)` is unique among `routes`. The `routes`
collection is sparse: its entries are active vehicle-days, and omitted keys
represent inactive days. An inactive vehicle remains at its current depot,
which is the instance's initial depot on day one and its most recent overnight
depot thereafter.

An active route contains a required service activity, a transfer operation,
or at least one traversed road arc. This includes repositioning routes, which
may consist of a road path between depots with zero service stops. Vehicle-days
whose plan consists only of depot dwell and zero-arc connections are inactive
and are represented implicitly by the sparse `routes` collection.

Route presence identifies active vehicle-days. Depot continuity determines
each route's starting depot, the driver and intervention teams determine its
crew, and the transfer-step records determine its transfer count.

`overnight_depot_id` is a `DepotId` from `depots.csv`; its road node is
obtained through `node_of_depot[depot_id]`, as defined in §1.1 of
`multiday_vrp.md`. The same identifier convention applies to the vehicle's
initial depot and eligible overnight depots. A `DepotOnlyStop` stores the
physical `node_id` instead. Its node must be mapped from at least one
`DepotId`.

### 4.2 `VehicleDayRoute`

Each active route contains:

| Field | Meaning |
|---|---|
| `vehicle_id` | Vehicle assigned to the route. |
| `day_index` | Calendar day of the route, using the ScenarioBank day order. |
| `overnight_depot_id` | Depot where the vehicle ends this day's route. |
| `start_time` | Planned duty start time at the vehicle's current depot. |
| `driver_id` | Person assigned as the route's driver. |
| `stops` | Ordered sequence of route stops. |
| `legs` | Ordered sequence of directed paths, aligned with `stops`. |

`start_time` and every other absolute time value in a solution use the numeric
coordinate whose origin is the first planning midnight, defined by
`multiday_vrp.md` §1.1. The IANA time zone and offset in input timestamps
identify instants before conversion to this coordinate.
Duration fields such as waits and breaks use the `ScenarioBank` time unit.
Input timestamp conversion is specified in
[`multiday_vrp_input_format.md`](multiday_vrp_input_format.md) §3.2.
Every real-valued field in a representation-valid `VrpSolution` is a finite
Python `float`. The evaluator applies the numeric constraint tolerance in
`multiday_vrp.md` §1; identity compares scalar values exactly as specified in
§4.6.

The starting depot comes from the instance on day one and from the vehicle's
most recent overnight depot on later days, carrying depot continuity across
inactive days. `overnight_depot_id` is the route's depot decision. Vehicle
eligibility for the overnight depot is evaluated using the instance data.
`start_time` lies within that calendar day's allowed start interval, as
defined in §1.1 of `multiday_vrp.md`.

For `m` stops, `legs` contains `m + 1` paths: from the starting depot to the
first stop, between consecutive stops, and from the last stop to the overnight
depot. With zero stops, the single leg connects the two depots and represents
a repositioning route when it contains one or more arcs. An empty leg is a
zero-arc connection between locations at the same road node.

Every route stop carries its own timing decisions. The route-level
`start_time` is shared across all scenarios; simulated arrival and return
times are derived during evaluation.

### 4.3 Route-stop variants

Every `RouteStop` is a tagged value of exactly one of these kinds:

| Variant | Identifying data | Additional decision data |
|---|---|---|
| `ActivityStop` | `ActivityRef(kind, activity_id)` | For an intervention, the set of distinct assigned `team_member_ids`. |
| `TransferOperationStop` | `TransferStepRef(package_id, step_number)` and role `out` or `in` | The role identifies the operation within the linked step. |
| `DepotOnlyStop` | `node_id` | A depot dwell or break location at a road node mapped from at least one `DepotId`. |

An `ActivityRef` distinguishes `pickup`, `delivery`, and `intervention` even
when their identifiers have the same text. The activity's road node comes
from the instance. A transfer operation's node comes from its referenced
`TransferStep`. A depot-only stop identifies the physical road node directly.

Each intervention `ActivityStop` carries a `team_member_ids` frozenset,
including the empty set as a well-formed candidate assignment. The evaluator
checks its team size, crew membership, availability, and qualifications.

Activities and transfer operations located at a route's starting or overnight
depot appear as explicit route stops, just like operations at other locations.
A depot visit with multiple operations is an ordered block of stops at the
same physical depot node, connected by zero-arc legs. Co-located `DepotId`s
share one such visit; their IDs identify the initial depot and eligible
overnight-depot choices and preserve depot continuity. A `DepotOnlyStop`
represents depot dwell or a scheduled break at its `node_id`. Depots
encountered between route locations are represented as nodes within the
corresponding `RouteLeg`; a depot node passed only in a leg remains a transit
point.

An Operator can create an explicit intermediate depot visit by inserting a
`DepotOnlyStop` between two route locations and replacing their connecting leg
with two legs through that depot node. Removing such a stop merges its two
incident legs into one path between the surrounding route locations. The
depot-visit Operator contract, including timing, break, tour-marker, and
tool-segment effects, is defined in
[`multiday_vrp_operators.md`](multiday_vrp_operators.md) §4.6.

Each stop also records:

| Field | Meaning |
|---|---|
| `pre_service_wait` | Nonnegative planned wait before an activity or transfer operation. Its value is zero at a depot-only stop. |
| `wait` | Nonnegative planned stationary time after service, or after arrival for a depot-only stop; this is the model's post-service wait decision. |
| `break_duration` | Nonnegative scheduled break occupying the initial part of the post-service dwell, with `break_duration <= wait`. |
| `tour_end` | Boolean marker for a stop that ends a tour at a depot visit that continues as another tour. |

Service duration is resolved from the instance. `break_duration` is at most
`wait`. The names `wait` and `tour_end` correspond to the decisions
`wait[k, d, l]` and `tour_end[k, d, l]` in `multiday_vrp.md`. Tour-end markers
are set on the final stop of an intermediate destination-side depot visit when
the route continues as another tour. The final overnight depot ends the last
tour implicitly; an explicit final stop there has `tour_end = false`. The tour
count is one plus the number of markers and is bounded by the vehicle's
`max_tours_per_day` value.

Route position distinguishes occurrences. If an activity appears more than
once, each position identifies a separate occurrence for evaluation. Multiple
operations at one road node remain separate stops and are connected by
zero-arc legs, preserving their order and timing choices.

### 4.4 `RouteLeg`

`RouteLeg` contains the ordered `arc_ids` selected for that connection. The
path's origin and destination are derived from the vehicle-day endpoints or
the adjacent stop locations. The path must be a directed walk between those
nodes, as defined in §2 of `multiday_vrp.md`.

Road arcs are explicit plan decisions because the application chooses both
service order and road paths. The derived arc-occurrence view lists each arc's
appearances; `RouteLeg` remains the authoritative path decision.

### 4.5 `TransferStep`

Each `TransferStep` contains:

| Field | Meaning |
|---|---|
| `package_id` | Package whose custody is transferred. |
| `step_number` | Position of this transfer in that package's custody chain, starting at one. |
| `facility_id` | `NodeId` of the transfer facility used by the outgoing and incoming operations. It equals the facility's `node_id` in `transfer-facilities.csv`. |

The key `(package_id, step_number)` is unique. For each package, selected step
numbers form the contiguous sequence `1, ..., n_transfer`; its length is at
most the package's `max_transfers` value. Thus the number of `TransferStep`
records for a package represents `n_transfer[i]`. Step numbers follow the
package custody order. A transfer operation stop references the step and
identifies the `out` or `in` role. A feasible selected step has exactly one
stop for each role; occurrence counts and custody order are evaluated under
§3.2 of `multiday_vrp.md`.
The route containing that stop determines its vehicle, day, and position.
Thus sender, receiver, operation days, and operation order are derived from
the route stops. The evaluator checks that the sending and receiving vehicles
are distinct, that the facility is eligible for the package, and that the
custody sequence and timing are valid. The facility is stored once on the step
and is shared by both operations.

### 4.6 Solution identity

The VRP application's `SolutionIdentity.equivalent(a, b)` uses exact
structural equality across every authoritative field of `VrpSolution` and its
nested records in §§4.1–4.5. Record variants and scalar field values are
compared at their corresponding positions. The order of `routes`, `stops`,
`legs`, each leg's `arc_ids`, and `transfer_steps` is significant.
`team_member_ids` compares by set membership. Identifier values compare by
their exact string values.

Scalar equality includes the scalar's exact runtime type and value. Finite
floating-point values compare exactly, with no tolerance. The evaluator uses
the numeric feasibility tolerance defined in `multiday_vrp.md` §1 for
constraint checks; solution identity uses these exact scalar values. Thus two
values within a constraint tolerance remain distinct when their exact values
differ. Every NaN value
compares equal to every other NaN value at the corresponding field; positive
and negative infinity each compare equal only to the same signed infinity;
positive and negative zero compare equal. These rules make identity
deterministic for type-correct plans that contain semantically invalid numeric
values, while the application evaluator assesses their validity. Identity
applies to every constructed `VrpSolution`, including plans with invalid
domains, unresolved references, or inconsistent structural relationships.
Public record constructors reject values outside their declared record,
tuple, frozenset, and scalar field types before candidate processing.

Identity is computed from the authoritative `VrpSolution` record graph.
Derived views are rebuilt from that graph, the instance remains fixed for the
search, and evaluation results belong to registry records. A configured
lookup key must accept every constructed `VrpSolution` and assign the same key
to structurally equal solutions; distinct solutions may share a key, as
allowed by the general identity contract.

## 5. Data intentionally derived from the plan

The following read-only views are built from `VrpSolution`, the instance, or
both. They may be cached for one immutable solution value and discarded or
rebuilt at any time.

| View | Derived information |
|---|---|
| Activity index | Each `(activity_kind, activity_id)` and all route-stop positions where it occurs. |
| Arc index | Each `arc_id` and all `(vehicle_id, day_index, leg_index, arc_position)` occurrences. |
| Route-location view | Stop nodes, leg endpoints, depot visits, and transit-node entries for event counting. |
| Vehicle-day state | Active route lookup, starting depot, overnight depot, and depot carried through inactive days. |
| Transfer index | Outgoing and incoming operation occurrences for each `TransferStep`, including vehicle, day, and stop position. |
| Package-custody view | Pickup, transfer, delivery, and overnight custody transitions. |
| Crew view | `driver_id` together with the union of `team_member_ids` across the route's intervention stops. |
| Tour and segment view | Depot visits, tour boundaries, and depot-to-depot segments inferred from route stops and markers. |

Scenario evaluation derives the following values separately for each scenario:

- arc-entry, arrival, service-start, service-finish, and departure times;
- vehicle and personnel duty timelines, availability, work, driving, break,
  rest, and overlap measures;
- package loads, transfer-facility storage, and tool-capacity reservations at
  each capacity state;
- time-window and rule violations, objective penalties, and scenario costs.

For route `(k, d)`, the derived crew is
`{driver_id[k, d]} ∪ ⋃ team_member_ids` over its intervention stops. This is
the `crew[k, d]` assignment defined in §1.7 of `multiday_vrp.md`.

The evaluator then aggregates scenario costs into the objective vector.
Scenario-specific schedules, feasibility findings, and objective vectors are
derived during evaluation and stored in the evaluation record. The search
engine associates that record with the solution through its registry, as defined in
[`multiobjective-vns.md`](multiobjective-vns.md).

## 6. Representation validity and operational feasibility

The application evaluator owns instance-dependent representation validation.
It checks field domains; the uniqueness of vehicle-day route and transfer-step
keys; the active-route condition; contiguous transfer-step numbering within
each package's limit; the required `len(legs) = len(stops) + 1` relationship;
route-stop and transfer-step references; depot and activity locations; and the
directed-walk and endpoint requirements for every route leg. The `routes`
tuple must be in strictly increasing `(day_index, instance vehicle order)`
order; the evaluator rejects an out-of-order tuple as representation-invalid
before checking route path continuity or simulating scenarios. It also checks
the structural placement rules for `tour_end` markers, the instance time
units for planned times and durations, and `0 <= break_duration <= wait`.
These checks use the fixed `LoadedVrpInstance` and produce the
`ApplicationEvaluation` representation-validity finding. For a failed
representation check, the VRP evaluator returns:

```text
ApplicationEvaluation(
    representation_valid=False,
    feasible=None,
    objective_values=None,
    analysis=None,
)
```

This `invalid` assessment records an application finding; it is not a
`ComponentFailure`. The evaluator classifies such a plan before assessing
operational feasibility. A plan that passes representation validation
proceeds to the operational requirements in
`multiday_vrp.md`, including service coverage, transfer operation counts and
custody order, vehicle and facility capacity, vehicle and personnel
assignments, time windows, overnight-depot eligibility, and work rules. A
representation-valid candidate may receive an infeasible assessment while
retaining its complete decision data for subsequent search.

This separation lets an Operator make a coherent change even when the result
temporarily violates an operational constraint. It also gives the evaluator a
single plan representation from which to report the violation and calculate
all applicable costs.

## 7. Operator editing and immutability

An Operator reads one `VrpSolution` and returns a new `VrpSolution`. The
solution records are immutable; a temporary `SolutionBuilder` may provide
focused editing operations such as:

- insert, remove, or reorder a route stop;
- replace a route leg's arc sequence;
- change a route's overnight depot or start time;
- change a stop's waits, break duration, or tour-end marker;
- change the route driver or an intervention team;
- add or remove an active `VehicleDayRoute`, or reassign service units between
  vehicle-day routes;
- add, remove, or change a package transfer step and its route operations.

The builder edits the authoritative route and transfer-step records. It then
creates a type-correct solution and rebuilds or invalidates all derived views.
The shared plan assessor verifies representation validity before an Operator
returns its final candidate. The Operator result carries statistics such as
the modification count.

The instance remains fixed while an Operator edits a solution. The Operator
reads locations, service durations, capacities, skills, and other input data
from the instance context; the candidate stores its plan decisions.

The VRP Operator catalog, including each move's neighborhood-size
`k` and modification-count `m`, is defined in
[`multiday_vrp_operators.md`](multiday_vrp_operators.md).

## 8. Codec relationship

The application codec preserves the immutable `VrpSolution` value as an opaque
checkpoint payload. Its callback contract, payload identity, compatibility
identifier, and failure behavior are specified in
[`multiday_vrp_codec.md`](multiday_vrp_codec.md). The authoritative route and
transfer relationships defined here are the solution data covered by that
contract. Derived views are rebuilt from the restored solution and instance.

## 9. Representation decisions

- The route plan is the single source of truth; per-activity, per-arc,
  per-package, and per-scenario information is derived.
- Only active vehicle-day routes are stored. Inactive days retain their depot
  through the vehicle's previous ending depot.
- A route is an ordered stop sequence with an explicit arc path for every gap,
  including the route endpoints.
- Driver and intervention-team assignments are authoritative; crew membership
  is derived.
- Each package transfer step is represented once and referenced by its
  outgoing and incoming route-operation stops.
- Operators produce immutable candidate values, and evaluation data remain
  separate from the solution.
