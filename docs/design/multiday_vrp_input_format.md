# Multi-day VRP input data format

## 1. Purpose and scope

This document defines a file format for one instance of the multi-day vehicle
routing problem in [`multiday_vrp.md`](multiday_vrp.md). It describes how to
store the network, requests, vehicles, personnel, rules, events, objectives,
and the references that bind them together.

Traffic scenarios use the standalone `ScenarioBank` format defined in
[`multiday_vrp_scenario_generation.md`](multiday_vrp_scenario_generation.md).
An instance manifest points to that bank. This document relies on the bank's
manifest, time grid, scenario probabilities, and travel-time files as defined
there.

The format is intended to be readable and editable by people while remaining
straightforward to validate and load. Version 1 uses TOML for instance-level
settings and objective declarations, and UTF-8 CSV for entity catalogs and
relationships. Application-provided calculations, such as custom cost,
penalty, and hours-of-service rules, are identified by stable names and
resolved through the registry supplied by the application that loads the
instance. The bundle contains fixed instance data; route plans and their
simulated outcomes are search solutions and are stored separately.

Search-control settings are supplied separately through
`MultiobjectiveVNSConfig` when the application constructs the generic search
engine. The instance bundle holds fixed application data; the run configuration
holds search settings such as `composition_selection_probability`, and the
event log records their values. The generic algorithm design defines the
selection rule and configuration default.

## 2. Bundle layout

An instance is a directory rooted at `instance.toml`:

```text
geneva-instance/
    instance.toml
    objectives.toml
    application-data.toml               # conditional, application-defined
    nodes.csv
    node-coordinates.csv                # optional, planar spatial support
    arcs.csv
    depots.csv
    vehicles.csv
    vehicle-overnight-depots.csv
    packages.csv
    package-time-windows.csv
    vehicle-package-compatibility.csv
    interventions.csv
    intervention-time-windows.csv
    personnel.csv
    driving-skills.csv                   # conditional
    intervention-qualifications.csv      # conditional
    vehicle-driving-skills.csv
    personnel-driving-skills.csv
    personnel-intervention-qualifications.csv
    vehicle-availability.csv
    personnel-availability.csv
    transfer-facilities.csv             # optional
    package-transfer-facilities.csv     # optional
    tools.csv                           # optional
    intervention-tools.csv              # optional
    intervention-qualifications-required.csv  # optional
    precedence.csv                      # optional
    co-location.csv                     # optional
    event-types.csv                     # optional
    events.csv                          # optional
    objective-event-types.csv           # optional
    objective-omission-penalties.csv    # conditional
    daily-work-limits.csv               # conditional
    daily-driving-limits.csv             # conditional
    person-rule-limits.csv               # conditional
    continuous-driving-limits.csv        # conditional
    hos-configuration.toml              # conditional, application-defined
```

The manifest supplies the paths for the files in use. Every path is relative
to the directory containing `instance.toml`; `..` may be used for a separately
stored scenario bank. Relative paths keep the bundle relocatable when its
directory layout is preserved. The manifest records the bank path and uses
the bank's own directory structure and file definitions.

An absent optional table represents an empty set or relation. A conditional
table is required when its corresponding rule or feature is selected. Every
CSV file that is present has a header row, including a file with zero data
rows.

## 3. Shared encoding rules

### 3.1 Identifiers and CSV tables

Identifiers are UTF-8 text values. They are case-sensitive, nonempty, and
preserved exactly as written. Leading and trailing whitespace is invalid.
Identifiers are unique within each entity catalog; references resolve by exact
text equality. CSV fields are never inferred as numbers when they represent
identifiers. Each relationship row refers to existing catalog entries, and a
repeated relationship is an input error.

CSV files use UTF-8, a comma delimiter, one header row, and the exact column
names and order specified below. Fields containing commas or quotes use
standard CSV quoting. Row order defines the deterministic order of each
catalog where the model uses entity order for reproducible tie handling or
diagnostics. Rows in relationship tables have set semantics unless a section
states otherwise.

Integer fields use base-10 integer notation. Real-valued fields use finite
decimal notation with `.` as the decimal separator; integral spellings such
as `0` are also accepted for real-valued fields. The loader uses each field's
declared type to convert real-valued fields to Python `float` values using
IEEE 754 binary64 arithmetic. The literals `true` and `false` are reserved
for Boolean fields. `NaN`, positive or negative infinity, values that overflow
during conversion, and locale-specific number formatting are invalid.
Constraint tolerances, probability normalization, objective ordering, and
overflow handling follow the numeric contract in
[`multiday_vrp.md`](multiday_vrp.md) §1.
Nonnegative and positive ranges follow the corresponding requirements in
`multiday_vrp.md`.

In CSV, Boolean fields use exactly `true` or `false`. An empty field is
permitted only where this document explicitly marks the field optional.
Unknown columns and duplicate header names are input errors in version 1.
Version-1 TOML objects accept only the keys defined for their object, except
for application-defined `parameters` and configuration files whose schemas are
owned by their registered components.

### 3.2 Dates, timestamps, and durations

The referenced `ScenarioBank` is authoritative for the IANA time zone,
planning dates, day indices, time grid, and time unit. Scenario generation
defaults to `Europe/Zurich`, the Geneva time zone; a bank generated for another
region records its selected IANA zone. Every absolute timestamp in the bank
and this instance bundle uses local civil time in that same zone and includes
an explicit numeric UTC offset, following §2.2 of the
[scenario-generation document](multiday_vrp_scenario_generation.md). The
timestamp expresses local civil time; its offset identifies the instant and
must agree with the bank's IANA zone at that timestamp's own date. Seasonal
offset changes are expected, so timestamps from different dates can carry
different numeric offsets while belonging to the same time zone.

Absolute timestamps are used for time windows and availability intervals. A
timestamp is a single unambiguous instant; for example, the two occurrences of
02:30 during the autumn clock change carry different offsets. Durations,
travel times, and continuous-time limits are finite decimal numbers in the
scenario bank's declared time unit. The instance's duration values and the
bank's `time_unit` must agree. For numeric plan and evaluator values, each
absolute timestamp is converted to elapsed units from the first planning
midnight, using the time-axis rule in `multiday_vrp.md` §1.1. Historical-to-
target profile alignment uses local clock time; offsets distinguish repeated
autumn occurrences according to the rule in the scenario-generation document.

`day_index` is a one-based integer referring to the planning-date order in the
ScenarioBank `target-grid.csv`. An interval stated to be half-open has the
form `[start_time, end_time)`; a time window is a closed interval
`[start_time, end_time]`, as specified in `multiday_vrp.md`.

### 3.3 Missing values and defaults

CSV fields are required unless explicitly declared optional. Encode zero as
`0`, Boolean values as `true` or `false`, and an empty relationship as an
absent row or an omitted optional file. For conditional values, the format
states whether the value is omitted, blank, or represented by an absent
relationship row.

The file format records defaults from `multiday_vrp.md` explicitly wherever a
field can be written directly. For example, write `max_tours_per_day = 1`
when a vehicle uses the default. This produces a fully specified loaded
instance and keeps the resulting data independent of loader-side assumptions.

### 3.4 Feature activation and inactive encodings

The formal activation criteria are defined in
[`multiday_vrp.md` §1.9](multiday_vrp.md#19-supported-feature-catalog). This
section maps those criteria to the input files. An **active encoding** supplies
the data that make the feature active under §1.9. An **inactive encoding** is
the neutral instance data for that feature. The format names a default where
the problem definition or input contract specifies one. For other fields, the
instance author supplies explicit active or neutral values as required by
the schema. The active-encoding column describes the data that activate a
feature; a value in that column is a default only when the inactive/default
column labels it as one. Real-valued activation comparisons use the numeric
tolerance defined in `multiday_vrp.md` §1. A travel-profile or scenario range
activates its feature when its spread exceeds the tolerance evaluated at the
range endpoints; a positive quantity activates its feature when
`value > tol(value, 0)`. Availability and window coverage treats gaps within
tolerance as covered. Capacity is potentially binding only when the declared
load bound exceeds capacity by more than `tol(capacity, load_bound)`. Integer,
Boolean, identifier, and set-membership criteria use their exact values.
Capacities, calendars, windows, and objective parameters are instance data.

An omitted optional file represents the empty set or relation stated in §2,
except for the vehicle and personnel availability files. Omitting either
availability file gives every corresponding entity availability throughout
the planning horizon. When an availability file is supplied, an entity with
no rows has an empty availability calendar. A supplied header-only file
therefore represents empty calendars for all entities. Compatibility uses a
complete relation, with one explicit row for every vehicle-package pair.

| Feature from `multiday_vrp.md` §1.9 | Active encoding | Inactive encoding or specified default |
|---|---|---|
| Directed route paths | Always represented by directed `arcs.csv` entries matching the ScenarioBank arc catalog; each route leg follows a path in that graph. | Core feature in every instance. A direct-matrix instance supplies its direct origin-destination arcs. |
| Planar node coordinates | Declare `node_coordinates` in `[files]`, provide both coordinate metadata fields, and include one row for every depot and route service location in `node-coordinates.csv`. Transit-only nodes may also be listed. | Omit `node_coordinates`, `node_coordinate_system_id`, and `node_coordinate_unit`; the default catalog uses the network and other supplied instance data. |
| Within-day time-dependent travel | At least one scenario, arc, and day has a travel-time range across its departure-grid boundaries greater than the numeric comparison tolerance. | Set the travel-time range within tolerance at all departure-grid boundaries for each scenario, arc, and day. Values may still differ across days or scenarios. |
| Scenario uncertainty | At least two positive-probability scenarios have travel-time values whose range exceeds the numeric comparison tolerance at an aligned arc, day, and departure-grid boundary. | Use one scenario as the standard deterministic encoding. Multiple scenarios whose travel-time values differ by no more than the comparison tolerance also represent deterministic travel; their declared aggregation remains active when the scenario count exceeds one. |
| Network-event exposure | Define event instances in `event-types.csv` and `events.csv`, then include their types in an `event_count` or `maximum_event_exposure` objective through `objective-event-types.csv`. | Omit event-count objectives or give them no event instances to count. Event data may remain available to describe effects already captured in ScenarioBank travel data. |
| Multi-day planning | Supply more than one planning date in the ScenarioBank target grid. | Supply one planning date. The instance declares its planning dates explicitly. |
| Variable active-vehicle count | Omit `exact_active_vehicle_count`; the plan may use any number of vehicles allowed by the other requirements. | Set `exact_active_vehicle_count` in `instance.toml` to an integer from `0` through the number of rows in `vehicles.csv`; every feasible plan must use exactly that many distinct vehicles over the horizon. |
| Overnight-depot choice | Give at least one vehicle multiple eligible rows in `vehicle-overnight-depots.csv`. | Give each vehicle exactly one eligible depot row. The relation is always required; no depot is inferred. |
| Multiple tours per day | Set `max_tours_per_day` above `1` for at least one vehicle. | **Version 1 default:** write `max_tours_per_day = 1` for every vehicle. |
| Restricted vehicle availability | Give a vehicle availability intervals that leave a gap larger than the comparison tolerance in the planning horizon. | Omit `vehicle-availability.csv` for full-horizon availability, or write intervals that cover the horizon within tolerance for every vehicle. In a supplied file, a vehicle with no rows has an empty calendar. |
| Potentially binding cargo/tool capacity | Set at least one vehicle's `cargo_capacity` below `W_all` by more than the numeric comparison tolerance defined in §1. | Set every vehicle's `cargo_capacity` to at least `W_all` within the comparison tolerance. The input supplies each capacity explicitly. |
| Potentially binding personnel seat capacity | With interventions present, set at least one vehicle's `seat_capacity` below the number of people listed in `personnel.csv`. | Omit interventions, or set every vehicle's seat capacity to at least the number of people in `personnel.csv`. `seat_capacity` includes the driver and is always explicit. |
| Package-vehicle compatibility | Mark at least one pair `compatible = false` in the complete `vehicle-package-compatibility.csv` relation. | Mark every vehicle-package pair `compatible = true`. When packages exist, exactly one row per pair is required. |
| Package transfer operations | Set `max_transfers` above `0` for a package and list its eligible facilities in `package-transfer-facilities.csv`. | **Problem-definition default:** `max_transfers = 0`. The input row also writes both transfer service durations as `0`; omit the transfer-eligibility file when no package permits transfer. |
| Transfer-facility storage | Define a transfer facility that holds initial package inventory or is eligible for a permitted package transfer. | Omit `transfer-facilities.csv` and `package-transfer-facilities.csv` when no transfer facility has either role. A pickup at a defined transfer-facility node creates initial inventory there. |
| Package pickup and delivery | Add one or more requests to `packages.csv`; provide their time windows and compatibility rows as required. | Omit `packages.csv` and its package-specific tables. The package catalog is optional when the instance has no package requests. |
| Positive service durations | Set any applicable package, intervention, or enabled transfer service duration above `tol(duration, 0)`. | Set every applicable service-duration field to zero within the comparison tolerance. Required duration fields remain present for catalog rows. |
| Optional service omission | Set `service_coverage_mode = 'penalized_omission'` and provide one omission amount per objective and required activity in `objective-omission-penalties.csv`. | **Version 1 default:** `service_coverage_mode = 'mandatory'`; omit `objective-omission-penalties.csv`. |
| Restrictive service windows | Give a package activity windows whose union leaves a gap larger than the comparison tolerance, or give an intervention fewer than all days or a window shorter than a full day beyond the tolerance. | For every package activity, provide windows whose union covers the full planning horizon within tolerance. For every intervention, provide a full-day window on every planning day within tolerance. Window tables remain required for defined activities. |
| Interventions | Add one or more interventions to `interventions.csv` and provide their windows and other required relations. | Omit `interventions.csv` and intervention-specific tables. The intervention catalog is optional when there are no interventions. |
| Tool-capacity reservations | Associate an intervention with a tool in `intervention-tools.csv` whose `capacity_demand` exceeds `tol(capacity_demand, 0)` in `tools.csv`. | Omit tool tables when no intervention uses a tool with demand above the comparison tolerance; tolerance-equivalent-to-zero demand does not activate capacity reservations. |
| Additional activity relationships | Add precedence or co-location rows in `precedence.csv` or `co-location.csv`. | Omit both optional files, or provide header-only files. |
| Driving-skill restrictions | Add one or more required skill rows to `vehicle-driving-skills.csv`. | Omit vehicle skill requirements. With no vehicle requirements, every authorized driver is skill-eligible for every vehicle, regardless of any skills listed for personnel. |
| Intervention qualifications or team-size restrictions | Give an intervention a required qualification row, or set `minimum_team_size` above `1`. | **Problem-definition default:** `minimum_team_size = 1`; omit qualification requirements. The value remains explicit in every intervention row. |
| Restricted personnel availability | Give a person availability intervals that leave a gap larger than the comparison tolerance in the planning horizon. | Omit `personnel-availability.csv` for full-horizon availability, or write intervals that cover the horizon within tolerance for every person. In a supplied file, a person with no rows has an empty calendar. |
| Personnel availability differentiation | Give at least two assignment-capable people different effective availability calendars; adjacent intervals within tolerance are treated as continuous. | Give every assignment-capable person the same effective calendar. A shared restriction is represented by `restricted_personnel_availability`. |
| Driver-assignment differentiation | Provide at least one vehicle with two or more eligible drivers and either differing assignment profiles or active staff-depot-continuity/work/driving/rest/HOS rules. | Give each vehicle at most one eligible driver, or use equivalent profiles with `staff_depot_mode = 'available_at_assignment'`, `active_simple_rules = []`, and `hos_regime_id = 'none'`. The loader derives profiles from driver skills, intervention qualifications, availability, and initial depots when continuity is selected. |
| Staff depot continuity | Set `staff_depot_mode = 'depot_continuity'` and provide each person's initial depot. | Inactive selection: `staff_depot_mode = 'available_at_assignment'`; the `initial_depot_id` field is empty for each person. The instance author writes the required mode explicitly. |
| Work, driving, rest, or HOS rules | Select one or more values in `active_simple_rules`, or select a registered HOS regime and supply its configuration. The problem-definition default treatment is `rule_treatment = 'penalty'`; `compliance` selects hard requirements. | **Problem-definition defaults:** `active_simple_rules = []`, `hos_regime_id = 'none'`, and `hos_regime_version = 'none'`. |
| Objective declaration | Always declare at least one objective in `objectives.toml`. | Core feature; every instance declares at least one objective. |
| Multiple objectives | Declare at least two entries in the `objectives` array in `objectives.toml`. | Declare exactly one objective. |
| Scenario aggregation | Supply more than one ScenarioBank scenario and declare an aggregation for each objective. | Use one scenario. `weighted_mean`, `weighted_quantile`, and `maximum` return that scenario's outcome; `weighted_standard_deviation` returns zero. The schema still requires an `aggregation` declaration. |

The feature names in the first column correspond to the `VrpFeatureId` values
and formal criteria in `multiday_vrp.md` §1.9.

The manifest's other required settings, including `rule_treatment`, have no
inactive-feature meaning by themselves. They become operative when their
associated rules or features are present. The loader validates the complete
encoding and derives the feature profile from the loaded values, following
§1.9. Section 4 onward remains authoritative for required files and fields,
including those needed when a feature is inactive.

After validation, the loader returns an immutable `LoadedVrpInstance` with the
public record shape defined in §3.5. Application binding and generic-search
assembly are defined in §3.6.

### 3.5 Public shape of `LoadedVrpInstance`

`LoadedVrpInstance` is the immutable, normalized result returned by the
instance loader. Its public fields are:

```text
LoadedVrpInstance(
    format_version: int,
    instance_id: str,
    scenario_bank: LoadedScenarioBank,
    settings: VrpInstanceSettings,
    network_data: VrpNetworkData,
    spatial_data: VrpSpatialData,
    fleet_data: VrpFleetData,
    package_data: VrpPackageData,
    pickup_symmetry_groups: tuple[PickupSymmetryGroup, ...],
    intervention_data: VrpInterventionData,
    personnel_data: VrpPersonnelData,
    activity_relations: VrpActivityRelationData,
    work_rule_data: VrpWorkRuleData,
    event_data: VrpEventData,
    objective_data: VrpObjectiveData,
    time_window_violation_objective_id: str | None,
    application_data: FrozenTomlTable | None,
    hos_configuration: FrozenTomlTable | None,
    hos_rule_definitions: tuple[HosRuleDefinition, ...],
    feature_profile: VrpFeatureProfile,
)

PickupSymmetryGroup(
    group_id: str,
    package_ids: tuple[str, ...],
)

VrpSpatialData(
    coordinate_system_id: str | None,
    coordinate_unit: str | None,
    node_coordinates: tuple[NodeCoordinateRecord, ...],
)

NodeCoordinateRecord(
    node_id: str,
    x: float,
    y: float,
)
```

The nested data records and the pickup-symmetry record have these public
fields:

| Record | Fields |
|---|---|
| `VrpInstanceSettings` | `load_unit`, `service_coverage_mode`, `staff_depot_mode`, `exact_active_vehicle_count`, `rule_treatment`, `active_simple_rules`, `hos_regime_id`, `hos_regime_version` |
| `VrpNetworkData` | `nodes`, `arcs`, `depots`, `transfer_facilities` |
| `VrpSpatialData` | `coordinate_system_id`, `coordinate_unit`, `node_coordinates` |
| `NodeCoordinateRecord` | `node_id`, `x`, `y` |
| `VrpFleetData` | `vehicles`, `vehicle_overnight_depots`, `vehicle_driving_skills`, `vehicle_availability` |
| `VrpPackageData` | `packages`, `package_time_windows`, `vehicle_package_compatibility`, `package_transfer_facilities` |
| `PickupSymmetryGroup` | `group_id`, `package_ids` |
| `VrpInterventionData` | `interventions`, `intervention_time_windows`, `tools`, `intervention_tools`, `intervention_qualifications_required` |
| `VrpPersonnelData` | `personnel`, `driving_skills`, `intervention_qualifications`, `personnel_driving_skills`, `personnel_intervention_qualifications`, `personnel_availability` |
| `VrpActivityRelationData` | `precedence`, `co_location` |
| `VrpWorkRuleData` | `daily_work_limits`, `daily_driving_limits`, `person_rule_limits`, `continuous_driving_limits` |
| `VrpEventData` | `event_types`, `events`, `objective_event_types` |
| `VrpObjectiveData` | `objectives`, `penalty_functions`, `objective_omission_penalties` |

`time_window_violation_objective_id` is derived from the validated objective
bindings. It is the unique objective ID bound to both `early_window` and
`late_window`; it is `None` when the instance has no service-window
categories. The loader validates this relationship before returning the
instance, and the default Operator catalog uses this field for time-window-
focused Operators.

`pickup_symmetry_groups` contains the optional pickup-order equivalence groups
declared in `instance.toml` §4. An omitted declaration produces an empty
tuple. Each group has a unique `group_id` and at least two distinct package
IDs. A package ID belongs to at most one group. The loader verifies that each
member package exists and that group members have exactly the same pickup
node, pickup service duration, and pickup-window alternatives. Window
alternatives compare by their ordered `(window_index, start_time, end_time)`
values after timestamp normalization; numeric values compare exactly. A group
declaration carries the application guarantee defined in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md) §4.6.
The declaration defines pickup-order equivalence for solution identity.
Package IDs remain attached to their pickup and delivery activities, and
vehicle assignments and delivery order remain explicit plan decisions.

`VrpInstanceSettings` has this value contract:

```text
VrpInstanceSettings(
    load_unit: str,
    service_coverage_mode: str,
    staff_depot_mode: str,
    exact_active_vehicle_count: int | None,
    rule_treatment: str,
    active_simple_rules: frozenset[str],
    hos_regime_id: str,
    hos_regime_version: str,
)
```

The accepted strings and their meanings are defined in §4.
`exact_active_vehicle_count` is `None` when the manifest omits the field;
otherwise it contains the validated exact count.

Every field in `VrpNetworkData` through `VrpObjectiveData` is a tuple of
immutable records. For CSV-backed records, each record's public field names
are the exact column names specified in §§5–10, and its values use the
validated Python types specified in §3.1 and the relevant table definition.
For example, `instance.network_data.arcs[0].tail_node_id` is the parsed
`tail_node_id` from `arcs.csv`. A missing optional table produces an empty
tuple. Tuple order follows the source table's row order; objective tuples
follow the array order in `objectives.toml`, which defines objective-vector
order.

`spatial_data` is an immutable `VrpSpatialData` record. When
`[files].node_coordinates` is absent, it contains
`coordinate_system_id=None`, `coordinate_unit=None`, and an empty
`node_coordinates` tuple. When the file is present, both metadata fields are
nonempty strings copied from `node_coordinate_system_id` and
`node_coordinate_unit`, and `node_coordinates` follows the CSV row order. Each
`NodeCoordinateRecord` contains a validated `node_id` and finite `x` and `y`
floats. The complete-coverage rule activates `planar_node_coordinates` in the
instance's `feature_profile`; the feature meaning is defined authoritatively
in [`multiday_vrp.md`](multiday_vrp.md) §1.9.

The `objectives` tuple in `VrpObjectiveData` contains immutable
`ObjectiveSpec` records with these fields: `objective_id`, `base_measure_id`,
`base_measure_parameters`, `aggregation`,
`penalty_aggregation`, and `penalty_scales`. `base_measure_parameters` and
`penalty_scales` are read-only mappings. `aggregation` and
`penalty_aggregation` are immutable records with an `id` field and the
declared parameter fields. Each `penalty_functions` element has the fields
`objective_id`, `violation_category`, `rule_id`, `function_id`, and
`parameters`. Each `objective_omission_penalties` element has the fields
`objective_id`, `activity_kind`, `activity_id`, and `amount`. The exact
validation and meanings of these fields are specified in §10.

An omitted availability file is expanded during loading into one full-horizon
availability interval for each corresponding vehicle or person. A supplied
header-only file yields empty availability tuples. This preserves the
distinction in §6–7 while presenting both cases through the same normalized
record shape.

`LoadedScenarioBank` is also immutable and has this public shape:

```text
LoadedScenarioBank(
    format_version: int,
    arc_ids: tuple[str, ...],
    time_zone: str,
    time_unit: str,
    planning_dates: tuple[date, ...],
    day_boundaries: tuple[tuple[float, ...], ...],
    scenarios: tuple[ScenarioRecord, ...],
    travel_times: Mapping[tuple[str, str, int, int], float],
    post_horizon_rule_id: str,
    post_horizon_rule_configuration_id: str,
)
```

`format_version` is `1`, and `time_zone` is the bank's IANA zone. `arc_ids`,
`planning_dates`, and `scenarios` retain their source order. Each inner
`day_boundaries` tuple corresponds to the matching `planning_dates` entry and
contains the elapsed-time coordinate for that day's boundaries, measured in
`time_unit` from the first planning midnight. Each `ScenarioRecord` has
`scenario_id: str` and normalized `probability: float` fields. The keys in
`travel_times` are `(scenario_id, arc_id, day_index, boundary_index)`;
values are finite travel-time floats. The mapping is read-only. The grid,
scenario, and travel-time meanings and their source files are defined in
[`multiday_vrp_scenario_generation.md`](multiday_vrp_scenario_generation.md)
§2.3. The VRP loader verifies that `arc_ids` equals the arc catalog in
`network_data.arcs`, that the bank and instance use the same time unit and
planning calendar, and that every table reference is valid.
The post-horizon identifiers select the registered rule and configuration;
the application binds the corresponding callable separately.

Every TOML table retained in `LoadedVrpInstance`, including objective
parameters, `application_data`, and `hos_configuration`, is a recursively
immutable `FrozenTomlTable`; TOML arrays are tuples. `application_data` and
`hos_configuration` are `None` when their manifest files are absent.
`hos_rule_definitions` is empty when `hos_regime_id = 'none'`; otherwise it
is the validated output of `HosConfigurationValidator`, as specified in
§12.2–12.3.
`FrozenTomlTable` is a read-only string-keyed mapping whose values are TOML
scalar values, tuples of TOML values, or nested `FrozenTomlTable` values.
Decimal values use the Python `float` contract in §3.1. The matching
application registrations validate extension-specific keys and values before
the loader returns, and bind their calculations to these parsed values
afterward.

The `feature_profile` field has the `VrpFeatureProfile` shape defined in
[`multiday_vrp.md`](multiday_vrp.md) §1.9. Application identity and callback
binding are specified in §3.6.

### 3.6 Application assembly for the generic search

`VrpApplication` bundles the validated instance with the application callbacks
and feature-selected Operator registrations required by
`MultiobjectiveVNS`. The public assembly functions are:

```text
load_vrp_application(
    path: str | Path,
    *,
    problem_instance_id: ProblemInstanceId | None = None,
    extension_registry: VrpExtensionRegistry | None = None,
    post_horizon_rules: Mapping[tuple[str, str], PostHorizonRule] | None = None,
    additive_path_costs: Mapping[ObjectiveId, AdditivePathCostRegistration]
    | None = None,
    settings: VrpOperatorCatalogSettings = VrpOperatorCatalogSettings(),
    operator_groups: tuple[OperatorGroup, ...] = (),
) -> VrpApplication

build_vrp_application(
    instance: LoadedVrpInstance,
    *,
    problem_instance_id: ProblemInstanceId | None = None,
    extension_registry: VrpExtensionRegistry | None = None,
    post_horizon_rules: Mapping[tuple[str, str], PostHorizonRule] | None = None,
    additive_path_costs: Mapping[ObjectiveId, AdditivePathCostRegistration]
    | None = None,
    settings: VrpOperatorCatalogSettings = VrpOperatorCatalogSettings(),
    operator_groups: tuple[OperatorGroup, ...] = (),
) -> VrpApplication
```

`load_vrp_application` loads the manifest and assembles the application in one
step. When no extension registry is supplied, it uses the empty built-in
registry. An application with custom declarations supplies the same registry
to loading and evaluation. `build_vrp_application` accepts an already loaded
instance and the registry used to validate its declarations.

`VrpApplication` has these immutable public fields:

| Field | Value |
|---|---|
| `instance` | The `LoadedVrpInstance` supplied by or returned from loading. |
| `problem_instance_id` | The supplied ID, or `instance.instance_id` when omitted. |
| `solution_codec` | The built-in `VrpSolutionCodec`. |
| `solution_identity` | The built-in `VrpSolutionIdentity`, bound to the instance's pickup-symmetry groups. |
| `objective_declaration` | The ordered declaration built from `instance.objective_data`. |
| `application_evaluator` | The evaluator bound to the instance, extension registry, and post-horizon rules. |
| `operators` | The feature-selected default Operator registrations. |
| `pre_registered_compositions` | The feature-selected standalone composite Operator registrations from Operator catalog §1.3. |
| `operator_groups` | Validated soft composition groups over ordinary and pre-registered composite Operators. |

The caller passes these fields to the corresponding
`MultiobjectiveVNS` constructor arguments, including both `operators` and
`pre_registered_compositions`. The caller remains responsible for
constructing at least one valid, feasible initial `VrpSolution` and supplying
it to `run(initial_solutions=...)`. Catalog settings and feature gates are
defined in [`multiday_vrp_operators.md`](multiday_vrp_operators.md) §§1–2;
the version-one catalog provides default registrations for every specification
listed there, and catalog assembly fails explicitly if a future specification
has no corresponding Operator builder.

## 4. Instance manifest

`instance.toml` identifies the bundle, selects global modes, points to the
ScenarioBank and objective declarations, and lists data files.

```toml
format_version = 1
instance_id = 'geneva-weekday-service'
scenario_bank_file = '../traffic/scenario-bank/scenario-bank.toml'
# application_data_file = 'application-data.toml'
# node_coordinate_system_id = 'EPSG:2056'
# node_coordinate_unit = 'm'
load_unit = 'kg'
service_coverage_mode = 'mandatory'
staff_depot_mode = 'available_at_assignment'
# exact_active_vehicle_count = 3  # omit to let the plan choose the count
rule_treatment = 'penalty'
active_simple_rules = []
hos_regime_id = 'none'
hos_regime_version = 'none'
# hos_configuration_file = 'hos-configuration.toml'

[[pickup_symmetry_groups]]
# The application declares the swap-invariance guarantee defined in §3.5.
group_id = 'same-depot-loading'
package_ids = ['package-01', 'package-02']

[files]
nodes = 'nodes.csv'
# node_coordinates = 'node-coordinates.csv'  # optional spatial search support
arcs = 'arcs.csv'
depots = 'depots.csv'
vehicles = 'vehicles.csv'
vehicle_overnight_depots = 'vehicle-overnight-depots.csv'
packages = 'packages.csv'
package_time_windows = 'package-time-windows.csv'
vehicle_package_compatibility = 'vehicle-package-compatibility.csv'
interventions = 'interventions.csv'
intervention_time_windows = 'intervention-time-windows.csv'
personnel = 'personnel.csv'
driving_skills = 'driving-skills.csv'
intervention_qualifications = 'intervention-qualifications.csv'
vehicle_driving_skills = 'vehicle-driving-skills.csv'
personnel_driving_skills = 'personnel-driving-skills.csv'
personnel_intervention_qualifications = 'personnel-intervention-qualifications.csv'
vehicle_availability = 'vehicle-availability.csv'
personnel_availability = 'personnel-availability.csv'
objectives = 'objectives.toml'
intervention_qualifications_required = 'intervention-qualifications-required.csv'
objective_event_types = 'objective-event-types.csv'
```

`format_version`, `instance_id`, `scenario_bank_file`,
`load_unit`, `service_coverage_mode`, `staff_depot_mode`, `rule_treatment`,
`active_simple_rules`, `hos_regime_id`, `hos_regime_version`, and
`[files].objectives` are required. The table paths shown in `[files]` are
required when the corresponding entity sets or relations are nonempty;
optional tables may be omitted from `[files]` to represent an empty set.

The recognized `[files]` keys and their conditions are:

| Key | File | Condition |
|---|---|---|
| `objectives` | `objectives.toml` | Required |
| `nodes` | `nodes.csv` | Required |
| `node_coordinates` | `node-coordinates.csv` | Optional; when supplied, it must cover every depot and route service location |
| `arcs` | `arcs.csv` | Required |
| `depots` | `depots.csv` | Required |
| `transfer_facilities` | `transfer-facilities.csv` | Required when any transfer facility is defined |
| `vehicles` | `vehicles.csv` | Required |
| `vehicle_overnight_depots` | `vehicle-overnight-depots.csv` | Required |
| `vehicle_driving_skills` | `vehicle-driving-skills.csv` | Required when a vehicle has required driving skills |
| `vehicle_availability` | `vehicle-availability.csv` | Optional; omitted means full-horizon availability |
| `packages` | `packages.csv` | Optional; omitted when there are no package requests |
| `package_time_windows` | `package-time-windows.csv` | Required when packages exist |
| `vehicle_package_compatibility` | `vehicle-package-compatibility.csv` | Required when packages exist |
| `package_transfer_facilities` | `package-transfer-facilities.csv` | Required when a package permits transfers |
| `interventions` | `interventions.csv` | Optional; omitted when there are no interventions |
| `intervention_time_windows` | `intervention-time-windows.csv` | Required when interventions exist |
| `tools` | `tools.csv` | Required when a tool is used by an intervention |
| `intervention_tools` | `intervention-tools.csv` | Required when an intervention requires a tool |
| `driving_skills` | `driving-skills.csv` | Required when a vehicle or person references a driving skill |
| `intervention_qualifications` | `intervention-qualifications.csv` | Required when a person or intervention references a qualification |
| `intervention_qualifications_required` | `intervention-qualifications-required.csv` | Required when interventions require qualifications |
| `personnel` | `personnel.csv` | Required |
| `personnel_driving_skills` | `personnel-driving-skills.csv` | Required when a person has driving skills |
| `personnel_intervention_qualifications` | `personnel-intervention-qualifications.csv` | Required when a person has intervention qualifications |
| `personnel_availability` | `personnel-availability.csv` | Optional; omitted means full-horizon availability |
| `precedence` | `precedence.csv` | Optional; omitted means no additional precedence pairs |
| `co_location` | `co-location.csv` | Optional; omitted means no co-location pairs |
| `event_types` | `event-types.csv` | Required when event types are defined |
| `events` | `events.csv` | Optional; omitted means no event instances |
| `objective_event_types` | `objective-event-types.csv` | Required for event-count or maximum-event-exposure objectives |
| `objective_omission_penalties` | `objective-omission-penalties.csv` | Required for `penalized_omission` coverage |
| `daily_work_limits` | `daily-work-limits.csv` | Required when `daily_work` is active |
| `daily_driving_limits` | `daily-driving-limits.csv` | Required when `daily_driving` is active |
| `person_rule_limits` | `person-rule-limits.csv` | Required when a selected simple rule uses break or rest limits |
| `continuous_driving_limits` | `continuous-driving-limits.csv` | Required when `continuous_driving` is active |

`hos_configuration_file` is a top-level manifest field required when
`hos_regime_id` names a rule set other than `none`.
`pickup_symmetry_groups` is an optional top-level array of tables. Each table
has `group_id: nonempty string` and `package_ids: array of at least two
distinct package ID strings`. Group IDs are unique, and membership is disjoint
across groups. The loader applies the static member checks and the application
guarantee specified in §3.5. Group declarations affect solution identity and
are part of the instance modeling configuration; changing them requires a new
`instance_id`.
`exact_active_vehicle_count` is an optional top-level manifest field. When
present, it is a built-in integer excluding Boolean values, in the range
`0 <= exact_active_vehicle_count <= number of vehicle rows`; it requires
exactly that many distinct vehicles to have at least one active vehicle-day
route in a feasible plan. When omitted, the number of active vehicles remains
a plan decision. The loader derives `variable_active_vehicle_count` from this
field as defined in `multiday_vrp.md` §1.9. An explicit zero is valid for an
instance whose feasible plans use no vehicles.
`application_data_file` is a top-level field required when an
application-registered measure, aggregation, penalty function, penalty
aggregator, or rule needs additional instance data. The loading application
validates it with the schema declared by the matching extension registration.

`node_coordinate_system_id` and `node_coordinate_unit` are optional top-level
fields used with `[files].node_coordinates`. Both are required when that file
is supplied. They are omitted when that file is absent; either metadata field
without the coordinate file is an input error. The system identifier
names the planar coordinate system used by every row, such as an EPSG
projected-coordinate identifier or an application-defined local Cartesian
system. The unit is a descriptive label shared by the x and y values, such as
`'m'` or `'solomon_units'`. Coordinate data and metadata are part of the
instance configuration; changing them requires a new `instance_id` because
they change the feature profile and the feature-selected Operator catalog.

`instance_id` is a stable application-level identifier for the data and
modeling configuration. The application changes it when any input or
modeling declaration that affects feasibility, simulation, objective
evaluation, solution identity, the feature profile, or the default Operator
catalog changes. The ScenarioBank has its own identity and metadata.

`load_unit` is a descriptive label such as `'kg'`, `'liters'`, or
`'capacity_units'`. The model uses one scalar unit for package loads, tool
capacity demands, vehicle capacities, and transfer-facility storage
capacities.

`service_coverage_mode` is `'mandatory'` or `'penalized_omission'`.
`staff_depot_mode` is `'available_at_assignment'` or `'depot_continuity'`.
`rule_treatment` is `'penalty'` or `'compliance'` and applies to the selected
simple rules and HOS regime. `active_simple_rules` is an array containing
unique values from `'daily_work'`, `'daily_driving'`, `'continuous_driving'`, and
`'minimum_rest'`; an empty array selects none. The HOS ID `'none'` selects no
HOS rule set. A non-`none` HOS ID resolves to an application-registered rule
set with the declared version and configuration format.
For `hos_regime_id = 'none'`, `hos_regime_version` is `'none'`; an active HOS
regime has a nonempty version and its `hos_configuration_file`.

The manifest and every referenced table are validated before search. The
ScenarioBank supplies the instance's time zone, time unit, planning dates,
day indices, time grid, scenarios, and probabilities. Timestamp offsets,
duration units, and day indices in the remaining instance files are validated
against that bank. The `arcs.csv` identifiers must equal the bank's arc
catalog exactly, and every timestamp must lie within the bank's planning
horizon where the corresponding model field requires it.

## 5. Network and facility tables

### 5.1 `nodes.csv`

Required columns: `node_id`.

One row identifies each road node, including service locations and transit
nodes. The file contains at least one row.

### 5.1.1 Optional `node-coordinates.csv`

Required columns: `node_id,x,y`.

Each row gives one road node a coordinate pair in the planar coordinate
system named by `node_coordinate_system_id`. `node_id` must occur in
`nodes.csv`, and each node occurs at most once. The loader requires a row for
each node used by a depot, package pickup or delivery, intervention, or
transfer facility. Coordinates for transit-only nodes are optional. Multiple
node IDs may share the same coordinate pair to represent co-located road
nodes.

`x` and `y` are finite decimal values converted to Python `float` using the
binary64 rules in §3.1. They use the common `node_coordinate_unit`. Coordinates
must describe positions in one planar Cartesian system; geographic longitude
and latitude are projected to that system by the application before the CSV
is written. The loader uses them to derive the `planar_node_coordinates`
feature and to expose spatial information to feature-selected Operators.
Coordinates contribute geometric information to those Operators. Route
adjacency, path travel values, feasibility, and objective values continue to
use the network, ScenarioBank, and application evaluation contracts.

### 5.2 `arcs.csv`

Required columns: `arc_id,tail_node_id,head_node_id`.

Each row defines one directed road arc. Both endpoint IDs must occur in
`nodes.csv`. Parallel arcs are allowed when they have distinct `arc_id`
values. The arc ID set equals the ScenarioBank's arc catalog. As specified in
`multiday_vrp.md`, every vehicle may traverse every arc; this file defines the
network rather than vehicle-specific road eligibility.

### 5.3 `depots.csv`

Required columns: `depot_id,node_id`.

Each row maps one unique `depot_id` to a `node_id` in `nodes.csv`. Multiple
depot IDs may refer to the same node; they remain distinct for vehicle
overnight-depot eligibility, initial-depot selection, and overnight continuity.
Route visits, tour boundaries, depot-to-depot segments, and tool loading or
unloading use the mapped physical node, so co-located depot IDs share those
route semantics.
Route paths use the mapped node IDs. This table defines the `DepotIds` set and
`node_of_depot` mapping in `multiday_vrp.md` §1.1. The depot catalog is
nonempty.

### 5.4 `transfer-facilities.csv`

Required columns: `node_id,storage_capacity`.

Each row identifies a transfer facility at the `node_id` itself and gives its
storage capacity in `load_unit`. Thus the facility identifier is exactly the
road node identifier used by `nodes.csv` and by `TransferStep.facility_id`. A
node appears at most once. Each listed facility is available for package
handling throughout the planning horizon. The file may be absent or
header-only.

## 6. Vehicles, packages, and interventions

Service-duration fields in this section are deterministic values shared by all
scenarios. The ScenarioBank represents road-travel uncertainty.

### 6.1 Vehicle tables

`vehicles.csv` has columns:

```csv
vehicle_id,cargo_capacity,seat_capacity,initial_depot_id,max_tours_per_day
van-1,800.0,3,depot-a,1
```

`cargo_capacity` is finite and nonnegative in `load_unit`. `seat_capacity` and
`max_tours_per_day` are positive integers. `initial_depot_id` is the depot at
which the vehicle starts day 1. The vehicle catalog is nonempty. Every vehicle
starts the planning horizon with an empty cargo load. `seat_capacity` includes
the assigned driver.

`vehicle-overnight-depots.csv` has columns `vehicle_id,depot_id`. It contains
the eligible overnight depots for each vehicle. Each vehicle has at least one
row, and its initial depot is included. This relation controls overnight
endpoints; every depot may also be visited during a route.

`vehicle-driving-skills.csv` has columns `vehicle_id,skill_id` and lists the
driving skills required by that vehicle. Each skill ID occurs in
`driving-skills.csv`.

`driving-skills.csv` has one column, `skill_id`, and defines the driving-skill
identifier catalog.

`vehicle-availability.csv` has columns `vehicle_id,start_time,end_time`.
Each row is a half-open availability interval `[start_time, end_time)` for the
vehicle. Intervals satisfy `start_time < end_time` and lie within the planning
horizon. When this file is present, a vehicle with no rows has an empty
availability calendar, so all of its duty time is outside its declared
availability and receives the availability-penalty treatment in
`multiday_vrp.md`. When this file is omitted from the manifest, every vehicle
has full-horizon availability.

### 6.2 Package tables

`packages.csv` has columns:

```csv
package_id,pickup_node_id,delivery_node_id,load,pickup_service_duration,delivery_service_duration,max_transfers,transfer_out_service_duration,transfer_in_service_duration
parcel-1,node-pickup,node-delivery,12.5,5.0,4.0,0,0.0,0.0
```

Pickup and delivery nodes must exist. `load` and service durations are finite
and nonnegative. `max_transfers` is a nonnegative integer. When it is positive,
both transfer service durations are required finite nonnegative durations and
the package must have at least one eligible transfer facility. When it is
zero, the two transfer service duration fields are `0`.

`package-time-windows.csv` has columns
`package_id,activity,window_index,start_time,end_time`. `activity` is `pickup`
or `delivery`. `window_index` is a positive integer unique within a package
and activity. Each package has at least one pickup window and at least one
delivery window. Times form a closed interval within the planning horizon.

`vehicle-package-compatibility.csv` has columns
`vehicle_id,package_id,compatible`. It contains exactly one row for every
vehicle-package pair. `compatible` is a Boolean. This complete relation makes
every compatibility value explicit.

`package-transfer-facilities.csv` has columns `package_id,node_id` and lists
the facilities allowed for each package transfer. Every row refers to a node
in `transfer-facilities.csv`. Packages with a positive `max_transfers` have at
least one row; other packages have none.

When a package's pickup node is a transfer-facility node, the package belongs
to that facility's initial inventory until its pickup service completes. This
initial inventory is derived from `packages.csv`; no separate stock table is
used. A package at any other pickup node has unlimited modeled pickup stock,
as specified in `multiday_vrp.md`.

### 6.3 Tool and intervention tables

`tools.csv` has columns `tool_id,capacity_demand`. Capacity demand is finite
and nonnegative in `load_unit`. A tool type is an abstract capacity reservation
for an intervention. The model supplies its required tool types independently
for each depot-to-depot segment, and loading or unloading takes zero modeled
time; the file therefore records tool types and their capacity demands.

`interventions.csv` has columns:

```csv
intervention_id,node_id,service_duration,minimum_team_size
inspect-1,node-inspect,45.0,2
```

`minimum_team_size` is a positive integer. The intervention catalog may be
empty.

`intervention-time-windows.csv` has columns
`intervention_id,day_index,window_index,start_time,end_time`. It contains one
row for each window alternative on an eligible day. Day indices refer to the
ScenarioBank target-grid order. An intervention is eligible on a day exactly
when it has one or more rows for that day; each interval is closed and lies
within that day's calendar boundaries. `window_index` is a positive integer,
unique within an intervention and day. Every intervention has at least one
eligible day.

`intervention-tools.csv` has columns `intervention_id,tool_id` and lists the
tool types required by an intervention.

`intervention-qualifications-required.csv` has columns
`intervention_id,qualification_id` and lists qualifications required by an
intervention. The file may be absent or header-only. The qualification IDs
must occur in `intervention-qualifications.csv`, the qualification identifier
catalog.

`intervention-qualifications.csv` has one column,
`qualification_id`, with one row per qualification identifier.

## 7. Personnel, qualifications, and availability

`personnel.csv` has columns `person_id,authorized_driver,initial_depot_id`.
`authorized_driver` is a Boolean and defines membership in the driver set.
The file contains at least one authorized driver.
When `staff_depot_mode = 'depot_continuity'`, every person's
`initial_depot_id` identifies the depot where their first assigned duty
starts. Under `'available_at_assignment'`, `initial_depot_id` is empty.

`personnel-driving-skills.csv` has columns `person_id,skill_id` and lists each
person's driving skills. Only authorized drivers need driving skills.
`personnel-intervention-qualifications.csv` has columns
`person_id,qualification_id` and lists each person's intervention
qualifications.

`personnel-availability.csv` has columns `person_id,start_time,end_time`.
Each row is a half-open availability interval within the planning horizon. A
person with no rows in a supplied file has an empty availability calendar, so
all of that person's duty time is outside their declared availability and
receives the availability-penalty treatment in `multiday_vrp.md`. When this
file is omitted from the manifest, every person has full-horizon
availability.

## 8. Precedence, co-location, and events

### 8.1 Activity references

Relationship tables identify a service activity with two fields:
`activity_kind` and `activity_id`. `activity_kind` is one of `pickup`,
`delivery`, or `intervention`. The ID refers to the corresponding package or
intervention. Thus `(pickup, parcel-1)` and `(delivery, parcel-1)` are distinct
activities.

`precedence.csv` has columns
`before_activity_kind,before_activity_id,after_activity_kind,after_activity_id`.
Each row states that the first activity is a prerequisite for the second.
Repeated ordered pairs and self-precedence rows are invalid.

`co-location.csv` has columns
`activity_kind_1,activity_id_1,activity_kind_2,activity_id_2`. Each row is an
unordered pair. To avoid duplicate encodings, the two activity references are
written in ascending lexicographic order by `(activity_kind, activity_id)`.
The two references identify distinct existing activities. Their source nodes
are read from the package or intervention tables. If those nodes differ, the
loader rejects the input when `service_coverage_mode = 'mandatory'`, because
both activities must then be served at their distinct fixed nodes. When
`service_coverage_mode = 'penalized_omission'`, the loader accepts the pair;
the plan may omit at least one member and incur its declared omission penalty.
If both activities are served, their nodes must match as specified in
`multiday_vrp.md` §3.2.

### 8.2 Event tables

`event-types.csv` has columns `event_type_id,location_domain`. The domain is
`node` or `arc` and determines the identifier type used by event instances.

`events.csv` has columns `event_id,event_type_id,location_id`. A node-domain
event refers to `nodes.csv`; an arc-domain event refers to `arcs.csv`. Multiple
events may share a location, and each event instance is counted separately.
These records define event exposure for objectives; the corresponding effect
on travel times is represented in the referenced ScenarioBank. The route-level
counting convention is the one defined in §1.3 of `multiday_vrp.md`.

`objective-event-types.csv` has columns `objective_id,event_type_id`. It
defines the event-type group counted by an event-count or maximum-event-
exposure objective. Several objectives may include the same event type. Each
objective using `event_count` or `maximum_event_exposure` has at least one row;
rows for other objective measures are invalid.

## 9. Simple work and driving rules

The manifest's `active_simple_rules` determines which rule-limit tables are
required. `rule_treatment` applies to each active simple rule and to the
selected HOS regime. The simple rules use the boundary convention in
`multiday_vrp.md`: every person starts the horizon rested, and every
continuous-driving counter starts at zero. The simple-rule files therefore
contain limits and break/rest durations, while pre-horizon history and
terminal-boundary data belong to an application-defined HOS configuration.

`daily-work-limits.csv` has columns `person_id,day_index,max_work_time`. It
contains exactly one row for each person and day when `daily_work` is active.
The limit is finite and nonnegative.

`daily-driving-limits.csv` has columns `driver_id,day_index,max_driving_time`.
It contains exactly one row for each authorized driver and day when
`daily_driving` is active. The limit is finite and nonnegative.

`person-rule-limits.csv` has columns
`person_id,minimum_rest_time,qualifying_break_minimum`. It contains each
person to whom a selected rule applies. `minimum_rest_time` is required when
`minimum_rest` or `continuous_driving` uses inter-duty rest for that person.
`qualifying_break_minimum` is required when `daily_work` or
`continuous_driving` uses qualifying breaks for that person. Required rest and
break durations are strictly positive. A field unused by every active rule is
empty. The row set is the union of people requiring either value: all personnel
for `minimum_rest`, all authorized drivers for `continuous_driving`, all
personnel for `daily_work` break qualification, or the applicable combination
of those rules.

`continuous-driving-limits.csv` has columns
`driver_id,max_continuous_driving_time`. It contains exactly one row per
authorized driver when `continuous_driving` is active. The limit is finite and
nonnegative. The qualifying-break and inter-duty-rest values are read from
`person-rule-limits.csv`.

When no simple rule of a given type is active, its limit file may be omitted.
When a rule is active, its required table has exactly the complete set of
records stated above; missing or additional records are input errors.

## 10. Objectives, penalties, and rule extensions

### 10.1 `objectives.toml`

The objective array order defines the objective-vector order. At least one
objective is required. Every objective is minimized.

```toml
format_version = 1

[[objectives]]
objective_id = 'mean-travel-time'
base_measure_id = 'total_travel_time'
base_measure_parameters = {}
aggregation = { id = 'weighted_mean' }
penalty_aggregation = { id = 'sum' }
penalty_scales = {}

[[objectives]]
objective_id = 'p90-travel-time'
base_measure_id = 'total_travel_time'
base_measure_parameters = {}
aggregation = { id = 'weighted_quantile', q = 0.9 }
penalty_aggregation = { id = 'sum' }
penalty_scales = {}

# Keep service-window penalties in their own objective dimension.
[[objectives]]
objective_id = 'time-window-violation'
base_measure_id = 'zero'
base_measure_parameters = {}
aggregation = { id = 'weighted_mean' }
penalty_aggregation = { id = 'sum' }
penalty_scales = { early_window = 1.0, late_window = 1.0 }

[[penalty_functions]]
objective_id = 'time-window-violation'
violation_category = 'early_window'
rule_id = ''
function_id = 'linear'
parameters = { rate = 1.0 }

[[penalty_functions]]
objective_id = 'time-window-violation'
violation_category = 'late_window'
rule_id = ''
function_id = 'linear'
parameters = { rate = 2.0 }
```

This example assumes that service windows are active and that no other
penalty category is active. If vehicle or personnel availability, duty overlap,
or penalty-treated rules are active, add their bindings to the objective or
objectives that should receive those penalties. The built-in `zero` measure is
constant for every plan and scenario; the dedicated window objective's value
therefore comes from its assigned penalties before its scenario aggregation.

Each `objective_id` is unique. `base_measure_id` names one of these built-in
measures or an application-registered measure:

| ID | Scenario-level measure |
|---|---|
| `zero` | Constant value `0.0` in every scenario; supports an objective made from assigned penalty categories |
| `total_travel_time` | Sum of all traversed arc travel durations |
| `total_waiting_time` | Sum of `pre_service_wait + wait` over every stop in every vehicle-day route; see `multiday_vrp.md` §4.2 for the full rule |
| `event_count` | Total occurrences of the event types assigned to this objective |
| `maximum_event_exposure` | Maximum event count on any one vehicle-day route |
| `total_route_duration` | Sum of active vehicle-day duty durations |
| `maximum_route_duration` | Maximum active vehicle-day duty duration |
| `total_personnel_work_time` | Sum of personnel work time over personnel-days |
| `maximum_personnel_work_time` | Maximum personnel work time over personnel-days |
| `total_driver_work_time` | Sum of driver work time over driver-days |
| `maximum_driver_work_time` | Maximum driver work time over driver-days |
| `vehicles_used` | Number of vehicles used during the horizon |
| `vehicle_days_used` | Number of active vehicle-days |
| `tours_used` | Total tours across active vehicle-days |

These IDs follow the measures in §4 of `multiday_vrp.md`. Application-specific
measures such as fuel or operating cost use IDs registered by the loading
application and their required instance data. `base_measure_parameters` is a
TOML table interpreted by that application's registered measure; built-in
measures use an empty table unless their definition explicitly needs
parameters. The loading application's registry defines the callable interface
and parameter schema for a custom measure, as specified by the extension rules
in §12.

The built-in aggregation IDs are `weighted_mean`, `weighted_quantile`,
`weighted_standard_deviation`, and `maximum`. `q` is required for
`weighted_quantile` and lies in `(0, 1]`; it is omitted for the other
aggregations. `maximum` returns the largest scenario outcome. An application
may register an additional aggregation ID; its configuration is carried in
the `aggregation` table and interpreted by the matching application
implementation. A custom aggregation table has an `id` and may include a
`parameters` TOML table. The loading application's registry defines its
callable interface and parameter schema under §12.

`penalty_aggregation` selects how the per-violation penalty values assigned to
that objective are combined into `P_o[s, x]`. The built-in `sum` applies the
category function to each assigned violation, sums values within each
category, applies that objective's category scale, and sums the scaled
category totals. An application may
register a different penalty aggregation ID; it receives the category-indexed
penalty values and scales and returns one finite nonnegative value, equal to
zero exactly when all included violations are zero. A custom penalty
aggregation table has an `id` and may include a `parameters` TOML table. Its
registration and callable interface are defined by the loading application
under §12.

`objective-event-types.csv` is the authoritative definition of the event-type
group for `event_count` and `maximum_event_exposure` measures. A row associates
one objective with one included event type.

`penalty_functions` is an array of tables. Each row has
`objective_id,violation_category,rule_id,function_id,parameters` and binds a
function for one objective. `rule_id` is the empty string for a
category-level binding. Standard categories are
`early_window`, `late_window`, `vehicle_availability`,
`personnel_availability`, `duty_overlap`, `simple_rule`, and `hos_rule`. A
rule-specific binding for a simple or HOS rule uses the rule's exact ID and
overrides the category-level binding for that rule. Other categories use an
empty `rule_id`. A simple-rule ID is one of the selected `active_simple_rules`
values; an HOS-rule ID is declared by the registered HOS configuration.
Each function receives a violation measure and its TOML `parameters`, and
returns zero for zero violation and a finite positive value for positive
violation. The loading application's registry defines how each function ID
and its parameters bind to an implementation under §12.
Each active category has at least one objective binding. Within any one
objective, a category has exactly one category-level binding, with optional
rule-specific overrides for that objective. A penalty category contributes
only to objectives that have its binding; the evaluator does not copy a
penalty into every objective. The `early_window` and `late_window` categories
are assigned together to exactly one objective, whose `base_measure_id` is
`zero`. That objective is the dedicated time-window-violation objective, and
it is the sole recipient of both categories. An application may assign other
soft-violation categories to that objective as well, for example to combine
service windows with vehicle or personnel availability. It may assign those
other categories to additional objectives when that tradeoff is useful.

The built-in penalty function ID `linear` uses `rate * violation_measure`;
`rate` is a finite positive decimal. The loading application registers
additional function IDs. The tuple
`(objective_id, violation_category, rule_id)` is
unique, and each `objective_id` refers to an objective declared in this file.
A rule-specific row applies only to the matching selected rule and violation
category for that objective. A category is active when the instance can
produce that kind of violation: time-window categories when service activities
exist; vehicle and personnel availability and duty-overlap categories for the
corresponding entities; `simple_rule` when a simple rule is selected with
`rule_treatment = 'penalty'`; and `hos_rule` when an HOS regime is selected
with that treatment.
Within each objective to which a category is assigned, the category has one
category-level function binding and one finite positive scale. Rule-specific
function rows override the category-level function for the named rule and use
the same category scale.

Each objective's `penalty_scales` provides a finite positive multiplier for
each penalty category bound to that objective. A missing scale for a bound
category is an input error. Additional scales are retained in the
configuration and take effect when their categories are bound and apply to
the instance.

When `service_coverage_mode = 'penalized_omission'`,
`objective-omission-penalties.csv` is required with columns
`objective_id,activity_kind,activity_id,amount`. It contains exactly one
finite positive amount for every objective and every required pickup,
delivery, and intervention activity. In `mandatory` mode, the file is
omitted.

For each objective and scenario, the application calculates the declared base
measure, adds only the penalty contribution assigned to that objective and any
omission amounts, then applies the declared weighted statistic as specified
in `multiday_vrp.md` §1.8.

### 10.2 Application-defined HOS data

When `hos_regime_id` has a value other than `none`, the application sets
`hos_configuration_file` in `instance.toml` to a TOML file interpreted by the
application's registered HOS rule set. That file contains the regime's
version-specific limits, periods, break and reset rules, pre-horizon history,
terminal boundary convention, and required rule data described in
`multiday_vrp.md` §1.7.
Rule-specific penalty-function bindings and parameters are stored in
`objectives.toml`. The selected `HosRegimeRegistration` validates this file
and returns the configured rule/personnel pairs that define the expected
scenario results. The application registry resolves `hos_regime_id` and
version and supplies the regime implementation, whose rule findings and
violation measures follow `multiday_vrp.md` §1.7 and §12. An
application-registered extension may also read supplementary instance data from
`application_data_file`; those values participate in the application's
instance configuration identity.

## 11. Input validation and instance loading

The public loader is `load_vrp_instance(path: str | Path, *,
extension_registry: VrpExtensionRegistry | None = None) -> LoadedVrpInstance`.
When `extension_registry` is `None`, the loader uses the empty custom registry;
all built-in measures, aggregations, penalty rules, and the `none` HOS setting
remain available. The loader validates every selected custom extension against
the registry before returning. Applications pass the same registry to the
application builder so the callbacks validated during loading are the
callbacks used during search. The registry and its callback contracts are
specified in §12.

An instance loader performs these checks before search:

1. Read `instance.toml`, verify its version, resolve paths, and load the
   referenced ScenarioBank and objective declarations.
2. Validate the ScenarioBank according to
   `multiday_vrp_scenario_generation.md`; then use its time zone, time unit,
   planning calendar, and day indices to validate the remaining files.
   Confirm that the `arcs.csv` ID set equals the bank's arc catalog.
3. Read catalogs, then validate every foreign-key reference and every
   uniqueness constraint.
4. Validate field types, numeric ranges, timestamps, interval bounds, and
   completeness of conditional tables.
5. Validate consistency requirements from `multiday_vrp.md`, including
   vehicle overnight-depot eligibility, intervention-day eligibility,
   package-transfer eligibility, qualification references, event domains,
   objective groups, penalty bindings, and selected rule data.
6. Resolve application-defined measure, aggregation, penalty, and HOS IDs
   through the loading application's extension registry.
   Each registration validates its ID, version, configuration parameters,
   and any required `application_data_file` or HOS configuration data. An ID
   without a matching registration makes the instance invalid for that
   application.
7. Return `LoadedVrpInstance` using the public fields in §3.5. The application
   binds its resolved model implementations to this value, assigns a
   `ProblemInstanceId` for the bound instance, and passes that same ID to the
   evaluator, default catalog factory, and search engine.

Input validation errors identify the file, row (when applicable), column,
and violated requirement. A loader rejects an instance before search when a
required file, reference, or declaration is missing or inconsistent.

### Initial-plan responsibility

The application caller constructs the starting `VrpSolution` value or values
from the loaded instance and supplies them through
`MultiobjectiveVNS.run(initial_solutions=...)`. The supplied initial set must
contain at least one valid, feasible plan: it must satisfy the solution
representation contract, meet every hard requirement in every ScenarioBank
scenario, and yield usable values for every declared objective. The generic
search API defines the remaining initial-submission behavior in
[`multiobjective-vns.md`](multiobjective-vns.md) and
[`multiobjective-vns-algorithm.md`](multiobjective-vns-algorithm.md).

## 12. VRP extension registry

The file format includes stable IDs for application-specific calculations.
`VrpExtensionRegistry` is the typed Python registry that binds those IDs to
validated callbacks. The VRP application owns the registry and passes it to
`load_vrp_instance` and to the application builder. It resolves these five
extension families: base measures, scenario aggregations, penalty functions,
penalty aggregations, and HOS regimes. Built-in IDs always resolve to the
package implementations fixed by §10 and `multiday_vrp.md`; custom
registrations supply additional IDs in those families.
Passing `extension_registry=None` selects the package-owned empty registry,
whose IDs are `vrp.extensions.empty` and `vrp-extensions-empty-v1`.

`ExtensionFamily` is the closed set
`Literal['base_measure', 'scenario_aggregation', 'penalty_function',
'penalty_aggregation', 'hos_regime']`. `ExtensionMethod` is the closed set
of runtime calculation methods:
`Literal['evaluate', 'aggregate', 'evaluate_scenario']`.
`ObjectiveExtensionFamily` is the subset
`Literal['base_measure', 'scenario_aggregation', 'penalty_function',
'penalty_aggregation']`.
`ViolationCategory` is the closed set
`Literal['early_window', 'late_window', 'vehicle_availability',
'personnel_availability', 'duty_overlap', 'simple_rule', 'hos_rule']`.

Each family resolves the following input field exactly:

| Registry family | TOML selector |
|---|---|
| `base_measures` | `objectives.toml` → `objectives[].base_measure_id` |
| `scenario_aggregations` | `objectives.toml` → `objectives[].aggregation.id` |
| `penalty_functions` | `objectives.toml` → `penalty_functions[].function_id` |
| `penalty_aggregations` | `objectives.toml` → `objectives[].penalty_aggregation.id` |
| `hos_regimes` | `instance.toml` → `hos_regime_id` and `hos_regime_version` |

### 12.1 Registry and registration records

The immutable registry has this public shape:

```text
VrpExtensionRegistry(
    registry_id: str,
    configuration_id: str,
    base_measures: tuple[BaseMeasureRegistration, ...] = (),
    scenario_aggregations: tuple[ScenarioAggregationRegistration, ...] = (),
    penalty_functions: tuple[PenaltyFunctionRegistration, ...] = (),
    penalty_aggregations: tuple[PenaltyAggregationRegistration, ...] = (),
    hos_regimes: tuple[HosRegimeRegistration, ...] = (),
)
```

`registry_id` and `configuration_id` are stable, nonempty strings following
the identifier rules in §3.1.

Each registration is an immutable record. Its `extension_id` is the exact
custom ID written in the relevant TOML field; its `component_id` identifies
the callback implementation in application diagnostics; and its
`configuration_id` identifies the callback behavior and captured fixed
settings. All three are stable, nonempty textual identifiers that follow the
identifier rules in §3.1. Within one family, `extension_id` values are unique.
Across the whole registry, `component_id` values are unique. The registry's
`configuration_id` covers family membership, every registration's
`extension_id`, `component_id`, and `configuration_id`, every HOS version,
and the implementation versions represented by those IDs. Applications
change it whenever any of those details change.
Registry construction validates every ID, requires every callback field to be
callable, and checks family-local `extension_id` uniqueness and registry-wide
`component_id` uniqueness. Static type checking verifies each callable's
declared Protocol signature; callback outputs receive the runtime checks in
§12.4. Invalid registrations raise `VrpExtensionRegistryError` before
instance loading begins. This `ValueError` has fields `reason: str`,
`family: ExtensionFamily | None`, `extension_id: str | None`, and
`component_id: str | None`.

The registration records have these fields:

```text
BaseMeasureRegistration(
    extension_id: str,
    component_id: str,
    configuration_id: str,
    validate_parameters: ValidateExtensionParameters,
    evaluate: BaseMeasureCallback,
)

ScenarioAggregationRegistration(
    extension_id: str,
    component_id: str,
    configuration_id: str,
    validate_parameters: ValidateExtensionParameters,
    aggregate: ScenarioAggregationCallback,
)

PenaltyFunctionRegistration(
    extension_id: str,
    component_id: str,
    configuration_id: str,
    validate_parameters: ValidateExtensionParameters,
    evaluate: PenaltyFunctionCallback,
)

PenaltyAggregationRegistration(
    extension_id: str,
    component_id: str,
    configuration_id: str,
    validate_parameters: ValidateExtensionParameters,
    aggregate: PenaltyAggregationCallback,
)

HosRegimeRegistration(
    extension_id: str,
    version: str,
    component_id: str,
    configuration_id: str,
    validate_configuration: HosConfigurationValidator,
    evaluate_scenario: HosScenarioCallback,
)
```

`version` in `HosRegimeRegistration` must equal `hos_regime_version` in
`instance.toml` and is a nonempty stable string. For the other four families,
the registration's `configuration_id` supplies the implementation-version
identity; TOML parameters supply instance-specific settings. A custom
`extension_id` is
looked up only within its declared family, so the same text may be used in
different families.

### 12.2 Shared extension configuration and scenario records

Before assembling the final `LoadedVrpInstance`, the loader calls
`HosConfigurationValidator` once when a custom HOS regime is selected. Its
request contains normalized personnel data, planning dates, the time unit,
HOS configuration, and application data. The validated rule definitions
become `LoadedVrpInstance.hos_rule_definitions`; the evaluator and HOS
callback use this stored tuple for the instance's full lifetime.
`HosConfigurationRequest.input_location` is
`'instance.toml.hos_configuration_file'`.

`ValidateExtensionParameters` has this contract:

```text
validate_parameters(request: ExtensionParametersRequest) -> None
```

After constructing the normalized `LoadedVrpInstance`, the loader calls this
callback once for each selected custom registration use. It visits objectives
in declaration order and validates that objective's custom base measure,
scenario aggregation, and penalty aggregation parameters in that order. It
then visits custom penalty-function bindings in `objectives.toml` array order.
The callback validates the parameter schema and any required application-data
values. `ExtensionParametersRequest` identifies the family, extension,
objective, optional penalty category and rule, and exact TOML path for that
use. For families other than `penalty_function`, category and rule are
`None`. A category-level penalty binding has a category and `rule_id=None`; a
rule-specific binding supplies both. An absent parameter table is passed as
an empty `FrozenTomlTable`. The extension receives the same immutable
application data during evaluation. Validation is deterministic and has no
state changes.

Custom scenario-dependent calculations receive this immutable trace:

```text
VrpScenarioEvaluation(
    scenario_id: str,
    probability: positive float,
    vehicle_days: tuple[VehicleDayScenarioEvaluation, ...],
)

VehicleDayScenarioEvaluation(
    vehicle_id: str,
    day_index: int,
    duty_start_time: float,
    duty_end_time: float,
    stops: tuple[StopScenarioEvaluation, ...],
    legs: tuple[LegScenarioEvaluation, ...],
)

StopScenarioEvaluation(
    stop_index: nonnegative integer,  # zero-based
    stop_kind: Literal['activity', 'transfer_operation', 'depot_only'],
    node_id: str,
    arrival_time: float,
    service_start: float | None,
    service_finish: float | None,
    departure_time: float,
    break_start: float | None,
    break_finish: float | None,
    activity_ref: ActivityRef | None,
    transfer_step_ref: TransferStepRef | None,
    window_assessments_by_objective: tuple[WindowScenarioAssessment, ...],
)

WindowScenarioAssessment(
    objective_id: str,
    selected_window_index: nonnegative integer,  # zero-based
    early_violation: float,
    late_violation: float,
    guidance_value: float,
)

LegScenarioEvaluation(
    leg_index: nonnegative integer,  # zero-based
    arc_traversals: tuple[ArcTraversalEvaluation, ...],
)

ArcTraversalEvaluation(
    arc_id: str,
    entry_time: float,
    arrival_time: float,
    travel_time: float,
)
```

The trace contains one record per active vehicle-day, route stop, and route
leg in the scenario, preserving route and arc order. `duty_start_time` and
`duty_end_time` give the simulated duty interval. `service_start` and
`service_finish` are `None` for a depot-only stop. Break endpoints are both
`None` when no positive break is scheduled; otherwise they give the actual
break interval. `activity_ref` is present only for an activity stop, and
`transfer_step_ref` is present only for a transfer-operation stop.
`window_assessments_by_objective` contains one record for the dedicated
time-window-violation objective, for an activity with service windows. Its
zero-based selected index is the position of the selected alternative among
alternatives sorted by input `window_index`; it identifies the minimum local
score defined in `multiday_vrp.md` §3.4. Its early and late violation amounts
are tolerance-adjusted, and `guidance_value` is that alternative's
category-scaled local score before penalty aggregation. For every other stop,
`window_assessments_by_objective` is empty. Every time and
duration is a finite Python `float` on the instance's continuous time axis and in its
declared time unit. A callback receives a complete trace for the scenario it
evaluates. The evaluator retains completed traces in `VrpOperatorAnalysis`,
as specified in `multiday_vrp_operators.md` §2.3. Vehicle-days follow the
order of `VrpSolution.routes`; stop and leg indexes follow their route order,
and `arc_traversals` follows each leg's `arc_ids`. All index fields use
built-in Python `int` values with `bool` rejected. A vehicle-day's
`day_index` is positive. Stop times satisfy the corresponding route timeline:
arrival precedes service start, service finish precedes departure, and a
break lies within post-service dwell (or post-arrival dwell for a depot-only
stop). Every `WindowScenarioAssessment` violation and guidance value is a
finite nonnegative `float`. Its `guidance_value` is a per-finding guidance
value for Operators; the objective's actual penalty total is the result of
its configured `PenaltyAggregationCallback`.

### 12.3 Callable contracts

Each callback is a Python `Protocol` with one `__call__` method. The single
request record makes the arguments, including optional values, explicit:

```text
BaseMeasureRequest(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    objective_id: str,
    scenario: VrpScenarioEvaluation,
    parameters: FrozenTomlTable,
    application_data: FrozenTomlTable | None,
)

class BaseMeasureCallback(Protocol):
    def __call__(self, request: BaseMeasureRequest) -> float: ...

ScenarioAggregationRequest(
    objective_id: str,
    scenario_values: tuple[ScenarioObjectiveInput, ...],
    parameters: FrozenTomlTable,
    application_data: FrozenTomlTable | None,
)

ScenarioObjectiveInput(
    scenario_id: str,
    probability: float,
    value: float,
)

class ScenarioAggregationCallback(Protocol):
    def __call__(self, request: ScenarioAggregationRequest) -> float: ...

PenaltyFunctionRequest(
    objective_id: str,
    violation_category: ViolationCategory,
    rule_id: str | None,
    violation_measure: float,
    parameters: FrozenTomlTable,
    application_data: FrozenTomlTable | None,
)

class PenaltyFunctionCallback(Protocol):
    def __call__(self, request: PenaltyFunctionRequest) -> float: ...

PenaltyAggregationRequest(
    objective_id: str,
    category_values: tuple[PenaltyCategoryInput, ...],
    parameters: FrozenTomlTable,
    application_data: FrozenTomlTable | None,
)

PenaltyCategoryInput(
    category_id: ViolationCategory,
    scale: float,
    values: tuple[float, ...],
)

class PenaltyAggregationCallback(Protocol):
    def __call__(self, request: PenaltyAggregationRequest) -> float: ...

ExtensionParametersRequest(
    instance: LoadedVrpInstance,
    family: ObjectiveExtensionFamily,
    extension_id: str,
    objective_id: str,
    violation_category: ViolationCategory | None,
    rule_id: str | None,
    input_location: str,
    parameters: FrozenTomlTable,
    application_data: FrozenTomlTable | None,
)

HosRuleDefinition(
    rule_id: str,
    personnel_ids: tuple[str, ...],
)

HosConfigurationRequest(
    personnel_data: VrpPersonnelData,
    planning_dates: tuple[date, ...],
    time_unit: str,
    input_location: str,
    hos_configuration: FrozenTomlTable,
    application_data: FrozenTomlTable | None,
)

HosRuleFinding(
    rule_id: str,
    personnel_id: str,
    violation_measure: float,
)

HosScenarioRequest(
    instance: LoadedVrpInstance,
    solution: VrpSolution,
    scenario: VrpScenarioEvaluation,
    hos_configuration: FrozenTomlTable,
    application_data: FrozenTomlTable | None,
)

class ValidateExtensionParameters(Protocol):
    def __call__(self, request: ExtensionParametersRequest) -> None: ...

class HosConfigurationValidator(Protocol):
    def __call__(
        self, request: HosConfigurationRequest
    ) -> tuple[HosRuleDefinition, ...]: ...

class HosScenarioCallback(Protocol):
    def __call__(self, request: HosScenarioRequest) -> tuple[HosRuleFinding, ...]: ...
```

All request and result records in this section are immutable value records.
`ScenarioObjectiveInput.value` is the complete scenario-level objective cost
`Y_o[s, x]`, including base measure, penalty contribution, and any service
omission amount, before scenario aggregation. The `rule_id` in
`PenaltyFunctionRequest` is `None` for a category-level binding and contains
the exact rule ID for a rule-specific binding.

`objective_id`, `scenario_id`, `category_id`, `rule_id`, and entity IDs use
the exact strings in the loaded instance and ScenarioBank. Scenario values
are supplied in ScenarioBank order; category values are ordered by the
standard category order in §10.1, and each `values` tuple follows evaluator
finding order. The penalty aggregator receives function outputs before
category scaling, together with the configured scale for each category
assigned to that objective. It receives one `PenaltyCategoryInput` for every
category assigned to that objective, including a category with an empty
`values` tuple. Each applicable finding, including a zero finding, is passed
through its selected penalty function once.

The HOS validator returns at least one rule. Each `rule_id` is a unique,
nonempty string, each `personnel_ids` tuple is nonempty and duplicate-free,
and every personnel ID refers to a person in `personnel_data`. Its tuple
defines every expected `(rule_id, personnel_id)` pair and is stored in
`LoadedVrpInstance.hos_rule_definitions`. The HOS callback can read these
definitions from the request's `instance`. The evaluator requires
`evaluate_scenario` to return exactly one finding for each declared pair,
in `HosRuleDefinition` order and its `personnel_ids` order, with no unknown or
repeated pair. A finding's `violation_measure` is finite and nonnegative;
zero means that rule is
satisfied for that person in that scenario, and a positive value means it is
violated. The HOS implementation aggregates its rule's applicable periods
and triggers into this one measure per person and rule. Under `compliance`
treatment, any positive finding makes the plan infeasible; under `penalty`
treatment, each finding is passed through the corresponding rule-specific or
category-level `hos_rule` penalty function.

The evaluator calls a custom HOS callback once per ScenarioBank scenario,
after it has constructed that scenario's complete route trace. It calls a
custom base-measure callback once for each objective/scenario pair that uses
that measure, after all hard requirements pass. It calls a penalty function
for each applicable objective-specific violation finding and calls the
penalty aggregator once for each objective/scenario penalty total. It calls a
custom scenario aggregation once per objective, after all scenario costs for
that objective are available. Every call follows the order of objectives,
scenarios, findings, and categories defined in this document and
`multiday_vrp.md`. A hard-feasibility failure ends candidate evaluation under
`multiday_vrp.md` §1.8 before later objective callbacks are invoked.

### 12.4 Resolution, validation, and failures

The loader resolves each custom ID exactly in its family. It rejects unknown
IDs, duplicate IDs, a HOS version mismatch, a missing HOS configuration, or
an extension configuration that fails its validator. Built-in IDs resolve to
the fixed package implementations. Configuration validation follows the
order in §12.2. The loader returns the
`LoadedVrpInstance` shape in §3.5, including the validated HOS rule
definitions; callback objects remain in the registry owned by the application.

The application builder resolves the loaded declarations against the same
immutable registry. Its `ApplicationEvaluator.configuration_id` includes
`registry_id`, `registry.configuration_id`, and each selected registration's
`component_id` and `configuration_id`. The `ProblemInstanceId` covers the
loaded instance, ScenarioBank identity, all TOML parameters, application
data, and HOS configuration. These identities together determine resume
compatibility.

All extension callbacks are deterministic and pure for their arguments. They
return built-in Python `float` values; `bool`, integers, `Decimal`, NaN,
infinity, and overflowed values are malformed outputs. Base measures and
scenario aggregations return finite values. Penalty functions return zero
when their input measure is zero and a finite positive value when it is
positive. Penalty aggregations return finite nonnegative values and return
zero exactly when all supplied penalty values are zero. Every HOS finding
obeys the result contract in §12.3. The evaluator checks each returned value
before using it.

A validator exception or malformed validator result raises
`VrpExtensionConfigurationError`, a `ValueError` with fields
`family: ExtensionFamily`, `extension_id: str`, `component_id: str`,
`method: Literal['validate_parameters', 'validate_configuration']`,
`input_location: str`, and `reason: str`. A runtime callback exception or
malformed result raises `VrpExtensionEvaluationError`, a `RuntimeError` with
fields `family: ExtensionFamily`, `extension_id: str`,
`component_id: str`, `method: ExtensionMethod`,
`objective_id: str | None`, `rule_id: str | None`,
`scenario_id: str | None`, and `reason: str`. `input_location` identifies the
TOML path such as `objectives.toml.objectives[0].base_measure_parameters` or
`instance.toml.hos_configuration_file`. `method` is the failing registered
callback name. `validate_parameters` must return `None`; that return value is
checked as a configuration result. If a validator or runtime callback raises,
its exception is chained as the cause; malformed output has no cause. The VRP
`ApplicationEvaluator` propagates that error to the generic executor; the
generic package records the evaluator failure according to
`multiobjective-vns.md` §5.6, with the extension details preserved in the
diagnostic reason.

The ScenarioBank remains an independently versioned data product governed by
`multiday_vrp_scenario_generation.md`. Its `JointImputer` and
`PostHorizonRule` registration and callback contracts remain defined in that
document and are separate from `VrpExtensionRegistry`.
