# Configurable vehicle routing with multi-day planning and travel scenarios

This document defines a configurable vehicle-routing model spanning a range
from single-day routing with fixed travel times to multi-day routing with
scenario-based, time-dependent travel, package transfers, interventions, and
personnel rules. An instance uses the features supported by the data it
provides and the modeling choices it declares. The model specifies the
application data, plan decisions, feasibility conditions, and candidate
objectives. When travel is uncertain, the user represents its distribution
with a fixed set of simulated scenarios; when travel is fixed, one scenario
represents the deterministic travel data. Scenario statistics and penalties
for time windows, vehicle availability, and personnel rules use the simulated
outcomes.

The file-based schema for the instance data is specified in
[`multiday_vrp_input_format.md`](multiday_vrp_input_format.md). That schema
references the standalone traffic `ScenarioBank` format in
[`multiday_vrp_scenario_generation.md`](multiday_vrp_scenario_generation.md).

## 1. Input data

All entity sets are finite. `N`, `DepotIds`, `K`, `U`, `R`, and `S` are
nonempty, with `R ⊆ U`. Other sets may be empty when their field-level rules
allow it. All identifiers are unique within their respective sets. The input
also supplies a deterministic order for each entity set, used to make tied
choices and first-failure findings repeatable. All time values use one time
unit and one continuous axis. Scenario route simulation may extend beyond the
horizon boundary when a route is late. A day identifier indexes day-specific
intervention windows, scenario travel data, and vehicle and personnel
schedules. Time values, travel times, service durations, capacities, and load
amounts are finite real numbers; durations, capacities, and load amounts are
nonnegative.

### Numeric representation and comparisons

The VRP application parses real-valued input fields and performs calculations
with Python `float` values using IEEE 754 binary64 arithmetic. It rejects
Boolean values, NaN, positive or negative infinity in input data, and values
that overflow during conversion or calculation. The exception is the reserved
objective value `math.inf` for a hard-infeasible plan, as defined in §1.8.

For a numeric constraint comparison between finite values `a` and `b`, the
application uses the comparison tolerance
`tol(a, b) = 1e-9 + 1e-12 * max(abs(a), abs(b))`, in the unit of the compared
values. An upper-bound check `a <= b` passes when `a <= b + tol(a, b)`; a
lower-bound check `a >= b` passes when `a >= b - tol(a, b)`. Equality checks
use the same tolerance. This convention applies to capacities, time windows,
availability and duty limits, FIFO checks, and other numeric feasibility
checks. Scenario probabilities are positive finite floats; their sum must be
within `1e-12` of one, after which the loader normalizes them to sum to one.
For a penalty based on a numeric bound, an excess within this tolerance is
treated as zero violation; larger excesses are measured from the bound.
Equivalently, define `upper_violation(a, b) = 0` when
`a <= b + tol(a, b)`, and `a - b` otherwise; define
`lower_violation(a, b) = 0` when `a >= b - tol(a, b)`, and `b - a`
otherwise. For a directly calculated nonnegative duration `v` that measures
violation against zero, define `positive_violation(v) = 0` when
`v <= tol(v, 0)`, and `v` otherwise. Penalty functions receive these
tolerance-aware violation amounts.

Feature activation applies the same tolerance to real-valued comparisons. An
observed range is considered nonconstant or uncertain when
`max(values) - min(values) > tol(max(values), min(values))`. A positive real
quantity activates a feature when `value > tol(value, 0)`. Availability and
window intervals cover a horizon when every uncovered gap is within the
comparison tolerance. Capacity can be potentially binding when
`W_all > capacity + tol(capacity, W_all)`. Discrete comparisons, including
identifiers, Boolean values, and integer counts, use their exact declared
values. Section 1.9 applies these rules to each feature criterion.

Objective comparisons use the exact order of their Python `float` values,
without a tolerance, so Pareto dominance follows a transitive total order.
Finite evaluated objective values compare numerically, and `math.inf` compares
greater than every finite value. NaN and negative infinity are invalid
objective results. The canonical solution-identity rule in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md) compares
stored floating-point fields exactly outside declared pickup-order
symmetries; feasibility tolerance does not change solution identity.

### 1.1 Planning horizon and road network

The planning horizon is an ordered set of dated days
`D = {1, ..., n_days}`, where `n_days` is a positive integer. All dates and
times use the IANA time zone declared by the associated `ScenarioBank` and one
continuous time axis. Scenario generation defaults to `Europe/Zurich`, the
Geneva time zone; an application may select another IANA time zone. Every
absolute timestamp in the `ScenarioBank` and VRP instance uses local civil time
in the selected zone, with its numeric UTC offset identifying the instant as
defined in
[`multiday_vrp_input_format.md`](multiday_vrp_input_format.md) §3.2. For day
`d`, let `theta[d, 0]` be midnight at the start of its calendar date and
`theta[d, M[d]]` be midnight at the start of the next date. The calendar span
of day `d` is `[theta[d, 0], theta[d, M[d]])`.

The numeric time axis has origin `theta[1, 0] = 0`. For an absolute timestamp
`t`, its numeric coordinate is the elapsed physical time from the instant
`theta[1, 0]` to `t`, expressed in the `ScenarioBank` time unit. The timestamp's
numeric UTC offset identifies its instant; elapsed time is calculated between
instants rather than by subtracting local wall-clock readings. Calendar days
that span daylight-saving changes therefore have their actual elapsed duration.
For example, a daylight-saving transition day advances this axis by its actual
23- or 25-hour duration. Every `theta[d, j]`, time-window endpoint,
availability endpoint, route start time, and simulated time uses this
coordinate.

Each day is divided into `M[d]` indexed time intervals, where `M[d]` is a
positive integer and `I_time[d] = {1, ..., M[d]}`. The day grid is shared by
all arcs and scenarios. Its boundaries satisfy
`theta[d, 0] < theta[d, 1] < ... < theta[d, M[d]]`, and interval `(d, j)` is
`[theta[d, j - 1], theta[d, j])` for `j ∈ I_time[d]`. The interval widths may
differ within a day and between days. The pair `(d, j)` is the time-interval
index: `d` identifies the day and `j` identifies the interval within that day.
Each interior boundary is the first time of the interval that begins there.
The last boundary of each day marks that day's end, and the last boundary of
the final day marks the end of the planning horizon. Time windows are closed
intervals within the closed span from the first horizon boundary through the
final horizon boundary, so a window may close exactly at midnight or at the
end of the planning horizon.

The calendar-day span defines the domain for scenario travel-time functions
and their time-interval indices. Vehicle and personnel availability calendars
describe intended operating periods; out-of-calendar operation receives the
scenario-level penalty treatment in §3.5.

Each vehicle-day duty starts at its assigned depot within its calendar day and
returns by the day's ending boundary in every scenario for a feasible plan. Its
start time belongs to `[theta[d, 0], theta[d, M[d]])`; its scenario return time
is at or before `theta[d, M[d]]`. A later return is a hard feasibility
violation. The evaluator records a hard violation and applies the objective
value rule in §1.8. Route duty time therefore belongs to one calendar day for
a feasible plan, while availability calendars describe preferred periods
within that day.

For adjacent days in the horizon, the ending boundary of day `d` is the
starting boundary of day `d + 1`, so `theta[d, M[d]] = theta[d + 1, 0]`.

The city road network is a directed graph `G = (N, A)`, where `N` is the set
of road nodes and `A` is the set of directed road arcs. Nodes include both
service locations and transit points such as road intersections. A service
location is a node associated with a depot, pickup, delivery, intervention,
or transfer facility; routes may also pass through transit points. Arc
direction represents allowed travel. Each arc `a ∈ A` has a tail node
`tail[a]` and a head node `head[a]`;
distinct arcs may share endpoints. A route path between successive route
locations is a finite directed walk in `A` whose consecutive arc heads and
tails match and whose endpoints are those locations.

`DepotIds` is the nonempty set of depot identifiers. The instance supplies a
mapping `node_of_depot: DepotIds -> N`; multiple depot identifiers may map to
the same road node. Co-located depot IDs identify the same physical location
for route visits and depot-to-depot segments. Depot IDs identify each vehicle's
initial depot and eligible overnight choices, including the identity carried
through overnight continuity. Every vehicle may visit any depot during a
route. Each vehicle has a nonempty set of eligible overnight depot
identifiers, defined in §1.4. Route paths use the road node obtained from the
selected depot ID.

Every vehicle may traverse every directed arc in `A`.

### 1.2 Scenario-based time-dependent travel data

Let `S = {1, ..., n_scenarios}` be the finite scenario set, where
`n_scenarios` is a positive integer. Travel time on an arc is a random variable
conditional on the arc, day, and entry time. Write `X[a, d, time]` for the
travel time on arc `a` when a vehicle enters it at time `time` on day `d`. A
simulation scenario `s ∈ S` supplies a coherent realization of travel
conditions across
the modeled network, days, and departure times. For each scenario it defines a
finite, nonnegative travel-time function `tau[s, a, d](time)`.

The user supplies a finite scenario bank, which may be generated from
historical observations, a calibrated traffic simulator, or another
application model. If a scenario generator is used, the user runs it once
with fixed inputs to form the bank before search begins. A procedure for
generating the bank from aligned historical observations is described in
[`multiday_vrp_scenario_generation.md`](multiday_vrp_scenario_generation.md).
Each scenario represents joint travel conditions across arcs and times, so a
route simulation preserves the dependence specified by the user. Each function
`tau[s, a, d](time)` is the travel-time realization for scenario `s`; the
weighted scenario bank approximates the modeled travel-time uncertainty.
Service durations are deterministic input values, so the scenario bank models
road-travel uncertainty. If the user has
only marginal travel-time samples, the user declares the dependence
model used to form network-wide scenarios. The scenario bank defines exogenous
traffic conditions: every vehicle route is evaluated against the fixed
travel-time functions and scenario probabilities.

Within each day, every scenario and arc travel-time function is specified at
the shared time boundaries `theta[d, j]` from §1.1 and is linearly interpolated
between adjacent boundaries. Each interior boundary has one shared function
value for the intervals on both sides. The grid may be nonuniform. The
day-specific values at the two sides of midnight may differ; a departure
exactly at midnight uses the following day's values. Scenario data cover every
departure time that a route plan may produce within the planning horizon,
including departures outside vehicle and personnel availability calendars.

Let `H = theta[n_days, M[n_days]]` be the final horizon boundary. For every
scenario `s` and arc `a`, the application supplies a post-horizon travel-time
function `tau_post[s, a](time)` for every `time >= H`. Its values are finite
and nonnegative, and its boundary value matches the final-day profile:
`tau_post[s, a](H) = tau[s, a, n_days](H)`. As an application guarantee, for
departures at or after `H`, the arrival-time function
`time + tau_post[s, a](time)` is nondecreasing. Together with the final-day
FIFO profile and the matching boundary value, this preserves FIFO across `H`.
An arc entered before `H` uses the profile for
its calendar day; an arc entered at or after `H` uses `tau_post`. This
extension lets the evaluator determine the return time of a route that crosses
the horizon and assess its hard return-time requirement.

For each scenario, arc, and day, define the arrival-time function
`arrival_fn[s, a, d](time) = time + tau[s, a, d](time)`. FIFO means this
function is nondecreasing in departure time: whenever `time1 < time2`,
`arrival_fn[s, a, d](time1) <= arrival_fn[s, a, d](time2)`. Every scenario in the
bank must satisfy this condition for every arc over the full planning horizon.
A violation means that a later entry onto an arc could arrive earlier than an
earlier entry. This model includes planned
waits at stops; the FIFO condition rules out cases where waiting at a transit
node could produce an earlier arrival on the next arc.

Before the FIFO checks, validation confirms that every supplied boundary
travel-time value is finite and nonnegative. Because `arrival_fn` is linear
between grid boundaries, checking its endpoint values verifies monotonicity
across each interpolation segment. A validator checks arrival times at each
pair of adjacent grid boundaries within each day. At midnight, it also checks
that the previous day's left-hand arrival-time limit is less than or equal to
the following day's arrival time at midnight. This validation runs before
search begins. A scenario bank that violates any check is rejected as invalid;
the diagnostic identifies the scenario, arc, day, and interval or midnight
transition.

Before search, validation checks the post-horizon boundary value against the
final-day profile. During evaluation, each queried post-horizon travel time is
checked for finiteness and nonnegativity. A failed query check is an
`ApplicationEvaluator` contract violation under §5.11 of
`multiobjective-vns.md`; its diagnostic identifies the scenario, arc, and
queried time. These checks validate the boundary match and queried values; the
application guarantee supplies FIFO over the entire post-horizon domain and
across `H`.

Each scenario `s` has a positive finite probability `p[s]`. After input
validation and normalization under the numeric contract above,
`sum(p[s] for s in S) = 1`. Equal probabilities give the ordinary empirical
simulation sample. The scenario set and probabilities remain fixed for a
search. Travel simulation, rule assessment, penalty functions, and objective
statistics are deterministic for fixed instance data and plan, so evaluating
the same route plan produces the same result. An application evaluator
simulates every plan under this fixed bank, propagating
departure times through each route and querying the scenario travel-time
function at the actual entry time of every arc.

For a scenario outcome `Y[s]`, the probability-weighted mean is
`sum(p[s] * Y[s] for s in S)`. A probability-weighted quantile at level
`q ∈ (0, 1]` is the smallest value `y` for which
`sum(p[s] for s with Y[s] <= y) >= q`. Other statistics, such as a standard
deviation or a tail-risk measure, may also be calculated from the simulated
outcomes. The probability-weighted standard deviation is
`sqrt(sum(p[s] * (Y[s] - mean(Y))**2 for s in S))`, where `mean(Y)` is the
probability-weighted mean above. The application declares the exact formula
for each selected statistic. These are empirical estimates from the finite
scenario bank; at
`q = 1`, the quantile is the largest outcome in that bank. An objective
declares the scenario-level quantity it measures and the statistic used to
aggregate its values over `S`.

The application instance identity covers every input and modeling declaration
that affects plan validity, scenario simulation, or objective evaluation,
including the data in §§1.1–1.8 and the selections and formulas in §§3–5. This
includes the network, demands, vehicle and personnel data, scenario bank and
probabilities, travel-time model, service-coverage mode, objective declarations,
event-type groups, penalty functions and scales, and rule treatments. A resumed
search uses the same instance identity so the evaluator returns repeatable
objective and assessment values for each solution.

### 1.3 Events on the network

Let `E_type` be the set of event types and `E_evt` the set of event instances.
Each event instance `e ∈ E_evt` has a type `type[e] ∈ E_type` and a location
`loc_evt[e]` in that type's placement domain. Each type declares its domain as
road nodes or road arcs. Examples include traffic lights and rail crossings
on nodes and construction sites on arcs. Multiple event instances may share
a location; each instance contributes its own count. An event's effect on
travel time is already reflected in the scenario travel times; event counts
provide a separate measure of exposure.

For each event instance attached to an arc, count one occurrence per traversal
of that arc. For each event instance attached to a node, count one occurrence
when a route starts there and one whenever a traversed arc enters that node.
This includes transit nodes, service locations, and depot arrivals. A path
endpoint shared by consecutive path segments and service performed there form
one counted visit. A zero-arc connection preserves that visit count; a later
arc entry creates another visit. The user chooses which event types to count
together and which to report as separate objectives. Event-type groups may
overlap; each objective counts its own selected group.

### 1.4 Vehicles and depots

Let `K` be the set of vehicles. Each vehicle `k` has:

- a cargo capacity `C[k] >= 0`, measured in a scalar load unit shared by the
  instance;
- a personnel seat capacity `seat_capacity[k]`, a positive integer that
  includes the driver;
- an availability calendar specifying the time periods on each day when the
  vehicle is scheduled to perform route activities;
- a set of eligible overnight depots `DepotIds[k] ⊆ DepotIds`;
- an initial depot `h0[k] ∈ DepotIds[k]` for day 1;
- a required driving-skill set `required_drive_skills[k] ⊆ Q_drive`, as
  defined in §1.7,
  such as license classes needed to operate the vehicle;
- a positive integer `max_tours_per_day[k]`, defaulting to one; and
- package-vehicle compatibility data described in §1.5.

An instance may declare `exact_active_vehicle_count`, an exact number of
vehicles that must be used over the planning horizon. A vehicle is active
over the horizon when it has at least one active vehicle-day route, using the
definition in §2. If the declaration is absent, the number of active vehicles
is a decision and may vary from zero through `|K|`, subject to the other
requirements. If it is present with value `q`, a plan is feasible only when
exactly `q` distinct vehicles are active. The input format defines its range
and manifest encoding.

The deterministic order on `K` breaks tied duty start-time orderings in §3.5.

An availability calendar is a finite collection of intervals `[a, b)` with
`a < b` contained in the planning horizon. Its intervals
describe intended operating periods; §3.5 defines the penalty for activity
outside them. Personnel availability calendars use the same interval
representation.

Every vehicle begins the planning horizon with an empty cargo load.
Package cargo and tool capacity are defined in §§1.5–1.6.

Section 2 defines vehicle-day routes, tour boundaries, inactive vehicle state,
and overnight depot continuity. Packages may remain aboard overnight while
awaiting delivery, as specified in §1.5.

### 1.5 Packages and vehicle compatibility

Let `N_transfer ⊆ N` be the set of road nodes equipped for package storage and
transfer. The node identifier is also the transfer-facility identifier. The
input column is named `node_id`, and the solution field `facility_id` carries
that same `NodeId`. A transfer facility may share a node with a depot or
occupy another network node. Each `f ∈ N_transfer` has storage capacity
`storage_capacity[f] >= 0` in the same scalar load unit as vehicle cargo.
Transfer facilities are available for package handling throughout the
planning horizon. This set may be empty.

Let `P` be the set of package requests. A package `i ∈ P` is an indivisible
item with:

- a pickup node `pickup_node[i] ∈ N`;
- a delivery node `delivery_node[i] ∈ N`;
- a load amount `w[i] >= 0`;
- a pickup service duration `s_pick[i] >= 0`;
- a delivery service duration `s_deliver[i] >= 0`;
- a list of pickup time windows `W_pick[i]`;
- a list of delivery time windows `W_deliver[i]`;
- a nonnegative integer `max_transfers[i]`, defaulting to zero;
- a set of eligible transfer facilities `transfer_facilities[i] ⊆ N_transfer`;
- transfer-out and transfer-in service durations `s_transfer_out[i] >= 0`
  and `s_transfer_in[i] >= 0` when `max_transfers[i] > 0`.

Each window list contains one or more closed intervals `[a, b]` on the
continuous planning-horizon time axis, with
`theta[1, 0] <= a <= b <= H`. The intervals define the desired timing windows
used by the penalty rule in §3.4 and may fall on any date in the horizon. Every
package is available at its pickup node at the start of the horizon, and both
window lists contain at least one interval. Pickup and delivery may occur on
the same day or on different days. A pickup operation draws the package from
inventory at its pickup node. At a node in `N_transfer`, pickup stock forms
part of the initial inventory defined below; other pickup nodes have unlimited
modeled stock. A vehicle can reload packages during any tour by performing
pickup activities at their specified nodes. A package with
`pickup_node[i]` at a depot represents a depot replenishment request and can be
loaded during a depot visit. Sections 2, 3.2, and 3.3 define package custody
changes, their capacity effects, and the availability of delivered packages
to later activities that declare a prerequisite.

For a package with `max_transfers[i] > 0`, `transfer_facilities[i]` is
nonempty and the two transfer service durations are supplied. The plan may
select an ordered number `n_transfer[i]` of transfer steps from zero through
`max_transfers[i]`. Each selected step has a transfer-out operation on the
vehicle currently carrying the package and a transfer-in operation on a
distinct vehicle, both at one selected facility in
`transfer_facilities[i]`. Section 3.2 defines the custody order and carrier
assignments; §§2–3 define facility storage accounting and capacity.

For each vehicle `k` and package `i`, `compatible[k, i]` states whether that
vehicle may carry the package. Package load amounts, vehicle capacities, and
facility storage capacities use the same scalar unit throughout the instance.
Packages are indivisible. The initial inventory at transfer facility `f` is
`{i ∈ P : pickup_node[i] = f}`; §3.3 defines its capacity condition.

### 1.6 Tool requirements and interventions

Let `T` be the set of tool types. Each tool type `t ∈ T` has a nonnegative
capacity demand `w_tool[t]`, measured in the same unit as vehicle capacity.
A tool type is an abstract capacity reservation for interventions. Tool
availability is assumed independently for each depot-to-depot segment; the
capacity charge for a segment is defined below. Loading and unloading change
the vehicle's capacity state and take zero modeled time.

Let `J` be the set of intervention requests. Each intervention `j ∈ J` has:

- a location `loc[j] ∈ N`;
- a service duration `s[j] >= 0`;
- a day-specific list of time windows `W_intervention[j, d]`, which may be
  empty;
- a required tool set `T_required[j] ⊆ T`;
- a required qualification set `Q_required[j] ⊆ Q_intervention`, as defined
  in §1.7; `Q_required[j] = ∅` makes team-size and crew rules the applicable
  personnel conditions;
  and
- a minimum intervention team size `team_size_min[j]`, a positive integer
  that defaults to one.

For an assigned day, the intervention's listed windows define the desired
timing windows used by the penalty rule in §3.4. Each listed interval is a
closed interval `[a, b]` with
`theta[d, 0] <= a <= b <= theta[d, M[d]]`. An
empty list makes that day ineligible for the intervention. Every intervention
has at least one eligible day. Repeated work at one location is represented by
separate intervention requests; repeating their day-specific windows expresses
a recurring visit pattern while keeping each required visit individually
identified.

Personnel and qualifications are defined in §1.7. The team `team[k, d, j]`
assigned to intervention `j` by the plan in §2 must collectively hold every
qualification in `Q_required[j]` and contain at least `team_size_min[j]`
distinct people. In symbols,
`Q_required[j] ⊆ ⋃ {Q[u] : u ∈ team[k, d, j]}` and
`|team[k, d, j]| >= team_size_min[j]`. One person may satisfy several
required qualifications. The team performs the intervention together for its
full service duration `s[j]`, which is fixed by the input and independent of
team composition. The team size may exceed the minimum when the plan assigns
additional crew members, subject to crew and seat capacity. For
example, a two-person minimum requires two distinct crew members whose
combined qualifications cover the intervention's requirements.

A route segment runs from the start of a vehicle-day route or from the end of
one depot visit through the service block at the next depot visit or overnight
depot. The service block comprises the ordered service operations and
inter-stop dwell at that depot visit. The first segment begins at the day's
starting depot; the final segment ends at its overnight depot. An initial
depot visit at the starting depot before the first road leg belongs to the
first segment. A later return to that depot is an ordinary destination-side
depot visit. For a depot-to-depot segment `seg`, let
`J[seg]` be the interventions served along it, with depot-location
interventions assigned as specified below. Define
`T_segment[seg] = ⋃ { T_required[j] : j ∈ J[seg] }`. Its tool-capacity
reservation is `sum(w_tool[t] for t in T_segment[seg])`: each required tool
type contributes once per segment, even when several interventions in that
segment use it. Section 2 defines the capacity states and loading cycle for
this reservation.
Interventions at destination-side depots belong to the arriving segment. An
intervention in the initial start-side depot visit belongs to the first outgoing
segment, whose tool demand applies during that service and throughout the
segment. The first-segment tools are supplied at the starting depot. Section 2
defines when each segment's reservation is loaded and unloaded. An empty
intervention segment has zero tool-capacity load.

Service activities have identifiers `pickup(i)` and `delivery(i)` for each
package `i ∈ P`, and `intervention(j)` for each intervention `j ∈ J`. Let
`A_task` be this finite set of activities. The package custody chain in §3.2
imposes pickup-before-delivery precedence. The user may declare additional
ordered activity pairs in `precedence_requirements`; both members of every
pair belong to `A_task`. In a pair `(a, b)`, activity `a` is a prerequisite for
activity `b`. The instance may also declare `co_location_requirements`, a set
of unordered pairs whose members both belong to `A_task` and must be served at
the same network node. Section 3.2 defines the occurrence and timing rules for
these relationships. For example, an intervention that uses
a delivered package can require `delivery(i)` to precede
`intervention(j)` and require those two activities to be co-located. The
delivery must then finish before the intervention begins in every scenario,
and both use the same network node. The delivery and intervention may use
different vehicles when the other assignment requirements permit it; the
relationship governs their timing and location.

Each service activity has one fixed network node, given by its package pickup
or delivery location or its intervention location. A co-location requirement
therefore requires its paired activity locations to have the same node. A
co-located pair whose fixed input nodes differ is inconsistent with mandatory
service coverage, so the instance loader rejects it in `mandatory` mode. In
`penalized_omission` mode, the loader accepts the pair; a plan can satisfy the
relation by omitting at least one activity and paying its declared omission
penalty. When both activities occur, their fixed nodes must match.

### 1.7 Personnel, qualifications, and hours-of-service rules

Let `Q_drive` and `Q_intervention` be finite, separately identified sets of
driving-skill and intervention-qualification identifiers. Let `U` be the set
of personnel and `R ⊆ U` the subset authorized to drive a vehicle. Every
person `u ∈ U` has driving skills `drive_skills[u] ⊆ Q_drive`, intervention
qualifications `Q[u] ⊆ Q_intervention`, and an availability calendar. The
instance also defines work and rest rules for personnel and driving rules for
drivers. The vehicle's required driving skills are defined in §1.4. A driver
may serve as an intervention-team member when their intervention
qualifications meet the requirements in §1.6.

The simple rule set provides four rule types: maximum work time `H_work[u, d]`
per calendar day, maximum driving time `H_drive[r, d]` per calendar day,
maximum continuous driving time `H_cont[r]` between qualifying breaks, and
minimum rest duration `R_min[u]` between consecutive duties. The instance may
select these rule types individually and supplies a limit for each applicable
person or day. For each person whose selected simple work or continuous-driving
rule uses qualifying breaks, the instance supplies `B_min[u]`. A break qualifies
for person `u` when its duration is at least `B_min[u]`. For each person whose
selected simple minimum-rest or continuous-driving rule uses inter-duty rest,
the instance supplies `R_min[u]`. The minimum-rest rule uses this value as its
limit, and a continuous-driving counter resets after inter-duty rest of at least
this duration.
For a planning instance that must demonstrate compliance with a specified
hours-of-service (HOS) regime, the user supplies the regime's name, version,
and explicit rule set. Each rule
identifies the people it governs, the activity or rest measure it uses, its
time period or trigger and period boundary convention, its limit, and the
conditions that qualify a break or reset an accumulated measure. Under
`penalty` treatment, each rule also defines a finite nonnegative violation measure
that equals zero exactly when the rule is satisfied and is strictly positive
when the rule is violated.
Periods may be calendar days, fixed multi-day periods, rolling periods of
elapsed time, or intervals between qualifying breaks or rests.
Multiple rules may govern the same person and activity at once, such as a
daily driving limit, a longer-period driving limit, a continuous-driving
limit, and minimum rest requirements.

The instance may activate any subset of the simple rule types and may also
select a user-supplied HOS regime. By default, the simple-rule activation set
is empty and the active HOS regime is `none`. An HOS regime represents a
particular regulatory or organizational rule set. Every activated rule applies
simultaneously, including rules that govern the same measure over different
periods. The regime defines the meaning of its measures and their interactions,
including which activities count as work, which stationary intervals qualify
as breaks, and how breaks and rests reset or reduce accumulated quantities.
Planned route-stop breaks and the gaps between assigned duties provide the
break and rest intervals assessed by the selected rules.

The VRP application binds the selected HOS regime ID and version to its rule
implementation. That implementation evaluates the modeled personnel duties
and returns the rule findings or violation measures described in this section.
Its configuration validator declares the applicable `(rule_id, personnel_id)`
pairs, and its scenario evaluator returns one finite nonnegative violation
measure for each pair. A zero measure means the rule is satisfied for that
person and scenario; a positive measure means it is violated. The exact
registration and callback contract is defined in
`multiday_vrp_input_format.md` §12.

For rules whose periods reach before the planning horizon, the instance
provides pre-horizon work, driving, break, and rest history covering the
longest required look-back period and establishing each person's opening
accumulations. The instance also declares the terminal boundary convention
for rules whose required rest or measurement period extends beyond the last
planned duty, including any post-duty rest requirement and assumed off-duty
continuation used for that check. These boundary data let the evaluator assess
the selected rules across the full modeled duty timeline.

The instance selects one treatment for its work, driving, and HOS rules:
`penalty` or `compliance`. Under `penalty`, the rule violations contribute
scenario-level penalty measures as defined in §3.5. Under `compliance`, every
selected rule is a hard requirement in every supplied scenario, as defined in
§3.6. The default treatment is `penalty`.

The instance declares one `staff_depot_mode`, applied to all personnel:

- `available_at_assignment`: each person is available at the assigned route's
  starting depot at the beginning of each duty, regardless of the depot used
  for that person's preceding duty. The application represents repositioning
  through this availability assumption.
- `depot_continuity`: each person's first assigned duty begins at an input
  initial depot `staff_initial_depot[u] ∈ DepotIds`; each later assigned duty
  in the order defined in §3.5 begins at the overnight depot where that
  person's preceding duty ended.

The `staff_initial_depot` value is required for every person when
`staff_depot_mode = depot_continuity`. The selected mode is fixed for the
instance and defines the depot relationship between assigned duties.
Availability calendars use the interval representation in §1.4. Rule limits
are finite nonnegative durations; break and rest minima are positive.

Every active vehicle-day route has a crew `crew[k, d] ⊆ U` for its full duty.
Its assigned driver `driver[k, d] ∈ R` belongs to that crew, and
`|crew[k, d]| <= seat_capacity[k]`. Crew members travel with the vehicle from
its starting depot to its overnight depot.
Technicians therefore share the vehicle's route, travel times, and stop
schedule. A technician is a person assigned to an intervention team; a
driver may also serve as a technician when qualified. The crew consists
exactly of the assigned driver and the personnel assigned to intervention
teams on that route:

```text
crew[k, d] = {driver[k, d]} ∪ {u ∈ U :
    u belongs to team[k, d, j] for some intervention j served on (k, d)}
```

For each intervention on the route, the plan assigns one team drawn from that
crew; the qualification and team-size conditions are defined in §1.6. A person
may serve on different vehicle-day crews over the horizon, and the instance's
`staff_depot_mode` governs that person's location between duties. Crew
membership and intervention-team assignments are decision data.

A person's duty is the assigned vehicle-day route from its start at the
vehicle's starting depot until its return to the overnight depot. Availability
follows the soft-constraint treatment in §3.5. Under the simple work-rule
set, a person's work time includes time spent traveling with the vehicle,
servicing, waiting, and taking post-service dwell, less qualifying breaks.
The assigned driver's driving time is the sum of arc travel durations while
that person drives. A selected HOS regime defines its own work and driving
measures according to the rule declarations above.

At any route stop, including a service stop and an explicitly represented
depot-only stop at the starting, intermediate, or overnight depot, a route may
schedule a stationary break of duration `break[k, d, l]`, where `l` identifies
the stop. The break is part of that stop's post-service dwell `wait[k, d, l]`
and satisfies
`0 <= break[k, d, l] <= wait[k, d, l]`. Under the simple rule set, a break
qualifies for person `u` when its duration is at least `B_min[u]`; a qualifying
driver break resets the continuous-driving counter. Shorter stationary
durations count as work. An HOS regime supplies its own break qualification
and reset rules. The scheduled break occupies the initial part of the stop's
post-service dwell: it starts at service completion, or at arrival for a
depot-only stop, and lasts `break[k, d, l]`. Any remaining dwell follows the
break. Waiting before service to reach a window opening counts as work and
pauses driving under the simple rule set.

Under the simple rule set, the continuous-driving counter accumulates the
assigned driver's arc-driving durations. Service, waiting, and depot dwell
pause driving while retaining the accumulated duration. The counter
resets after a qualifying break or after inter-duty rest of at least
`R_min[r]`. Rest between consecutive duties assigned to the same person is
measured from the end of the earlier duty to the start of the next. Under the
simple rules, each person begins the horizon rested and each selected
continuous-driving counter begins at zero; post-horizon rest is outside the
simple model. For an HOS regime, the instance supplies pre-horizon history and
a terminal boundary convention.

Availability and duty-overlap violations contribute scenario-level
penalty measures under §3.5. Rule violations selected for `penalty` treatment
also contribute those measures. Rules selected for `compliance` treatment
are hard requirements under §3.6. The exact measures and their relation to
objective penalties are defined there.

### 1.8 Objective declaration

Let `n_objectives` be a positive integer and
`O = {1, ..., n_objectives}` the ordered objective index set. The application
declares one objective for each `o ∈ O`, in objective-vector order. Each
objective specifies a base scenario cost `C_o[s, x]` for route plan `x`
(defined in §2) and scenario `s`, a penalty contribution `P_o[s, x]` for the
violation categories explicitly assigned to that objective in §§3.4–3.5, an
omission penalty `M_o[a]` for each required service activity `a` when the
coverage mode in §3.2 allows omission, and an aggregation statistic `rho_o`.
Define the omission cost for objective `o` by

```text
OmitCost_o(x) =
    sum(M_o[a] for each omitted activity a)  if
        service_coverage_mode = penalized_omission
    0                                         if
        service_coverage_mode = mandatory
```

The scenario-level objective cost is `Y_o[s, x] = C_o[s, x] + P_o[s, x] +
OmitCost_o(x)`, and its objective value is

```text
F_o(x) = rho_o({(p[s], Y_o[s, x]) : s ∈ S})
```

The objective vector is `F(x) = (F_o(x) : o ∈ O)` in the declared order.

When service-window penalties are active, the application declares one
dedicated time-window-violation objective. Its base measure is the built-in
`zero` measure, and it receives the `early_window` and `late_window` penalty
categories. Those categories belong to this objective alone. Their scenario
penalty total is summarized by this objective's declared statistic, such as a
weighted mean or maximum. This keeps service-window penalties as their own
Pareto coordinate alongside vehicle-count, distance, and other objective
values. The same objective may also collect vehicle-availability,
personnel-availability, or duty-overlap penalties when the application wants
one combined temporal-violation dimension. Other penalty categories remain
assignable to any objective according to the input-format rules in
`multiday_vrp_input_format.md` §10.1.

The application supplies the fixed data and calculation rule needed by each
base cost `C_o`. For example, a distance, toll, fuel, or operating-cost
objective requires the corresponding network, vehicle, or activity attributes
as instance data. Built-in and application-registered base measures use the
scenario context and callback contracts in
`multiday_vrp_input_format.md` §12. Section 4 defines the travel-time and
event-count base costs.

For a plan that satisfies every hard requirement in every scenario, each base
cost `C_o[s, x]`, penalty contribution `P_o[s, x]`, scenario cost `Y_o[s, x]`,
and objective value `F_o(x)` is a finite Python `float`; `P_o[s, x] >= 0`. The
declared aggregation statistic returns one finite value and uses the
scenario probabilities as weights. The application's `ObjectiveFunction`
uses Python float ordering for these values. For a plan that violates a hard
requirement, the evaluator assigns `math.inf` to every objective. This is the
application's reserved `ObjectiveValue` sentinel. The generic package allows
an application-defined ordered objective-value domain; its `FiniteReal` type
governs package numeric settings and policy values. `math.inf` compares greater
than every finite VRP objective value, and its formatter returns a readable
hard-infeasible label. This sentinel remains greater than feasible costs at
every scale.

The evaluator assigns `math.inf` only after identifying a hard-feasibility
failure. For an otherwise feasible plan, a NaN, infinity, or arithmetic
overflow in a cost, penalty, or aggregation is an unusable objective result;
the evaluator reports the application assessment and contract diagnostic
defined by `multiobjective-vns.md` §5.6.

An objective includes only the violation categories assigned to it, using the
corresponding user-defined functions and scales. A category assigned to more
than one objective contributes separately to each of those objectives. Every
penalty contribution is zero when its associated rule is respected and
positive when it is violated. In `penalized_omission` mode, the user declares
a finite positive `M_o[a]` for every objective and required activity; this
amount is added to every scenario cost for each omitted activity. These
contributions enter before scenario aggregation. A deterministic base measure
has the same value in every scenario; its full objective cost may vary with
scenario penalties. Section 4 defines the available objective measures.

Every representation-valid plan receives an objective vector. If a hard
feasibility requirement fails in the plan or in any scenario, the plan is
infeasible and each minimized objective receives the reserved `math.inf`
value. The evaluator checks hard requirements in the order of §§3.1, 3.2, and
3.3, then checks compliance-treated rules in §3.5. Within each section, it
checks requirements in document order. Within a requirement, it scans
scenarios in `S` order, entity identifiers in their declared order, and route
elements in route order, as applicable. It records the first hard failure and
ends evaluation of that plan at that point. Feasibility findings identify the
plan as infeasible and report the triggering violation, including its scenario
when the violation is scenario-dependent. A plan that passes all hard
requirements receives the soft-constraint and service-omission penalties
defined above; scenario costs and objective values are calculated as defined
in §4. Section 3.2 determines whether a missing service is a hard failure or
an objective penalty. Service-coverage findings identify omitted or repeated
activities when the coverage check is reached.

### 1.9 Supported feature catalog

This catalog defines the model or search-support meaning of each supported
feature and the formal condition used to classify it. A feature is **active**
when its declared data or choice affects the set of permitted plans,
feasibility assessment, objective evaluation, or the feature-selected search
capabilities available for the instance. Activation means that the feature
participates in the declared model or search configuration; an individual
plan may still satisfy every associated condition. An optional feature is
**inactive** when its stated neutral case removes its additional choices,
restrictions, objective contributions, or feature-selected search
capabilities. The catalog also identifies features that are part of every
plan representation and therefore have no inactive state.

`VrpFeatureProfile` is an immutable record with one field:
`active_features: frozenset[VrpFeatureId]`. `VrpFeatureId` is a closed set of
case-sensitive string values given in the following table. Membership means
active; each known identifier absent from the set is inactive. The profile is
derived from the validated instance and feature dependencies. The two core
identifiers, `directed_route_paths` and `objective_declaration`, are always
members. It is fixed for the `LoadedVrpInstance` and is shared by every
candidate evaluated against that instance.

An **effective personnel availability calendar** is the union of a person's
declared availability intervals, compared with the §1 numeric tolerance.
Adjacent intervals whose gap is within tolerance have the same effect as one
continuous interval. For personnel-availability differentiation, assignment-
capable people are authorized drivers and, when interventions exist, people
who meet at least one intervention's qualification requirements. A **driver
assignment profile** records the assignment-relevant attributes of an
authorized driver who satisfies a vehicle's required skills: driving skills
when any vehicle requires a skill, intervention qualifications when any
intervention requires one, effective availability calendar, and initial depot
when staff depot continuity is selected. Profile comparison uses these
attributes, so people with equal assignment-relevant values have equivalent
profiles regardless of their identifiers.
Decision-domain flexibility is read from the validated instance values and is
distinct from feature activation. The input format defines field encodings and
defaults; this section owns the semantic activation criteria. For the capacity
profile, define
`T_used = ⋃ {T_required[j] : j ∈ J}` and
`W_all = sum(w[i] for i in P) + sum(w_tool[t] for t in T_used)`.

| Feature group | `VrpFeatureId` | Feature | Active condition and meaning | Inactive meaning |
|---|---|---|---|---|
| Travel | `directed_route_paths` | Directed route paths | **Core; always active.** Every leg between consecutive route stops uses a directed path in `G`. A matrix instance may represent each leg by its direct origin–destination arc (§§1.1, 2). | No inactive state; path connectivity is part of every plan. In a direct-matrix profile, the leg uses its direct arc. |
| Spatial support | `planar_node_coordinates` | Planar node coordinates | Active when the optional coordinate data cover every node used by a depot, package pickup or delivery, intervention, or transfer facility. They enable coordinate-based search Operators; plan feasibility and objective evaluation continue to use the network and application evaluation definitions. Input coverage is specified in `multiday_vrp_input_format.md` §5.1.1. | The optional coordinate data are absent, so the default catalog uses Operators whose inputs are available from the network and other instance data. |
| Travel | `within_day_time_dependent_travel` | Within-day time-dependent travel | Active when, for at least one scenario, arc, and day, the range of in-horizon boundary travel times exceeds the §1 comparison tolerance (§1.2). The post-horizon extension has a separate role in evaluating late returns and does not affect this profile flag. | Every in-horizon travel-time profile has a boundary range within the comparison tolerance for its day. Its level may differ across calendar days. |
| Travel | `scenario_uncertainty` | Scenario uncertainty | Active when the range of travel-time values across positive-probability scenarios exceeds the §1 comparison tolerance at some arc, day, and aligned departure-grid boundary (§1.2). | Every scenario range at an aligned arc, day, and boundary is within the comparison tolerance. A single scenario is the standard inactive case. |
| Travel | `network_event_exposure` | Network-event exposure | Active when at least one event instance is included in at least one selected event-count objective (§§1.3, 4.2). | No event count contributes to an objective. Event effects already represented in travel times remain part of the travel data. |
| Horizon and fleet | `multi_day_planning` | Multi-day planning | Active when `n_days > 1`; vehicle routes are linked by overnight depot continuity (§§1.1, 1.4, 3.1). | `n_days = 1`; the plan contains one calendar day's vehicle duties. |
| Horizon and fleet | `variable_active_vehicle_count` | Variable active-vehicle count | Active when `exact_active_vehicle_count` is absent; the number of vehicles used is a plan decision, constrained by the other instance requirements. | Inactive when `exact_active_vehicle_count` is supplied; every feasible plan uses exactly that number of distinct vehicles over the horizon. |
| Horizon and fleet | `overnight_depot_choice` | Overnight-depot choice | Active when at least one vehicle has more than one eligible overnight depot; the instance records the eligible set for each vehicle (§1.4). | Every vehicle has exactly one eligible overnight depot, so its overnight-depot choice is fixed. |
| Horizon and fleet | `multiple_tours_per_day` | Multiple tours per day | Active when `max_tours_per_day[k] > 1` for at least one vehicle (§1.4). | `max_tours_per_day[k] = 1` for every vehicle. |
| Horizon and fleet | `restricted_vehicle_availability` | Restricted vehicle availability | Active when a vehicle's availability calendar leaves a gap larger than the §1 comparison tolerance within a calendar day (§§1.4, 3.5). | Every vehicle is available throughout the horizon within the comparison tolerance. |
| Horizon and fleet | `potentially_binding_cargo_tool_capacity` | Potentially binding cargo/tool capacity | Active when `W_all > cargo_capacity[k] + tol(cargo_capacity[k], W_all)` for at least one vehicle; the capacities may restrict which package and tool loads can be carried (§§1.4–1.6, 3.3). | Every vehicle capacity is at least `W_all` within the comparison tolerance, so no possible package-and-tool load can exceed vehicle capacity beyond that tolerance. |
| Horizon and fleet | `potentially_binding_personnel_seat_capacity` | Potentially binding personnel seat capacity | Active when `J` is nonempty and the seat capacity is less than the number of people in `U` for at least one vehicle; the seat limit may restrict the driver-plus-intervention-team crew (§§1.4, 1.6–1.7). | `J` is empty, so each route crew consists only of its driver, or every vehicle has at least as many seats as there are people in `U`, so seat capacity cannot restrict crew membership. |
| Horizon and fleet | `package_vehicle_compatibility` | Package–vehicle compatibility | Active when `compatible[k, i]` is false for at least one vehicle–package pair (§§1.5, 3.3). | `compatible[k, i]` is true for every pair; any vehicle may carry any package. |
| Horizon and fleet | `package_transfer_operations` | Package transfer operations | Active when `max_transfers[i] > 0` for at least one package, with its required eligible transfer facilities (§1.5). | `max_transfers[i] = 0` for every package; packages remain with their carrier from pickup through delivery. |
| Horizon and fleet | `transfer_facility_storage` | Transfer-facility storage | Active when a transfer facility holds initial package inventory or is eligible for a permitted package transfer (§§1.5, 3.3). | No package is picked up from a transfer facility and no transfer operation can use one; facility storage has no effect on the plan. |
| Services and cargo | `package_pickup_delivery` | Package pickup and delivery | Active when `P` is nonempty; each package defines a pickup and delivery request (§§1.5, 3.2). | `P` is empty; the instance contains no package requests. |
| Services and cargo | `positive_service_durations` | Positive service durations | Active when any applicable package pickup, package delivery, intervention, or enabled transfer service duration exceeds `tol(duration, 0)` (§§1.5–1.6). | Each applicable duration satisfies `duration <= tol(duration, 0)`; service activities change location or custody without consuming material modeled time. |
| Services and cargo | `optional_service_omission` | Optional service omission | Active when `A_task` is nonempty and `service_coverage_mode = penalized_omission`; omitted activities incur the declared objective penalties (§§1.8, 3.2). | `service_coverage_mode = mandatory`, or `A_task` is empty; every required activity is served, with an empty activity set giving a vacuous coverage condition. |
| Services and cargo | `restrictive_service_windows` | Restrictive service windows | Active when package windows leave a gap larger than the §1 comparison tolerance in the horizon, or an intervention has a restricted eligible day or a window shorter than that day beyond the tolerance (§§1.5–1.6, 3.4). | Every package activity has windows covering the full horizon within tolerance, and every intervention is eligible throughout each day with a full-day window within tolerance. Service still follows the route and day-boundary rules. |
| Services and cargo | `interventions` | Interventions | Active when `J` is nonempty; intervention locations, service, day-specific windows, and team assignments enter the plan (§1.6). | `J` is empty; the instance has no intervention requests. |
| Services and cargo | `tool_capacity_reservations` | Tool-capacity reservations | Active when an intervention requires a tool type whose `w_tool[t] > tol(w_tool[t], 0)`; its capacity demand applies to the relevant depot-to-depot segment (§§1.6, 2). | Every intervention tool demand satisfies `w_tool[t] <= tol(w_tool[t], 0)`. |
| Services and cargo | `additional_activity_relationships` | Additional activity relationships | Active when `precedence_requirements` or `co_location_requirements` is nonempty (§§1.6, 3.2). | Both relationship sets are empty; the package custody and route rules defined elsewhere still apply. |
| Personnel | `driving_skill_restrictions` | Driving-skill restrictions | Active when `required_drive_skills[k]` is nonempty for at least one vehicle (§§1.4, 1.7). | Every vehicle has an empty required-skill set; every authorized driver is skill-eligible for every vehicle. |
| Personnel | `intervention_qualifications_or_team_size` | Intervention qualifications or team-size restrictions | Active when an intervention has a nonempty `Q_required[j]` or `team_size_min[j] > 1` (§§1.6–1.7). | Every intervention has no qualification requirement and a minimum team size of one; any one crew member can staff it. |
| Personnel | `restricted_personnel_availability` | Restricted personnel availability | Active when a person's availability calendar leaves a gap larger than the §1 comparison tolerance in the planning horizon (§§1.7, 3.5). | Every person's availability covers the full planning horizon within the comparison tolerance. |
| Personnel | `personnel_availability_differentiation` | Personnel availability differentiation | Active when at least two assignment-capable people have effective availability calendars that differ beyond the §1 tolerance. | All assignment-capable people have equivalent effective availability calendars; a shared calendar's restriction is represented by `restricted_personnel_availability`. |
| Personnel | `driver_assignment_differentiation` | Driver-assignment differentiation | Active when at least one vehicle has two or more eligible drivers and either their driver assignment profiles differ or staff-depot-continuity/work/driving/rest/HOS rules make allocation across duties consequential. | Each vehicle has at most one eligible driver, or every multi-driver eligible set has equivalent profiles with `staff_depot_mode = available_at_assignment`, `active_simple_rules = ∅`, and `hos_regime_id = none`. |
| Personnel | `staff_depot_continuity` | Staff depot continuity | Active when `staff_depot_mode = depot_continuity`; successive duties of a person must connect through that person's depot locations (§1.7). | `staff_depot_mode = available_at_assignment`; each assigned person is available at the assigned route's starting depot. |
| Personnel | `work_driving_rest_hos_rules` | Work, driving, rest, or HOS rules | Active when at least one simple rule is selected or the HOS regime is not `none`; the selected `penalty` or `compliance` treatment determines its effect (§1.7). | The simple-rule activation set is empty and the HOS regime is `none`; no such rule contributes a penalty or hard condition. |
| Evaluation | `objective_declaration` | Objective declaration | **Core; always active.** At least one minimized objective defines the objective vector and its base cost, penalties, and scenario statistic (§§1.8, 4). | No inactive state; objective evaluation is required for every instance. |
| Evaluation | `multiple_objectives` | Multiple objectives | Active when `n_objectives > 1` (§1.8). | `n_objectives = 1`; the objective vector has one component. |
| Evaluation | `scenario_aggregation` | Scenario aggregation | Active when `n_scenarios > 1`; the declared statistic aggregates multiple weighted scenario outcomes (§§1.2, 1.8). | `n_scenarios = 1`; each declared statistic returns the single scenario outcome. |

#### Dependencies between features

Some feature families require another family as their context. The validated
profile applies these dependency rules:

- **Travel uncertainty and scenario aggregation:** Within-day time dependence
  and scenario uncertainty are independent. A deterministic travel function
  may vary over the day, and uncertain scenarios may each use time-independent
  functions. Scenario uncertainty requires multiple distinct scenarios, so it
  necessarily activates scenario aggregation. Multiple scenarios with
  identical travel functions activate aggregation but represent deterministic
  travel.
- **Network-event exposure:** Event exposure requires both an event instance
  and an objective that counts its type. Event data may still inform the
  supplied travel-time scenarios when no event-count objective is selected.
- **Spatial search support:** `planar_node_coordinates` is an optional
  search-support feature. Its coordinate coverage and validation contract are
  defined in the feature table and input-format §5.1.1. Spatial coordinates
  guide feature-selected Operators; route paths, travel costs, feasibility,
  and objective values retain their network and evaluation definitions.
- **Package features:** Package compatibility, pickup/delivery windows, and
  package service durations apply when `P` is nonempty. Transfer operations
  require at least one package with `max_transfers[i] > 0`; eligible facilities
  and transfer-operation durations then apply to those packages. Transfer
  facility storage and initial inventory apply when a package pickup node is a
  transfer facility or a permitted transfer can use that facility, even when
  transfer operations are disabled for other packages. With `P` empty,
  package-specific features have no instance members to govern and are
  inactive.
- **Intervention features:** Tool reservations, intervention windows,
  intervention qualifications, and team-size requirements depend on `J` being
  nonempty. Positive tool-capacity reservations additionally require a
  required tool type with positive capacity demand. With `J` empty, tool and
  intervention-specific data have no plan effect.
- **Activity relationships and service rules:** Additional precedence and
  co-location requirements must reference activities in `A_task`. Service
  coverage, service windows, and service durations affect a plan only when it
  contains service activities. If `A_task` is empty, those service features
  have no applicable activities.
- **Personnel features:** Driving-skill restrictions depend on vehicle skill
  requirements. Intervention qualifications, team-size restrictions, and
  personnel seat limits depend on intervention requests because route crews
  include the driver and assigned intervention-team members. With no
  interventions, each route crew consists only of its driver. Availability,
  depot continuity, and work or HOS rules affect the assigned personnel and
  duties in a plan. Personnel-availability differentiation implies restricted
  personnel availability: distinct effective calendars mean at least one
  assignment-capable person has a restricted calendar. A common restricted
  calendar activates the availability condition and preserves profile
  equivalence. Driver reassignment becomes a default-catalog feature when
  driver profiles differ or an active continuity/work rule makes assignment
  across duties consequential. Identifier-only permutations share an
  equivalent profile.
- **Day linkage:** Cross-day depot continuity applies when `n_days > 1`. A
  single-day instance still chooses an overnight depot as the route's final
  depot when more than one is eligible, but that choice has no following-day
  route to link.

A parent feature in its neutral case makes dependent fields inapplicable; the
input schema defines how such fields are represented. When a parent feature is
present and its dependent data are required by §§1.1–1.8, missing values make
the instance incomplete and validation reports them as such.

The vehicle-capacity test uses `W_all` as a conservative upper bound, defined
by `W_all = sum(w[i] for i in P) + sum(w_tool[t] for t in T_used)`, where
`T_used = ⋃ {T_required[j] : j ∈ J}`. It includes all package load and all
tool capacity required by interventions, even when a particular plan cannot
carry all of them simultaneously. The transfer-facility storage test is
separate and uses `storage_capacity[f]` as defined in §1.5. A capacity feature classified active
may therefore remain nonbinding for every feasible plan in a particular
instance; the classification identifies a possible capacity restriction from
the declared data.

The feature dimensions are independent wherever their data permit it. For
example, scenario uncertainty may be active while time dependence is inactive,
or time dependence may be active while all scenarios are identical. The
inactive cases express simpler models within the same plan and evaluation
contracts.

As a concrete simple profile, a capacitated vehicle-routing instance can use
one planning day, one scenario with constant travel values, one depot, one
tour per vehicle, delivery-only package requests picked up at the depot,
mandatory service, horizon-wide service windows, universal package–vehicle
compatibility, no transfers, no interventions, and one generic authorized
driver per vehicle with no driving-skill or work-rule requirements. Personnel
and vehicle availability cover the planning day, with `staff_depot_mode` set to
`available_at_assignment`. Each planned leg uses its direct arc in the
complete directed origin–destination network; the arc data represent fixed
travel-time or distance-matrix values.

The same input and plan contracts support richer instances by activating the
corresponding dimensions through additional days, scenarios, depots, transfer
facilities, interventions, compatibility restrictions, and personnel rules.
The input loader derives `VrpFeatureProfile` from validated values using the
criteria in §1.9. The VRP Operator catalog factory uses that record to add the
Operators associated with active features. Validation checks completeness and
consistency relative to the input contract; the user supplies data that
faithfully describe the real application.

## 2. Decision variables and route-plan representation

Let `A_task` be the required service-activity set defined in §1.6. A route
stop is one required service activity, one package transfer operation, or a
depot-only stop, which represents a dwell or break location at a depot. Service
and transfer operations at that node each have their own route stop. A depot
location is a road node in the image of `node_of_depot`. A depot visit is a
maximal consecutive block of route stops at the same depot-location node, with
zero-arc paths between them. The resolved node of a service or transfer stop
determines whether it belongs to that visit; a depot node appearing only inside
a `Path` remains a transit node. Depot IDs mapped to one node therefore share
depot-visit, tour-boundary, depot-to-depot-segment, and tool loading/unloading
semantics. The initial `DepotId` identifies the day-one starting depot; each
overnight `DepotId` identifies an eligible end depot and the location carried
forward by depot continuity. Multiple operations at one node appear as
consecutive route stops connected by zero-arc paths. A plan `x` specifies for
each vehicle `k` and day `d`:

- `active[k, d] ∈ {0, 1}` is derived from whether a route is present for
  vehicle-day `(k, d)`, under the active-route conditions specified below;
- `Route[k, d]` is the ordered sequence of service activities, package
  transfer operations, and depot-only stops assigned to the vehicle-day
  route, including any explicitly modeled start-side or final depot stop;
- `Path[k, d, l]` is a finite ordered sequence of arc identifiers from `A`
  between consecutive route nodes, including the starting depot and overnight
  depot; it may be empty;
- `tour_end[k, d, l] ∈ {0, 1}` marks the end of a tour at an intermediate
  depot-location visit;
- `h_end[k, d] ∈ DepotIds` is the overnight depot identifier at which the
  route ends;
- `driver[k, d] ∈ R` assigns an authorized driver to an active route;
- `crew[k, d] ⊆ U` is the derived set of personnel assigned to an active route
  and includes its driver;
- `team[k, d, j] ⊆ U` assigns personnel to intervention `j` served
  on that route, and is defined when that activity is assigned to `(k, d)`;
- `n_transfer[i] ∈ {0, ..., max_transfers[i]}` is the derived number of
  selected transfer steps for package `i`;
- `transfer_facility[i, r] ∈ N_transfer` is the facility node recorded by
  transfer step `r`, for `r = 1, ..., n_transfer[i]`;
- `transfer_from[i, r] ∈ K` and `transfer_to[i, r] ∈ K` are derived as the
  vehicles assigned to the step's outgoing and incoming route operations;
  §3.2 defines their relationship;
- `transfer_out_day[i, r] ∈ D` and `transfer_in_day[i, r] ∈ D` are derived
  from the calendar days of those route operations;
- `start[k, d]` is the time the vehicle-day duty begins at its starting depot;
- `pre_service_wait[k, d, l] >= 0` is the planned wait before service at
  service stop `l`;
- `wait[k, d, l] >= 0` is the planned post-service dwell time at route stop `l`;
- `break[k, d, l] >= 0` is the duration of a scheduled break at route stop `l`,
  as defined in §1.7.

These variables describe the mathematical plan. The authoritative stored
representation is `VrpSolution` in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md): route
presence determines `active[k, d]`; driver and intervention-team assignments
determine `crew[k, d]`. The number of `TransferStep` records determines
`n_transfer`; each record stores its facility node, and the referenced route
operations determine sending and receiving vehicles, operation days, and
operation order. The route's `overnight_depot_id` stores `h_end[k, d]`. The
model variables remain useful for stating constraints and deriving views,
while the stored representation keeps one authoritative value for each
decision.

Immutable solution-record constructors enforce the declared Python field
types and collection forms. The application evaluator then determines whether
the complete plan is representation-valid against the fixed instance and the
structural relationships in this section. Its checks include value domains,
entity references, route and transfer relationships, and path connectivity.
The `routes` tuple follows strictly increasing `(day_index, instance vehicle
order)` order, and the evaluator rejects an out-of-order tuple as
representation-invalid before route-path checks and scenario simulation.
Every `Path` must be a directed walk from its prescribed origin to its
destination. A type-correct plan with an out-of-domain value, unresolved
reference, or broken structural relationship receives an `invalid`
application assessment with no objective vector, as specified in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md) §6. This
records the application's finding and produces no `ComponentFailure`. The
representation assessment occurs before §3 feasibility checks. A
representation-valid plan
proceeds to operational checks such as vehicle-specific overnight-depot
eligibility, driver authorization, crew qualification, service coverage, and
capacity.

`SolutionIdentity` resolves identity before evaluation, so its structural
equality rule applies to every type-correct `VrpSolution`, including plans the
evaluator later classifies as invalid. The identity rule is specified in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md) §4.6.

The active tour count is one plus the number of marked intermediate depot
stops. A marker is allowed only on the last stop of a destination-side depot
visit, grouped by its physical node as defined above, when the route continues
after that visit. Each marked stop ends the current tour after its service and
dwell; the next tour begins from that physical depot node. The final overnight
depot ends the last tour implicitly; an explicit
final route stop there has `tour_end = 0`. The count is at most
`max_tours_per_day[k]`. An unmarked depot stop remains within the current
tour. The daily route has one driver and crew across all its tours.

The transfer fields identify each selected step's facility, sending and
receiving vehicles, and operation days. Section 3.2 defines how those values
map to route operations and the package's custody chain.

Every decision in plan `x` is common to all scenarios: vehicle activation,
ordered routes and paths, tour boundaries, service and transfer assignments,
package-transfer counts, facilities, vehicles, and days, overnight depots,
driver, crew, and team assignments, start times, planned waits, and breaks.
Simulated arrivals, service start and finish times, departures, feasibility
findings, and scenario costs are evaluated separately for each scenario.
Planned `pre_service_wait`, post-service `wait`, and `break` durations are
fixed plan values shared across scenarios. At a service stop,
`pre_service_wait` gives the entire planned interval between arrival and
service start; the evaluator uses that duration in every scenario.

For an active route with `m` route stops, let `node_route[0]` be its starting
depot, `node_route[1], ..., node_route[m]` its ordered route stops, and
`node_route[m+1] = h_end[k, d]` its overnight depot. `Path[k, d, l]`, for
`l = 0, ..., m`, connects `node_route[l]` to `node_route[l+1]`. Every route
stop that serves a required activity or transfer operation identifies it in
the assigned `Route`; §3.2 defines activity occurrence counting and omission
treatment. The route sequence determines service order and depot-to-depot
segment boundaries. Every service or transfer operation at the starting depot
or overnight depot appears as an explicit route stop at that node. A service
at the overnight depot is followed by a zero-arc final path to `h_end[k, d]`.
The day-1 starting depot is `h0[k]`;
each later day's starting depot is the previous day's `h_end[k, d-1]`.
`active[k, d] = 1` exactly when its route contains at least one required service
activity, package-transfer operation, or traversed road arc. A vehicle-day
with none of these is represented as inactive, with empty `Route` and `Path`
fields even when it would otherwise contain only depot-only stops and zero-arc
paths. A route with depot-only stops and at least one traversed road arc is a
repositioning route. An inactive vehicle-day remains at its current depot,
has an empty `crew` field and null `driver` and route-time fields; its ending
depot equals its current depot. For an active vehicle, `start[k, d]` belongs to
`[theta[d, 0], theta[d, M[d]])`; the scenario return-time bound is defined in
§1.1.

An explicit depot visit at the starting depot before the first road leg is a
start-side depot visit of the first segment. Each subsequent depot visit is a
destination-side depot visit; completion of its service block ends the incoming
segment, and a following segment begins there if the route continues.
A depot appearing only inside a `Path` is treated as a transit node. A transfer
operation at a non-depot transfer facility is a route stop within the current
tour and leaves the current depot-to-depot segment unchanged. A stop marked as
a tour end under the rule above ends the current tour after its service and
post-service dwell; the next tour begins from the same depot.

Paths are traversed continuously through transit nodes. All planned waiting
and breaks occur at route stops.

For each route stop `l = 1, ..., m` in scenario `s`,
`arrival[k, d, l, s]` and `departure[k, d, l, s]` are the simulated arrival
and departure times. At a service stop containing a required activity or
transfer operation, the scenario-specific service start is arrival plus the
planned `pre_service_wait[k, d, l]`; service finish is service start plus
that operation's duration. Transfer operations use their package-specific
durations from §1.5. Time-window alternatives apply to required service
activities as defined in §3.4. At a depot-only stop, service duration and
pre-service wait are zero. For every route stop, departure equals arrival plus
pre-service wait, service duration, and planned dwell; the first two terms are
zero at a depot-only stop. Depot dwell is represented by `wait[k, d, l]`, and
a scheduled break occupies part of that dwell. Tool loading and unloading
relative to depot service and dwell follow the `T_capacity` phase rules below.
When a path has zero arcs, the
next route stop's arrival equals the preceding departure. The first path
begins at `start[k, d]`; each subsequent path begins at the departure time
from its preceding route stop. The route return time is arrival at its
overnight depot after the final path.

The onboard package set changes at pickup, delivery, transfer-out, and
transfer-in completion. A pickup or transfer-in loads the package onto its
assigned vehicle; a transfer-out moves it from the sending vehicle into
facility storage; delivery removes it from the plan. The package remains in
its prior custody state during each loading or unloading service and changes
custody when that service completes. For load bookkeeping in an infeasible
plan, each operation applies its requested custody change when the package is
in the appropriate prior state. Each occurrence counts toward coverage and
precedence assessment. Packages remain aboard through tour and overnight
depot boundaries. Delivery and transfer-out completion change package custody
as defined above.

The first interval of a day contains the cargo carried from the previous day;
the day-1 vehicle load is empty. The last interval's cargo carries to the next
day. For each vehicle-day, `LoadIntervals[k, d]` spans the full calendar day
and is the finite ordered sequence of capacity states separated by
package-custody completions, completion of depot service blocks, tool
unloading, tool loading, and route start or end. Each state corresponds to
one route phase between consecutive capacity-changing events. Its order is
fixed by the route, while its scenario-specific times follow the simulated
service and travel times. For state `h`, `P_aboard[k, d, h]` is the package set
and `T_capacity[k, d, h]` is the tool set assigned to that phase. Package
custody changes when pickup, delivery, transfer-out, or transfer-in service
completes, as defined above. For an inactive vehicle-day, the sequence contains
one parked state: it carries the package load from the preceding day, or an
empty load on day 1, and its tool set is empty. An active vehicle-day also
includes its parked load state before route start and after return. The
pre-start and post-return parked states have empty tool sets. When
multiple zero-duration capacity changes occur at the same time on one vehicle,
route-stop order determines their sequence, and the capacity state after each
stop is checked.

For an active route, `T_capacity` equals the current segment's `T_segment`
after loading and until the segment ends at its destination-side depot visit.
This reservation is capacity-charged during every state in the segment,
including travel, waiting, and service. The first-segment tools are loaded at
route start. When `Route` begins with a starting-depot visit, loading precedes
its first service; in other routes, loading precedes the first arc traversal
in `Path[k, d, 0]`. They remain capacity-charged through the start-side depot
visit when present, its final post-service dwell, and the segment's travel.

At a destination-side depot visit, the incoming segment's tools remain loaded
through every service operation in the visit, including intervention services,
and through dwell between consecutive stops in the visit. At completion of
the depot service block, after the final stop's service, those tools are
unloaded before its post-service dwell. That dwell is parked time with an empty
tool set, while cargo remains aboard. For a depot-only visit, unloading occurs
on arrival before its dwell.
When the final `Path` ends at the overnight depot and `h_end[k, d]` appears
only as that path endpoint, the final segment's tools are unloaded on arrival.
For an intermediate destination-side depot visit followed by another segment,
the next segment's tools are loaded after depot dwell and before its first
service or arc traversal.

For facility `f`, scenario `s`, and time `t`, `P_stored[f, t, s]` is the set of
packages in facility storage after all service completions at time `t` have
been applied. It includes packages initially awaiting pickup at `f` until
their pickup service completes, and packages placed there by a completed
transfer-out until the matching transfer-in service completes. The storage
load `sum(w[i] for i in P_stored[f, t, s])` is at most
`storage_capacity[f]` at every time in every scenario. A transfer-in releases
facility capacity when its service completes; a transfer-out occupies
facility capacity when its service completes. Service completions at the same
scenario time are applied atomically before the resulting storage state is
checked.

## 3. Feasibility constraints and simulated timing

### 3.1 Route continuity and depot continuity

Every active route starts at the vehicle's current depot and ends at an
eligible overnight depot. The representation contract in §2 defines the
directed-walk and endpoint requirements for each `Path`. Vehicle `k` starts
day 1 at its supplied depot `h0[k]`; each subsequent day's starting depot
equals the previous day's ending depot. Every active route has exactly one
assigned driver in `R` and a crew containing that driver. The assigned driver
satisfies the vehicle's required driving-skill set:
`required_drive_skills[k] ⊆ drive_skills[driver[k, d]]`. Every intervention
team on the route is a subset of that crew and satisfies the qualification
and size rules in §1.6.
The inactive vehicle state is defined in §2. Under
`staff_depot_mode = depot_continuity`,
each person's assigned route starts at the depot required by the person's
initial or preceding duty.

Every required activity and transfer operation assigned to vehicle-day `(k, d)`
starts before `theta[d, M[d]]` and finishes at or before that boundary in every
scenario. The vehicle-day route return time also satisfies the bound in §1.1.

### 3.2 Service coverage, precedence, and co-location

The instance declares `service_coverage_mode` as either `mandatory` or
`penalized_omission`; its default is `mandatory`. In `mandatory` mode, each
required pickup, delivery, and intervention occurs exactly once during the
horizon. A missing or repeated activity is a hard feasibility violation. In
`penalized_omission` mode, each required activity occurs at most once, and a
missing activity contributes its declared objective penalty `M_o[a]` from
§1.8. A repeated activity remains a hard feasibility violation in both modes.

For each `(a, b) ∈ precedence_requirements`, if `b` occurs, then `a` must also
occur and its service must finish before the service of `b` begins in every
scenario:
`service_finish[a, s] <= service_start[b, s]` for every `s ∈ S`. Thus, a
precedence pair makes the first activity a prerequisite for the second. In
`penalized_omission` mode, an omitted dependent activity receives its ordinary
omission penalty; its prerequisite may still occur. An occurring dependent
activity with an omitted prerequisite is a hard feasibility violation.
For each package, pickup, transfer operations, and delivery form its custody
chain. When pickup and delivery both occur with `n_transfer[i] = 0`, they use
the same vehicle, and pickup service must finish before delivery service begins
in every scenario. With zero transfers and an omitted delivery, the package
remains on the vehicle that performed pickup through the horizon. If
`n_transfer[i] > 0`, each step
`r = 1, ..., n_transfer[i]` has both `transfer_out(i, r)` and
`transfer_in(i, r)`. The ordered chain is pickup, then each transfer-out
followed by its matching transfer-in, followed by delivery when delivery
occurs. The pickup vehicle is `transfer_from[i, 1]`; for each later step,
`transfer_from[i, r] = transfer_to[i, r - 1]`. When delivery occurs, its
vehicle is `transfer_to[i, n_transfer[i]]`. When delivery is omitted, the
package remains aboard the vehicle `transfer_to[i, n_transfer[i]]` after the
last transfer-in. The two vehicles in each step are distinct, and both
operations use the selected facility, which belongs to
`transfer_facilities[i]`. The selected steps are exactly
`r = 1, ..., n_transfer[i]`, with both operations occurring exactly once for
each selected step. For each step `r`, `transfer_out(i, r)` occurs on vehicle
`transfer_from[i, r]` on day `transfer_out_day[i, r]`, and
`transfer_in(i, r)` occurs on vehicle `transfer_to[i, r]` on day
`transfer_in_day[i, r]`. A transfer-in may occur on a later tour or calendar
day than its matching transfer-out. The operation durations are
`s_transfer_out[i]` and `s_transfer_in[i]`, respectively, as defined in §1.5.
Pickup must finish before the first transfer-out begins; each transfer-out
must finish before its matching transfer-in begins; each transfer-in must
finish before the next transfer-out or delivery begins. These precedence
conditions hold in every scenario. Transfer operations model package-custody
changes through the chain above. `service_coverage_mode` and omission penalties
apply to package pickups, package deliveries, and interventions. If a package
pickup is omitted in `penalized_omission` mode, its transfer operations and
delivery are omitted as well, and `n_transfer[i] = 0`. For each pair in
`co_location_requirements`, if both activities occur, their service nodes
must be identical. In `penalized_omission` mode, an omitted member receives
its ordinary omission penalty, while a co-location check applies when both
members occur.

When a package pickup occurs and its delivery is omitted in
`penalized_omission` mode, the package remains aboard its final carrier through
the horizon and continues to consume vehicle capacity. With zero transfers,
that carrier is the pickup vehicle; with transfers, it is the vehicle assigned
to the last transfer-in. The missing delivery receives the declared omission
penalty. If both package activities are
omitted, each missing activity receives its own penalty. An omitted
intervention receives its activity's omission penalty; an intervention that
occurs must be scheduled on an eligible day as defined in §1.6.

### 3.3 Vehicle and facility capacity, package compatibility

Capacity is checked for every vehicle, day, and interval in `LoadIntervals[k,
d]`:

```text
sum(w[i] for i in P_aboard[k, d, h])
+ sum(w_tool[t] for t in T_capacity[k, d, h]) <= C[k]
for every k ∈ K, d ∈ D, h ∈ LoadIntervals[k, d]
```

A delivery releases its package's capacity when delivery service completes, so
a later pickup can use that capacity on the same day. For example, with
`C[k] = 1` and empty tool sets on the relevant segments, a package of load 1 can
be delivered before another package of load 1 is picked up. The capacity check
applies to the interval before delivery completion and the following interval
after the load changes. The tool term is
defined in §1.6. Every package in `P_aboard` must satisfy `compatible[k, i]`
for its vehicle. For each active route, the crew size also satisfies
`|crew[k, d]| <= seat_capacity[k]`; personnel seating is separate from cargo
capacity.

For every transfer facility `f`, scenario `s`, and time `t`, facility storage
must satisfy the capacity condition defined in §1.5:
`sum(w[i] for i in P_stored[f, t, s]) <= storage_capacity[f]`. This includes
the initial inventory awaiting pickup and packages between transfer-out and
transfer-in completion.

### 3.4 Scenario timing and time-window penalties

For each scenario `s`, the evaluator propagates route time through each chosen
road path. For an arc `a` entered at time `time`, its simulated arrival time is

```text
arrival = time + tau[s, a, d](time)       if time < H
arrival = time + tau_post[s, a](time)     if time >= H
```

For `time < H`, `d` is the calendar day containing the arc's actual entry
time. The arc uses that day's function even when its traversal continues past
midnight; a later arc uses the calendar day containing its own entry time.
An entry exactly at midnight uses the function for the day beginning then, as
specified in §1.2. The post-horizon function is defined in §1.2.

At a required service activity stop, each window alternative `[ell, u]`
defines a candidate window assessment. The planned pre-service wait and the
activity duration give
the scenario-specific service start and finish:

```text
service_start[s] = t_arrival[s] + pre_service_wait
service_finish[s] = service_start[s] + s_activity
early_violation[s, [ell, u]] = lower_violation(service_start[s], ell)
late_violation[s, [ell, u]] = upper_violation(service_finish[s], u)
window_satisfied[s, [ell, u]] iff
    early_violation[s, [ell, u]] = 0 and late_violation[s, [ell, u]] = 0
```

The evaluator records the simulated vehicle-day, stop, and arc timeline for
each completed scenario as a `VrpScenarioEvaluation`. Its immutable fields and
the exact values recorded for each stop and traversal are defined in
`multiday_vrp_input_format.md` §12.2. Registered base-measure and HOS callbacks
receive this completed scenario trace; they use the same timing results as the
built-in evaluator.

For a required activity, `s_activity` is `s_pick[i]` for `pickup(i)`,
`s_deliver[i]` for `delivery(i)`, or `s[j]` for `intervention(j)`.

The complete service interval must fit within the available window for the
window to be satisfied. Starting before its opening time beyond the numeric
tolerance incurs an early violation measured from the service start; finishing
after its closing time beyond the numeric tolerance incurs a late violation
measured from the service finish. The application defines separate early- and
late-window penalty functions and scales for the dedicated time-window-
violation objective in §1.8. Each function returns zero at a zero violation
and a finite positive value at a positive violation. For alternative window
`w = [ell, u]`, the evaluator calculates one local window score by applying
those functions and scales, then selects the alternative with the smallest
score. Ties select the alternative with the earliest opening time, then the
earliest closing time, then the lowest input `window_index`.
This local selection rule remains fixed when the time-window objective uses a
custom penalty aggregation.

For example, with window `[9:00, 10:00]` and a 30-minute service, arriving at
`8:50` with a planned 10-minute pre-service wait gives service start `9:00`
and finish `9:30`, so both violations are zero. Starting at `9:50` gives
finish `10:20` and a 20-minute late violation. Starting at `10:05` gives a
late violation as well, measured from the service finish. Starting before
`9:00` gives an early violation, measured from the service start. A planned
wait can move an early service start into the window; that wait contributes to
work time and may affect personnel-rule penalties.

For a service activity with alternative window list `W`, let `o_tw` be the
dedicated time-window-violation objective. Define the selected window index
and its local guidance score as

```text
window_index[s] = argmin over [ell, u] in W of
    scale[o_tw, early_window]
        * p_early[o_tw](lower_violation(service_start[s], ell))
    + scale[o_tw, late_window]
        * p_late[o_tw](upper_violation(service_finish[s], u))
window_guidance[s] = the minimized value above
```

For the selected alternative, the evaluator retains the early- and late-window
function outputs as separate category findings for `o_tw`. The configured
`PenaltyAggregation` receives those findings for the objective and scenario,
along with the category scales, and produces their contribution to
`P_o_tw[s, x]`. `window_guidance[s]` is a per-activity guidance value for
Operators; it is not a substitute for the configured penalty aggregation.

An intervention's applicable alternatives are drawn from its assigned day's
list in §1.6.

For scenario `s`, calculate window findings for every service occurrence and
the time-window-violation objective. The user may define aggregates by
activity type; every declared measure covers all applicable service
occurrences. The objective's configured penalty aggregator combines these
findings before scenario aggregation. Vehicle and personnel availability,
duty overlap, and rule violations selected for `penalty` treatment contribute
penalties only to the objectives to which the input assigns them, as described
in §§1.8 and 3.5. Rules selected for `compliance` treatment are feasibility
requirements under §3.6.

### 3.5 Vehicle availability and personnel-rule assessment

Each active vehicle-day route has one assigned driver and a crew as defined
in §1.7. Every crew member travels with that vehicle for the duty, and each
intervention team is present at its assigned service stop. Vehicle and
personnel availability use soft-constraint treatment. Work, driving, break,
and rest rules use the instance treatment declared in §1.7. For each scenario,
the evaluator calculates a nonnegative violation measure for each applicable
limit. For an upper limit `L` and realized quantity `z`, the measure is
`upper_violation(z, L)`. For a minimum requirement `L` and realized quantity
`z`, the measure is `lower_violation(z, L)`. A duration-valued violation
calculated directly, such as time outside an availability calendar or duty
overlap, is passed through `positive_violation(v)`, which returns zero when
`v <= tol(v, 0)` and `v` otherwise. HOS rule implementations apply the same
tolerance-aware bound rules when producing their measures.

For each active vehicle-day, vehicle-availability violation is the duration
of its duty interval outside the union of that vehicle's availability periods,
passed through `positive_violation`. For each person `u`, personnel-availability
violation is the sum over that person's assigned duties of `positive_violation`
applied to the duration outside the union of the person's availability periods.
For each selected simple daily work limit, the
person's work time is the sum of elapsed duty duration on that calendar day,
less the durations of breaks that qualify for that person under §1.7. For
each selected simple daily driving limit, a driver's driving time is the sum
of simulated arc travel durations on routes for which that driver is assigned.

Order each person's duties by `(start time, vehicle identifier, day index)`.
For each consecutive pair, let `t_end` be the earlier duty's end and `t_start`
the next duty's start. Their rest gap is `rest_gap = max(0, t_start - t_end)`.
For each selected simple minimum-rest rule, the insufficient-rest violation
is `lower_violation(rest_gap, R_min[u])`. Duty-overlap violation is calculated
over all unordered pairs of that person's duties: for intervals `[a_i, b_i]`
and `[a_j, b_j]`, the pair contributes
`positive_violation(max(0, min(b_i, b_j) - max(a_i, a_j)))`.
Simple-rule assessment covers the
planning horizon. An HOS regime also evaluates post-horizon rest obligations
under the terminal convention in §1.7.

For each selected simple continuous-driving limit, concatenate that
driver's arc-driving sequences across duties in the same duty order,
preserving route order within each duty. Separate continuous-driving blocks
at breaks that qualify for that driver and at inter-duty gaps of at least
`R_min[r]`. For a block `b`, let `drive_duration[b]` be its total arc-driving
duration. The continuous-driving violation is
`sum(upper_violation(drive_duration[b], H_cont[r]) for each block b)`. A stationary
interval shorter than `B_min[r]` is ordinary dwell and leaves the
continuous-driving counter unchanged. The initial block starts with the
state specified in §1.7.

For each rule in a selected HOS regime, the evaluator applies that rule's
declared activity measure, period or trigger, qualification conditions, and
boundary data to the same scenario-specific duty timeline. The result records
whether the rule is satisfied and, under `penalty` treatment, its violation
measure. This supports interacting daily, rolling multi-day,
continuous-driving, break, and rest rules within one regime.
The typed HOS callback and its complete per-rule/personnel result contract are
specified in `multiday_vrp_input_format.md` §12.3.

The user supplies a penalty function for each assigned kind of availability,
duty-overlap, or rule violation. Each function returns zero for a zero
violation measure and a strictly positive finite value for a positive
measure. The user also defines which objectives receive those categories and
how each objective's configured penalty aggregator combines them into
`P_o[s, x]`. Time-window categories remain assigned to the dedicated objective
defined in §1.8. Under `compliance` treatment, §3.6 determines feasibility
from the selected rule results. The objective evaluator retains the same
fixed-scenario evaluation model in either treatment.

### 3.6 Feasibility boundary

A plan is feasible exactly when every hard feasibility requirement holds in
every scenario `s ∈ S`. The hard requirements cover:

- route continuity, depot continuity, daily time-domain and return-time rules,
  driver authorization and vehicle driving skills, personnel assignments and
  qualifications, the tour-count limit, and `exact_active_vehicle_count`
  when declared in §§1.1, 1.4, 1.7, 2, and 3.1;
- required activity coverage under the selected `service_coverage_mode`,
  precedence, co-location, and intervention-day eligibility in §3.2;
- vehicle cargo capacity, package custody and compatibility, transfer-facility
  eligibility in §3.2, storage capacity and personnel seat capacity in §3.3; and
- every selected work, driving, break, and rest rule under `compliance`
  treatment, as defined in §§1.7 and 3.5.

A hard-constraint violation in any scenario makes the plan infeasible for the
scenario bank.

Time windows, vehicle and personnel availability, duty overlap, and rules
selected for `penalty` treatment contribute to the objectives assigned to
their penalty categories under §§1.8 and 3.4–3.5. Early- and late-window
penalties contribute to the dedicated time-window-violation objective.
Compliance-treated rules appear in the hard-requirements list above. A
compliance result certifies the plan against the selected rule set over the
supplied scenario bank.

In `penalized_omission` mode, each missing activity contributes the objective
penalty defined in §§1.8 and 3.2. The plan remains feasible when all remaining
hard requirements, including package precedence, hold.

## 4. Cost calculation and objective functions

Every objective is minimized, and the objective-vector order follows the
declaration in §1.8. For a plan that satisfies every hard requirement, each
objective is evaluated under every fixed simulation scenario and then
aggregated over the scenario set using its declared statistic. Section 1.8
defines the objective vector for a plan with a hard violation.

### 4.1 Travel-time objectives

For a hard-feasible plan and scenario `s`, let `C_travel[s, x]` be the total
travel time of plan `x`, calculated by summing the simulated duration of every
traversed arc across all vehicle-day routes. Each arc duration is evaluated
at that scenario's actual arc-entry time. A travel-time objective uses
`C_travel[s, x]` as its base measure and includes only additional penalty
categories explicitly assigned to that objective. Early- and late-window
penalties are evaluated in their dedicated objective, as defined in §1.8.
The declared statistic is applied to each objective's scenario totals;
examples include their mean, a selected quantile, or the largest value in the
scenario bank. For a quantile objective, the evaluator first calculates each
scenario's complete objective cost, then computes the weighted quantile across
those scenario costs.

Return times are checked against their daily boundaries. A late return is a
hard feasibility violation under §3.6. The post-horizon travel extension in
§1.2 supplies the travel times needed to determine a final return time;
§1.8 defines the resulting objective treatment.

Service durations, planned waits, and breaks affect later arc-entry times in
each scenario. They affect total travel time through the time-dependent travel
functions and affect route-duration or personnel-work objectives directly when
those measures are selected.

### 4.2 Event exposure and other objectives

For any nonempty application-selected event-type group
`G_evt ⊆ E_type`, `E_G(x)` is the sum of occurrences for event instances whose
type belongs to `G_evt`, over all vehicle routes and planning days, as defined
in §1.3. For an event-count objective, `C_o[s, x] = E_G(x)` for every scenario
`s`; its full scenario-level objective cost is
`Y_o[s, x] = E_G(x) + P_o[s, x] + OmitCost_o(x)`, following §1.8.
Selecting one group per type gives separate event objectives; selecting a
group containing several types gives one combined count.

Other possible objectives include:

- total or maximum route duration across vehicle-days;
- total planned waiting time across all vehicle-day route stops;
- total or maximum personnel work time, or driver work time;
- total operating cost or fuel use;
- number of vehicles used, number of vehicle-days used, or total number of
  tours; and
- maximum event exposure on any one route.

The built-in `zero` base measure returns `0.0` in every scenario. It supports
an objective whose scenario values consist of its assigned penalty categories,
such as the dedicated time-window-violation objective in §1.8.

For a maximum event-exposure objective, count `E_G` separately on each
vehicle-day route and take the maximum. For an active route, its duration is
scenario return time minus duty start time, including service, waits, and
breaks; an inactive vehicle-day has duration zero. Total route duration sums
these values over vehicle-days, and maximum route duration takes their
maximum. Personnel-work objectives use daily totals under the work
measurements selected in §1.7 and evaluated in §3.5, summed over
personnel-days or maximized over those units. A driver-work objective uses the
daily totals for drivers under the same selected work measurements, summed over
driver-days or maximized over those units. The number of vehicles used is
`sum(1 for k in K if sum(active[k, d] for d in D) > 0)`; the number of
vehicle-days used is `sum(active[k, d] for k in K, d in D)`.
When `exact_active_vehicle_count = q` is declared, `vehicles_used` equals
`q` for every feasible plan and carries no search discrimination.

The built-in `total_waiting_time` base measure is the sum, over every
vehicle-day route and every route stop, of `pre_service_wait + wait`. It
includes planned waiting before activities and transfer operations, and
stationary time after activities, transfer operations, and depot-only stops.
The scheduled break is contained within `wait`, so its duration is counted
once as part of that field. The measure uses the plan's stated waiting
durations, which are shared across scenarios; its value is therefore the same
in every scenario. A vehicle-day duty begins at `start_time`, and waiting
before the duty or after it has ended is outside this measure.
The total number of tours sums, over active vehicle-days, one plus the number
of marked tour-end stops in that route. This counts a repositioning route as
one tour and an inactive vehicle-day as zero tours. A deterministic base
measure has the same value in every scenario, while its full objective cost
may vary through scenario-dependent penalties as specified in §1.8. A
maximum over routes, personnel-days, or driver-days is zero when its set is
empty.

## 5. Instance-specific declarations

An instance is complete when it supplies the fields and modeling selections
defined in §§1.1–1.8 and the plan, feasibility, and objective declarations in
§§2–4. These values remain fixed while plans are compared.

The VRP application's `ApplicationEvaluator` applies the built-in rules and
measures in this document together with custom calculations selected by the
instance. `VrpExtensionRegistry` resolves custom base measures, scenario
aggregations, penalty functions, penalty aggregations, and HOS regimes. Its
typed registrations, validation timing, callable inputs and outputs, and
failure behavior are defined in
`multiday_vrp_input_format.md` §12. The evaluator combines those results under
the objective contract in §1.8; the generic search package receives the
resulting application assessment and objective vector. Custom post-horizon
travel rules follow the separate ScenarioBank extension contract in
`multiday_vrp_scenario_generation.md` §2.4.

## 6. Literature review and positioning

This section records a structured review of prior models that can inform or
overlap with the specification. The comparison uses the models' feature
combinations and assigned responsibilities.

### 6.1 Review question, search, and screening

The review question is: **Which published routing models combine, in one
planning specification, time-dependent travel on a road network, stochastic
traffic represented by coherent scenarios, pickup-and-delivery and
intervention work, multi-day vehicle and depot continuity, personnel roles,
driver rules, and multiple objectives?** A related question is which papers
cover the closest
subsets and therefore provide relevant modeling choices.

The search cutoff is **October 31, 2026**. The review uses two broad surveys as
its starting evidence. [Adamo, Gendreau, Ghiani, and Guerriero (2024), “A
review of recent advances in time-dependent vehicle routing,” *European
Journal of Operational Research*, 319(1), 1–15](https://doi.org/10.1016/j.ejor.2024.06.016),
surveys time-dependent vehicle routing, road-network path flexibility, travel
data, and dynamic routing. [Almadhaani and Ji (2026), “Time-Dependent Vehicle
Routing Problem with Time Windows: A Systematic Review of Modeling
Approaches, Solution Methods and Sustainability Implications,” *Sustainability*,
18(18), 9557](https://doi.org/10.3390/su18189557), searched six academic
databases on May 21, 2026 and reports 6,818 retrieved records and 61 included
journal articles. That review covers time-dependent routing with time
windows; targeted searches extend its scope to stochastic road travel,
pickup-and-delivery, multi-day operations, workforce scheduling, and tools.

The extension searches used scholarly search results and publisher, DOI, and
institutional-repository records. Search engines were queried with the terms
below; candidate records were checked against their titles, abstracts,
keywords, and available full texts. The query families were:

1. `('time-dependent' OR 'time varying' OR TDVRP) AND (stochastic OR scenario OR correlated) AND ('vehicle routing' OR 'pickup and delivery')`;
2. `('road network' OR 'path flexibility' OR 'geographical graph') AND 'vehicle routing' AND ('time-dependent' OR stochastic)`;
3. `('multi-day' OR multiperiod OR periodic) AND ('vehicle routing' OR 'pickup and delivery' OR 'technician routing') AND (depot OR driver OR crew OR overnight)`;
4. `('technician routing' OR 'field service') AND (tools OR parts OR intervention) AND (stochastic OR 'time-dependent' OR multiperiod)`; and
5. `(multiobjective OR Pareto OR robust) AND ('time-dependent' OR stochastic) AND 'vehicle routing'`.

Backward citation checks used the two reviews and the closest papers found in
these searches; forward citation records were checked where available. A
study was retained when its published model or evaluation method directly
addressed at least two of the specification's main dimensions, or when it was
a review covering one of the central strands. Screening compared each
retained study on network representation, travel-time uncertainty, path
decisions, service type, planning horizon, vehicle/driver coupling, resource
requirements, feasibility treatment, and objective structure. Journal
articles and full technical papers published by the cutoff were eligible.
For studies represented by publisher or repository records and abstracts, the
comparison uses the features stated in those materials.

The 2026 systematic review supplies a reproducible database-level search for
the time-dependent VRP with time windows. The targeted extension here supplies
a feature-based synthesis across adjacent research areas. Its review record
documents the query families, eligibility criteria, screened studies, and
source links. A publication-grade reproduction of the extension should also
archive the search platforms, query dates, result counts, and inclusion or
exclusion decisions. The comparisons describe evidence within the recorded
scope; additional sources or broader screening may extend the synthesis.

### 6.2 Time-dependent road networks and path decisions

- [Ichoua, Gendreau, and Potvin (2003), “Vehicle dispatching with
  time-dependent travel times,” *European Journal of Operational Research*,
  144(2), 379–396](https://doi.org/10.1016/S0377-2217(02)00147-9) established
  a FIFO time-dependent travel model used in much later routing work. Its
  travel conditions are deterministic, while this specification evaluates
  plans over a weighted scenario bank.
- [Huang, Zhao, van Woensel, and Gross (2017), “Time-dependent vehicle
  routing problem with path flexibility,” *Transportation Research Part B*,
  95, 169–195](https://doi.org/10.1016/j.trb.2016.10.013) makes road-path
  selection an explicit decision and studies deterministic and stochastic
  traffic. Its stochastic model uses two-stage path recourse after traffic
  information is revealed. Here, the planned route and road paths are common
  decisions evaluated across scenarios.
- [Raeesi and Zografos (2019), “The multi-objective Steiner pollution-routing
  problem on congested urban road networks,” *Transportation Research Part B*,
  122, 457–485](https://doi.org/10.1016/j.trb.2019.02.008) plans directly on
  urban road networks and combines time-dependent paths, time windows, flexible
  departure times, multiple trips, heterogeneous vehicles, and three
  objectives for fleet cost, fuel, and route duration. It provides a precedent
  for multiple tours as a vehicle-use decision; the present model adds
  stochastic scenarios, pickup-and-delivery, package transfers and facility
  storage, interventions, multi-day depot continuity, and driver assignments.
- The [Adamo et al. (2024) review](https://doi.org/10.1016/j.ejor.2024.06.016)
  distinguishes customer-graph routing from routing on the underlying road
  graph. It also documents path flexibility for objectives such as distance,
  tolls, fuel, and emissions, and describes the computational effort of
  time-dependent path queries. This supports treating the road path as a plan
  decision whenever the application objectives depend on path choice.

### 6.3 Stochastic travel, service windows, and objective evaluation

- [Taş, Dellaert, van Woensel, and de Kok (2014), “The time-dependent vehicle
  routing problem with soft time windows and stochastic travel times,”
  *Transportation Research Part C*, 48, 66–83](https://doi.org/10.1016/j.trc.2014.08.007)
  combines time-dependent stochastic travel, soft windows, and service
  reliability. It is a focused routing model; this specification exposes a
  general weighted scenario bank and user-defined scenario statistics and
  penalties.
- [Guo, Wallace, and Kaut (2019), “Vehicle Routing with Space- and
  Time-Correlated Stochastic Travel Times: Evaluating the Objective Function,”
  *INFORMS Journal on Computing*, 31(4), 654–670](https://doi.org/10.1287/ijoc.2019.0906)
  studies joint spatial and temporal travel-time dependence and scenario-based
  evaluation. It directly supports the requirement that a scenario describe
  coherent traffic across links and times. Its contribution centers on
  scenario generation and objective-evaluation stability.
- [Bomboi, Buchheim, and Pruente (2022), “On the stochastic vehicle routing
  problem with time windows, correlated travel times, and time dependency,”
  *4OR*, 20, 217–239](https://doi.org/10.1007/s10288-021-00476-z) studies
  correlated, time-dependent travel and route feasibility probabilities. It
  develops chance-constrained assessment; this specification uses scenario
  outcomes and application-defined penalties for soft violations, with its
  hard-feasibility contract stated in §3.6.
- [Wang, Dessouky, van Woensel, and Ioannou (2023), “Pickup and delivery
  problem with hard time windows considering stochastic and time-dependent
  travel times,” *EURO Journal on Transportation and Logistics*, 12, 100099](https://doi.org/10.1016/j.ejtl.2022.100099)
  combines pickup-and-delivery, stochastic time-dependent travel, service
  windows, and reliability measures. Its service windows are chance
  constraints; the present specification models soft-window violations as
  user-penalized objective contributions and extends the plan across days and
  intervention visits.
- [Miranda, Branke, and Conceição (2018), “Algorithms for the multi-objective
  vehicle routing problem with hard time windows and stochastic travel time
  and service time,” *Applied Soft Computing*, 70, 66–79](https://doi.org/10.1016/j.asoc.2018.05.026)
  explicitly seeks multiple trade-off solutions under stochastic travel and
  service duration. It focuses on a customer-service reliability formulation;
  the present specification uses a fixed scenario bank and includes road-path,
  multi-day, driver, and intervention decisions.
- [Duan, Lei, Sun, and Yang (2019), “Multi-Objective Robust Optimisation
  Method for Stochastic Time-Dependent Vehicle Routing Problem,” *Journal of
  Southwest Jiaotong University*, 54(3), 565–572](https://doi.org/10.3969/j.issn.0258-2724.20170617)
  combines stochastic time-dependent travel with a multiobjective robust
  formulation, including fleet size and worst travel time. It establishes
  prior art for the multiobjective stochastic time-dependent routing
  combination; this specification builds on that work with the integrated
  multi-day service, transfer, and personnel decisions described above.
- [Kubek (2025), “Urban Pickup-and-Delivery VRP with Soft Time Windows Under
  Travel-Time Uncertainty: An Empirical Comparison of Robust and Deterministic
  Approaches,” *Sustainability*, 17(24), 11308](https://doi.org/10.3390/su172411308)
  is a close urban-freight comparison: it uses a real road network, scenario
  travel data, pickup-and-delivery, soft windows, and travel-time/distance/
  service-quality criteria. Its stated scope centers on comparing robust and
  nominal planning; the current specification adds a multi-day linked fleet,
  driver schedules, qualified intervention teams that travel with their
  vehicles, and abstract tool-capacity reservations.
- The [Almadhaani and Ji (2026) systematic review](https://doi.org/10.3390/su18189557)
  finds that time-dependent VRP research already covers road networks,
  multiple objectives, multi-trip routing, synchronized fleets, and
  sustainability objectives. These dimensions are established research
  themes. The review centers on time-dependent routing with time windows; the
  synthesis here extends the scope to multi-day stochastic service models.

### 6.4 Multi-day operations, personnel, and service work

- [Zamorano and Stolletz (2017), “Branch-and-price approaches for the
  Multiperiod Technician Routing and Scheduling Problem,” *European Journal
  of Operational Research*, 257(1), 55–68](https://doi.org/10.1016/j.ejor.2016.06.058)
  links daily technician teams, assignments, routes, and time windows over a
  planning horizon. Its focus is deterministic maintenance work and workforce
  skills; scenario-based traffic on an explicit road graph is an additional
  dimension in the present specification.
- [Graf (2020), “Adaptive large variable neighborhood search for a
  multiperiod vehicle and technician routing problem,” *Networks*, 76(2),
  256–272](https://doi.org/10.1002/net.21959) is a particularly close
  integrated predecessor. It jointly plans truck deliveries and later
  technician installations over multiple days, with capacity, time windows,
  precedence, and technician work/rest patterns. Trucks and technicians have
  distinct routes and service roles, and travel is modeled by deterministic
  distances; the present model has stochastic road travel, intervention teams that
  travel with their assigned vehicles, and driver skills and work rules in a
  shared vehicle-day route representation.
- [Drexl, Rieck, Sigl, and Press (2013), “Simultaneous Vehicle and Crew
  Routing and Scheduling for Partial- and Full-Load Long-Distance Road
  Transport,” *Business Research*, 6, 242–264](https://doi.org/10.1007/BF03342751)
  combines multi-period pickup-and-delivery transport, vehicle/driver
  schedules, and driver work regulations. It addresses long-haul deterministic
  freight with crew exchanges and synchronized truck/driver paths; this
  specification addresses city-network planning with stochastic traffic and
  service interventions.
- [Lucci, Severín, and Zabala (2026), “Exact resolution of a simultaneous
  vehicle routing and crew scheduling problem in long-haul transport,”
  *International Transactions in Operational Research*, 33(5), 3158–3186](https://doi.org/10.1111/itor.70014)
  plans pickup-and-delivery requests with time windows over multiple days and
  coordinates truck and driver paths under driver regulations. Its long-haul
  model permits drivers to change trucks and use taxis; its objective is a
  scalar travel-cost and lateness measure, while this specification retains
  vehicle-day assignments and evaluates multiple objectives over traffic
  scenarios.
- [Avolio, Di Francesco, Fuduli, Gorgone, and Wolfler Calvo (2025),
  “Multi-day routes in a multi-depot vehicle routing problem with intermediate
  replenishment facilities and time windows,” *Computers & Operations
  Research*, 182, 107084](https://doi.org/10.1016/j.cor.2025.107084) studies
  time-window routing with multi-day routes, depots, and replenishment. Its
  freight-routing setting motivates explicit depot replenishment and carried-
  load continuity; the present model adds stochastic time-dependent road
  travel, package transfers between vehicles, a configurable number of tours
  per vehicle-day,
  pickup/delivery precedence, intervention resources, and drivers.
- [Izadkhah, Wang, Lainez-Aguirre, Pinto, and Gounaris (2026), “The periodic
  vehicle routing problem with multi-day trips,” *Transportation Research
  Part E*, 206, 104582](https://doi.org/10.1016/j.tre.2025.104582) links
  periodic service schedules with routes that span several days and driver
  hours-of-service constraints. Its routes can remain away from a depot over
  multiple days; this specification uses a separate depot-to-depot vehicle
  duty each day and requires depot continuity overnight. Repeated intervention
  visits can be represented as separate requests with recurring day-specific
  windows, alongside irregular requests in the same planning horizon.

### 6.5 Tools, multiple objectives, and personnel rules

- [Mathlouthi, Gendreau, and Potvin (2021), “Branch-and-Price for a
  Multi-Attribute Technician Routing and Scheduling Problem,” *SN Operations
  Research Forum*, 2(1)](https://doi.org/10.1007/s43069-020-00044-x) models
  technician skills, breaks, multiple time windows, and carried parts
  inventory, including replenishment at depots. This is a direct precedent
  for intervention-resource constraints. Its resource accounting tracks
  specific inventories and replenishment; the current specification uses an
  abstract tool-capacity reservation and general vehicle capacity within a
  multi-day stochastic routing model.
- [Goel and Vidal (2014), “Hours of Service Regulations in Road Freight
  Transport: An Optimization-Based International Assessment,” *Transportation
  Science*, 48(3), 391–412](https://doi.org/10.1287/trsc.2013.0477) treats
  driving, work, breaks, and rest as route-scheduling resources. Its
  schedule-evaluation approach follows timed activities and updates the
  applicable work and driving measures; this informs the sequence-based
  HOS-rule evaluation specified in §3.5.
- [He et al. (2026), “Time-Dependent Vehicle Routing Problem with
  Simultaneous Pickup-and-Delivery and Time Windows Considering Carbon
  Emission Costs Using an Improved Ant Colony Optimization Algorithm,”
  *Sustainability*, 18(3), 1430](https://doi.org/10.3390/su18031430) combines
  time-dependent traffic, pickup and delivery at customer visits, soft
  windows, and emissions costs. It demonstrates a close combination of
  operational and environmental objectives in a time-dependent setting; its
  travel and service model is deterministic and single-horizon.
- [Gamst and Pisinger (2024), “Decision support for the Technician Routing
  and Scheduling Problem,” *Networks*, 83(1), 169–196](https://doi.org/10.1002/net.22188)
  addresses technician assignments, skills, working hours, and task routing.
  It also discusses benchmark data containing tools and tool depots, while
  its reported experiments set those tool requirements aside. It is relevant
  to the workforce and service layer, with a different focus from the
  scenario-based integrated model here.

### 6.6 Synthesis and positioning

The screened literature establishes prior models for each major component
and for several substantial combinations:

- FIFO time-dependent road travel and path flexibility;
- stochastic, spatially and temporally correlated traffic with scenario-based
  route evaluation;
- pickup-and-delivery under stochastic, time-dependent travel;
- multiobjective robust or environmental routing under time-dependent
  conditions;
- multi-day delivery/installation plans and depot or vehicle continuity;
- intermediate replenishment and package transfers, plus multi-tour vehicle
  reuse within a day;
- multi-day vehicle/driver coordination and hours-of-service rules; and
- technician routing with time windows, skills, teams, tools, or parts
  inventories.

The closest integrated predecessors are Graf (2020), for multi-period delivery
and installation; Lucci et al. (2026), for multi-day pickup-and-delivery with
crew scheduling; Kubek (2025) and Wang et al. (2023), for urban or pickup-and-
delivery routing under stochastic time-dependent travel; and Mathlouthi et al.
(2021), for technician tasks with carried resources. The reviewed work also
shows that multiobjective path, emissions, fleet, and reliability criteria
have substantial prior art.

The reviewed sources establish substantial prior art across the dimensions
of this specification, often in overlapping combinations. The research
opportunity suggested by this synthesis is to integrate a user-defined
weighted bank of coherent traffic scenarios; FIFO travel functions on a
directed city road graph; joint service-order, road-path, and timing decisions
shared across scenarios; daily depot-return duties linked by overnight depot
continuity and package carryover; multiple depot-to-depot tours per vehicle-day,
depot replenishment, and inter-vehicle package transfers with facility storage;
pickup/delivery requests plus intervention visits with abstract tool-capacity
reservations, task precedence and co-location links, and qualified staff teams;
crew assignments, driver roles, and work rules; and user-selected minimized
objectives built from scenario statistics, event exposure, and asymmetric
violation penalties. The potential contribution lies in this integrated
formulation and its application-facing ability to compose scenario, service,
resource, personnel, and objective models. Establishing publication priority
requires independent screening of full records from bibliographic databases
such as Scopus, Web of Science, and Compendex, with a reproducible
deduplication and selection log.


