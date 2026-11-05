# Generating traffic scenarios from historical travel times

## 1. Purpose and context

This tool converts synchronized historical travel-time observations into a
finite, probability-weighted scenario bank. Each scenario supplies travel-time
functions for every declared directed arc and every boundary in a supplied
planning-time grid. The profiles represent a coherent network-wide traffic
realization, preserving observed dependence among nearby links and times.

The user chooses the maximum number of retained scenarios, `K`. The procedure
builds one candidate scenario from each eligible historical day block, then
reduces the weighted empirical bank to at most `K` representative whole-block
scenarios. It handles missing data according to the policy below and writes a
self-contained scenario bank. A consuming application can check that the
bank's arc catalog, time zone, unit, and target grid match its own model.

The procedure estimates the empirical traffic distribution represented by the
selected history. It assumes that the selected dates are informative about the
traffic conditions relevant to the planning horizon. A user may restrict or
weight dates to reflect weekdays, seasons, holidays, or other known conditions.

## 2. Inputs and output

The file generator parses real-valued fields and performs interpolation,
imputation integration, FIFO projection, distance calculation, and probability
normalization with Python `float` values using IEEE 754 binary64 arithmetic.
Input values must be finite after conversion. For finite values `a` and `b`,
numeric constraint and FIFO comparisons use
`tol(a, b) = 1e-9 + 1e-12 * max(abs(a), abs(b))`; this is also the tolerance
adopted by the VRP model. Scenario probabilities must be positive; their sum
must be within `1e-12` of one when reading a completed bank, and the generator
writes normalized probabilities whose sum is one within that tolerance. NaN,
infinity, conversion overflow, and non-finite computed travel times cause a
generation error identifying the affected data location.

### 2.1 Required inputs

The procedure receives:

- **Historical observations.** Each observation identifies a directed arc, an
  actual departure timestamp, and a finite nonnegative travel time. The input
  file stores local civil timestamps in one declared IANA time zone. Each
  timestamp includes its numeric UTC offset to identify the instant and
  distinguish repeated local times. The manifest's `time_zone` defaults to
  `Europe/Zurich`, the Geneva time zone, and may name another IANA zone when
  appropriate. The zone governs calendar dates, local clock times, and
  daylight-saving rules for the observations and target grid.
- **Arc catalog.** The directed arc identifiers whose travel times are
  generated. The catalog defines the tool's scope; downstream applications
  check identifier compatibility with their networks.
- **Target calendar and time grid.** The ordered planning dates and shared
  nonuniform interval boundaries supplied in a separate file. Historical
  profiles are mapped by local clock time to the corresponding day position.
  Explicit UTC offsets distinguish repeated local times during daylight-saving
  transitions. The alignment rules are in §2.2; missing-value handling is in
  §4.
- **Scenario count `K`.** A positive integer chosen by the user. It is the
  maximum number of representative scenarios retained after reduction. If the
  eligible candidate bank has at most `K` distinct scenarios, the procedure
  retains the full bank.
- **Maximum interpolation gap.** The largest actual elapsed time between the
  bracketing observations on one arc and date that may be filled by
  within-profile linear interpolation. The gap and interpolation fraction use
  elapsed time between timestamp instants, expressed in `time_unit`.

The standalone file contract in §2.2 supplies the arc catalog, target grid,
observations, and core generation settings.

The user may also provide:

- **Date-selection rules and optional block weights.** These condition the
  empirical distribution on the planning context. Eligible blocks have equal
  weight by default; supplied positive block weights are normalized over the
  eligible blocks before reduction.
- **A joint imputation model.** This completes unresolved missing values by
  drawing missing values for a full network-time block conditional on its
  observed and interpolated values. Its callable contract is defined in §2.4
  and its data semantics in §4.
- **Random seed.** The seed for reproducible stochastic imputation; it defaults
  to `0`. The generator creates one standard-library `random.Random` instance
  from this seed and passes it to stochastic extensions. Scenario reduction
  itself is deterministic; ties follow the declared candidate-block order.
- **FIFO projection weights.** Optional positive weights for each candidate
  block's arc, day, and boundary value. Unspecified weights are `1.0`; the
  weighted projection gives more protection to values with larger weights.
- **Scenario-distance weights.** Optional nonnegative weights for arc, day,
  and time-grid-boundary travel-time values. Unspecified weights are `1.0`;
  the reduction normalizes weights over nonconstant dimensions. The default
  therefore gives equal weight to each such dimension.
- **Optional protected traffic groups.** User-defined groups of candidate
  blocks, such as blocks in the highest network-congestion range, with a
  minimum number of representatives to retain from each group. Their record
  shape and validation are defined in §2.4. The requested `K` must be large
  enough to satisfy all group requirements.
- **A post-horizon travel-time rule.** The default holds each scenario's final
  boundary travel time constant for later departures. The same deterministic
  rule is applied to every scenario profile; it may use that profile's
  final-boundary values. A custom rule supplies a stable identifier and
  configuration identifier under the contract in §2.4 and §5.

### 2.2 File-based input format

Version 1 uses a TOML manifest and CSV files for the arc catalog, target
calendar and grid, historical observations, and optional block weights. These
files contain the standalone data and core settings for generation. Paths are
resolved relative to the manifest's directory.

```text
scenario-input/
    scenario-generation.toml
    arc-catalog.csv
    target-grid.csv
    historical-travel-times.csv
    block-weights.csv       # optional
```

The manifest has this shape:

```toml
format_version = 1
arc_catalog_file = 'arc-catalog.csv'
target_grid_file = 'target-grid.csv'
observations_file = 'historical-travel-times.csv'
scenario_count = 20
time_zone = 'Europe/Zurich'
time_unit = 'minutes'
maximum_interpolation_gap = 15.0
historical_date_start = 2024-01-01
historical_date_end = 2024-01-31
random_seed = 173
block_weights_file = 'block-weights.csv'
post_horizon_rule_id = 'constant_last_boundary'
post_horizon_rule_configuration_id = 'builtin-v1'

[date_selection]
eligible_block_start_dates = [
    2024-01-01,
    2024-01-08,
]
```

The required fields are `format_version`, `arc_catalog_file`,
`target_grid_file`, `observations_file`, `scenario_count`, `time_unit`,
`maximum_interpolation_gap`, `historical_date_start`, and
`historical_date_end`. The optional
`time_zone` field names an IANA time zone and defaults to `Europe/Zurich`. It
determines local-calendar dates, local clock times, and daylight-saving rules
for all input timestamps. Each timestamp carries its numeric UTC offset, which
must agree with that zone at the represented instant. The timestamps express
local civil times; the offset identifies their corresponding instants. The two
date fields give the inclusive local-calendar span of the historical database
in the selected time zone. Every date in that span participates in block
construction, including a date with no observation rows.
`random_seed`, `block_weights_file`, `post_horizon_rule_id`,
`post_horizon_rule_configuration_id`, and
`[date_selection].eligible_block_start_dates` are optional. When no start dates
are listed, every consecutive `D`-date block within the declared span is a
candidate for coverage checks. Listed dates select candidate block start
dates; each block contains `D` consecutive local calendar dates, where `D` is
the planning-horizon length. A listed date that cannot begin a complete block
within the declared span is reported as excluded. The manifest represents
calendar-based date rules by their resulting list of block start dates.

`time_unit` is one of `'seconds'`, `'minutes'`, or `'hours'`. It applies to
`maximum_interpolation_gap` and every numeric travel-time value in the CSV.
The interpolation gap is measured as elapsed time between timestamp instants.
The generated bank carries this travel-time unit. The generation process
performs no unit conversion. A consuming application checks unit compatibility
when it loads the bank. `random_seed`, when supplied, is a nonnegative TOML
integer; the default is `0`. The effective seed is written to the generation
report. The seed controls stochastic imputation, while the reduction
procedure remains deterministic.
`historical_date_start` must be on or before `historical_date_end`, and
`maximum_interpolation_gap` must be a finite nonnegative number. It is
interpreted as elapsed time between the two bracketing observation instants,
converted to `time_unit`; the spring-forward local-clock discontinuity does
not shorten that gap for interpolation eligibility.
When `eligible_block_start_dates` is supplied, it must contain at least one
unique date. `block_weights_file`, when supplied, contains one positive
weight for each date-selected candidate block start. Weights for blocks later
excluded for incomplete dates or insufficient data coverage are discarded; the
remaining weights are normalized across eligible blocks before reduction.
When `post_horizon_rule_id` is omitted, the generator uses
`constant_last_boundary` with configuration identifier `builtin-v1`. The
configuration identifier is optional for this built-in rule and must equal
`builtin-v1` when supplied. A custom `post_horizon_rule_id` requires a
`post_horizon_rule_configuration_id`; the pair resolves one registered
application rule as specified in §2.4.

`arc-catalog.csv` has one column, `arc_id`, and one row for each directed arc
whose travel times belong in the output. IDs are read as text, are unique, and
are carried unchanged into the scenario bank. The catalog is the complete arc
set for generation.

`target-grid.csv` defines the planning calendar and target travel-time
boundaries:

```csv
day_index,planning_date,boundary_index,boundary_time
1,2027-01-01,0,2027-01-01T00:00:00+01:00
1,2027-01-01,1,2027-01-01T06:00:00+01:00
1,2027-01-01,2,2027-01-02T00:00:00+01:00
2,2027-01-02,0,2027-01-02T00:00:00+01:00
2,2027-01-02,1,2027-01-02T06:00:00+01:00
2,2027-01-02,2,2027-01-03T00:00:00+01:00
```

`day_index` is one-based and consecutive. `planning_date` is a local calendar
date in the declared time zone; dates are consecutive. For each day, boundary
indices start at `0`, increase by one, and end at `M[d] >= 1`. Each day begins
at local midnight and ends at the next local midnight. `boundary_time` is an
ISO 8601 timestamp with a required numeric UTC offset, and that offset must
match `time_zone` in the manifest at the represented instant. Within each day,
the instants are strictly increasing and map to that day's local midnight,
ordered interior boundaries, and next local midnight. The last boundary of day
`d` and boundary `0` of day `d + 1` identify the same instant and appear as
separate rows. Calendar days spanning daylight-saving transitions therefore
have their actual elapsed duration, and interval widths may be nonuniform. For
example, `2027-10-31T02:30:00+02:00` and `2027-10-31T02:30:00+01:00` identify
the two distinct occurrences of 02:30 in `Europe/Zurich`. The number of
planning days `D` is the number of distinct `planning_date` values in this
file.

The observation table is UTF-8 CSV with a header and comma delimiter. It uses
exactly these columns:

```csv
arc_id,departure_time,travel_time
arc-001,2024-01-01T07:00:00+01:00,3.8
arc-002,2024-01-01T07:00:00+01:00,5.1
arc-001,2024-01-01T07:05:00+01:00,4.0
```

Each row gives one observed travel time for one directed arc and one actual
departure instant. `departure_time` uses ISO 8601 notation with a required
numeric UTC offset, and that offset must match `time_zone` in the manifest at
the represented instant. These timestamps provide synchronized, unambiguous
labels across arcs in local time. The underlying observation schedule is
shared across links; a row is present for each observed arc-instant pair,
while an unobserved pair has no row. The generator uses the manifest time zone
to identify each timestamp's local historical date and time of day, including
daylight-saving transitions, and then maps that observation to the
corresponding planning-day position. Each row's local date must fall within
the manifest's declared historical date span.

Historical observations are aligned to target boundaries by local calendar
date position and local clock time. When the source date has one occurrence of
a local clock time, the generator matches by local clock time regardless of
seasonal UTC-offset differences. UTC offset distinguishes observations only
when the source date contains two occurrences of the same local clock time. In
that case, the generator selects the source occurrence whose UTC offset
matches the target boundary. When the source date has one occurrence and the
target date has two, the source value supplies both target occurrences. A
missing observation at a selected source occurrence remains missing and
follows §4's missing-data rules. If the source time is repeated and neither
occurrence has the target boundary's UTC offset, that target value is also
missing under §4.

When a target boundary's local clock time falls inside a spring-forward gap on
the historical source date, no source instant corresponds to that local time.
The aligned value is labeled `missing`; its handling follows §4.

For example, an observation at 08:00 with offset `+02:00` on a summer source
date matches a target boundary at 08:00 with offset `+01:00` on a winter
planning date. Each offset records its own date's seasonal instant, and the
local clock time provides the match.

`arc_id` is read as text and must match an identifier in `arc-catalog.csv`
exactly. `travel_time` is a finite nonnegative decimal number in the
declared `time_unit`. A missing arc-time observation is represented by the
absence of its row; short-gap interpolation and joint imputation follow §4.
The table may be in any row order. A repeated
`(arc_id, departure_time)` pair, an unknown arc ID, a
malformed timestamp, or a blank, nonnumeric, negative, NaN, or infinite travel
time is an input error. Multiple raw sensor readings for one arc and instant
are aggregated before this file is created.

When `block_weights_file` is supplied, its required columns are
`block_start_date,weight`:

```csv
block_start_date,weight
2024-01-01,2.0
2024-01-08,1.0
```

Dates use ISO `YYYY-MM-DD` local calendar dates and identify complete blocks
as defined above. Each date-selected candidate block start appears exactly
once, and every weight is a finite positive decimal number. Rows for blocks
excluded later for date completeness or data coverage are reported and their
weights are omitted from normalization.

The manifest specifies the portable, file-backed inputs. A configured joint
imputer, FIFO projection weights, scenario-distance weights, protected traffic
groups, and custom post-horizon rules use the API contracts in §2.4 and are
recorded in the generation report.

### 2.3 Output

The caller selects an output directory when invoking the tool. The generation
procedure writes a self-contained `ScenarioBank` directory there.
The destination path must be absent or an existing empty directory. An
existing nonempty directory (including one containing only hidden entries) or
a path that names a non-directory is rejected with `ScenarioInputError`; the
generator never overwrites existing files. The generator creates a unique
temporary directory beside the destination, writes all bank files there,
closes them, and validates the completed file set. Immediately before
publication, it checks the destination again; a destination that became
nonempty is preserved and causes publication to fail. The generator publishes
the bank with one atomic directory rename on the same filesystem. If the
destination was an existing empty directory, that empty directory is removed
immediately before publication. The completed bank therefore appears at the
destination only after every output file is complete.
On a write or publication failure, the generator raises
`ScenarioGenerationError` with the failed operation and relevant path, removes
the staging directory when possible, and never leaves a partial bank at the
destination. The destination remains absent or empty unless it became
nonempty after initial validation, in which case its contents are preserved.
A staging directory that cannot be removed is identified in the error
diagnostic; it is never returned as a `ScenarioBank`.

The bank includes the arc catalog, time zone, time unit, target calendar and
grid, ordered scenarios, probabilities, travel-time profiles, and the
post-horizon rule identity pair. The bank manifest requires `time_zone` and
always writes the effective IANA time zone explicitly, including when the
generation input used the `Europe/Zurich` default. A consuming application
uses that field as the time-zone authority and checks compatibility
with its own network, calendar, and time units when it loads the bank.

A profile value is indexed by `(arc_id, day_index, boundary_index)`, where
`day_index` is the planning-day index and `boundary_index` ranges from `0`
through `M[day_index]` in `target-grid.csv`. The day index is part of the key:
the end boundary of a day and the start boundary of the next day are separate
values, allowing their day-specific travel times to differ at midnight.
Within each day's grid, adjacent boundary values define the piecewise-linear
travel-time function. The ordered scenario probabilities are positive finite
Python floats. Their sum must be within `1e-12` of one; accepted values are
normalized on load. The generator normalizes retained scenario weights before
writing the bank.

The output directory uses one TOML manifest and four CSV tables:

```text
scenario-bank/
    scenario-bank.toml
    arc-catalog.csv
    target-grid.csv
    scenarios.csv
    travel-times.csv
```

```toml
format_version = 1
arc_catalog_file = 'arc-catalog.csv'
target_grid_file = 'target-grid.csv'
time_zone = 'Europe/Zurich'
time_unit = 'minutes'
scenario_count = 2
scenarios_file = 'scenarios.csv'
travel_times_file = 'travel-times.csv'
post_horizon_rule_id = 'constant_last_boundary'
post_horizon_rule_configuration_id = 'builtin-v1'
```

All paths are relative to `scenario-bank.toml`. The arc catalog and target
grid are included so the bank can be read independently of the generation
inputs and any consuming model. The schemas for these two files are the same
as in §2.2. `scenario_count` equals the number of rows in `scenarios.csv`.
Both `post_horizon_rule_id` and `post_horizon_rule_configuration_id` are
required bank-manifest fields.
`post_horizon_rule_id` and `post_horizon_rule_configuration_id` identify the
rule that extends each final-boundary profile to later departures. The built-in
pair `constant_last_boundary` and `builtin-v1` holds the final-boundary travel
time constant. An application-defined rule uses its stable application ID and
configuration ID; the consuming application supplies the matching rule when
it loads the bank.

`scenarios.csv` has exactly one row for each scenario:

```csv
scenario_id,probability
scenario-000001,0.6
scenario-000002,0.4
```

Scenario IDs are assigned in retained candidate order as
`scenario-000001`, `scenario-000002`, and so on. Probabilities are finite,
positive decimal numbers whose sum is within `1e-12` of one. The generator
normalizes the probabilities before writing them, and a consuming loader
applies the same validation and normalization rule. The scenario table's row
order defines the bank's scenario order.

`travel-times.csv` has one row for every scenario, directed arc, planning day,
and day-grid boundary:

```csv
scenario_id,arc_id,day_index,boundary_index,travel_time
scenario-000001,arc-001,1,0,3.8
scenario-000001,arc-001,1,1,4.1
scenario-000001,arc-001,1,2,4.5
scenario-000002,arc-001,1,0,5.2
scenario-000002,arc-001,1,1,5.8
scenario-000002,arc-001,1,2,6.0
scenario-000001,arc-001,2,0,4.7
scenario-000001,arc-001,2,1,4.8
scenario-000001,arc-001,2,2,4.6
scenario-000002,arc-001,2,0,6.2
scenario-000002,arc-001,2,1,6.3
scenario-000002,arc-001,2,2,6.1
```

The arc catalog supplies valid arc IDs; `target-grid.csv` supplies the number
of planning days and the boundary count `M[d]` for each day. `day_index` is a
one-based integer and `boundary_index` is an integer in `0..M[d]`. The two
records
`(d, M[d])` and `(d + 1, 0)` remain distinct at midnight. `travel_time` is a
finite nonnegative decimal number in the manifest's `time_unit`. Each key
`(scenario_id, arc_id, day_index, boundary_index)` occurs exactly once; IDs
must refer to `scenarios.csv` and `arc-catalog.csv`. Rows may appear in any
order.

The generator validates the bank's cross-references, profile coverage,
travel-time values, and FIFO properties before publishing the output files.
A successful call returns the `GenerationResult` defined in §2.4, with
`output_directory` set to the published destination path.

### 2.4 Generation options and extension contracts

The Python interface is:

```text
generate_scenario_bank(
    input_manifest_path,
    output_directory,
    *,
    options: ScenarioGenerationOptions | None = None,
) -> GenerationResult
```

`None` selects the defaults listed below. The public result records have the
following shapes:

```text
GenerationResult:
    output_directory: Path
    report: GenerationReport

GenerationReport:
    report_version: int
    time_zone: str
    time_unit: str
    requested_scenario_count: int
    maximum_interpolation_gap: float
    random_seed: int
    joint_imputer_id: str | None
    joint_imputer_configuration_id: str | None
    post_horizon_rule_id: str
    post_horizon_rule_configuration_id: str
    date_selected_block_start_dates: tuple[str, ...]
    eligible_block_start_dates: tuple[str, ...]
    source_data_coverage: SourceDataCoverage
    interpolations: tuple[InterpolationRecord, ...]
    imputation_summary: ImputationSummary
    effective_fifo_projection_weights: Mapping[FifoWeightKey, float]
    unused_fifo_projection_weight_keys: tuple[FifoWeightKey, ...]
    effective_scenario_distance_weights: Mapping[ProfileCoordinate, float]
    protected_traffic_groups: tuple[ProtectedTrafficGroup, ...]
    protected_group_representatives: Mapping[str, tuple[str, ...]]
    fifo_adjustments: FifoAdjustmentSummary
    excluded_blocks: tuple[ExcludedBlock, ...]
    candidate_profile_count: int
    candidate_scenario_count: int
    retained_scenario_count: int
    candidate_assignments: tuple[CandidateAssignment, ...]
    reduction_cost: float
    scenario_probabilities: tuple[ScenarioProbability, ...]
```

The supporting report records are:

```text
HistoricalDateCoverage:
    historical_date: date
    expected_value_count: int
    observed_value_count: int
    interpolated_value_count: int
    unresolved_missing_value_count: int

SourceDataCoverage:
    expected_value_count: int
    observed_value_count: int
    interpolated_value_count: int
    unresolved_missing_value_count: int
    by_date: tuple[HistoricalDateCoverage, ...]

InterpolationRecord:
    coordinate: HistoricalCoordinate
    interpolated_value: float
    left_observation_time: datetime
    left_observation_value: float
    right_observation_time: datetime
    right_observation_value: float

BlockImputationSummary:
    block_start_date: str
    missing_value_count: int
    imputed_value_count: int

ImputationSummary:
    imputed_value_count: int
    by_block: tuple[BlockImputationSummary, ...]

ExcludedBlock:
    block_start_date: str
    reason: Literal['incomplete_date_span', 'unresolved_missing_values']
    missing_dates: tuple[date, ...]
    missing_coordinates: tuple[HistoricalCoordinate, ...]

FifoAdjustment:
    block_start_date: str
    arc_id: str
    day_index: int
    boundary_index: int
    original_travel_time: float
    projected_travel_time: float
    absolute_change: float

FifoAdjustmentSummary:
    maximum_absolute_change: float
    total_absolute_change: float
    adjustments: tuple[FifoAdjustment, ...]

CandidateAssignment:
    source_block_start_dates: tuple[str, ...]
    source_probability: float
    retained_scenario_id: str
    retained: bool
    transferred_probability_mass: float

ScenarioProbability:
    scenario_id: str
    probability: float
```

These are the complete public fields for `report_version = 1`.
`GenerationResult`, `GenerationReport`, and every supporting report record are
immutable values. `report_version` is `1`. `time_zone` is the effective IANA
zone, `time_unit` is the effective travel-time unit, `requested_scenario_count`
is the manifest value `K`, `maximum_interpolation_gap` is the effective gap in
`time_unit`, and `random_seed` is the effective seed from the manifest or its
default. `maximum_interpolation_gap` is a finite nonnegative float. All
integer fields use built-in Python `int` values with `bool` rejected; counts
are nonnegative, `requested_scenario_count` is positive, and `random_seed` is
nonnegative. Date strings use ISO `YYYY-MM-DD`. All report real values are
finite Python `float` values; coverage ratios are represented by their
integer numerator and denominator counts. Date, profile, and scenario tuples
preserve the order specified below. All mappings are immutable read-only
views.

`source_data_coverage` describes the aligned historical value grid after
short-gap interpolation and before block imputation. Its four value counts
sum to `expected_value_count`; the per-date records sum to the same totals.
`expected_value_count` counts every combination of historical date, arc,
planning-day position, and target-grid boundary in `AlignedHistoricalData`.
`by_date` is ordered by ascending historical date, and each date's four counts
sum to its `expected_value_count`. `len(interpolations)` equals
`interpolated_value_count` in `source_data_coverage`.
Each `InterpolationRecord` describes one interpolated target coordinate,
including the two same-arc, same-date observations used to obtain it. The
observation times in that record are timezone-aware datetimes in the source
zone, and the observation and interpolated values use `time_unit`. The
`imputation_summary` counts imputed candidate-block coordinates; if blocks
overlap in historical dates, the same aligned coordinate is counted once for
each block imputation. Its total is the sum of `by_block.imputed_value_count`.
Each block record reports the missing-coordinate count before imputation and
the same number of successfully imputed values. `by_block` has one record for
each block that used imputation. With no imputed values, the total is zero and
`by_block` is empty.

`date_selected_block_start_dates` records the date-selected block starts,
including starts later listed in `excluded_blocks`. `eligible_block_start_dates`
contains starts that pass date-span and coverage checks and proceed to profile
construction; a block requiring configured imputation is eligible. An
`ExcludedBlock` with reason `incomplete_date_span` has its absent dates in
`missing_dates` and an empty `missing_coordinates` tuple. An exclusion with
reason `unresolved_missing_values` has an empty `missing_dates` tuple and
lists every unresolved coordinate in `missing_coordinates`.

`candidate_profile_count` counts complete profiles before identical profiles
are coalesced. `candidate_scenario_count` counts distinct profiles after
coalescing and before reduction. `retained_scenario_count` equals
`len(scenario_probabilities)` and the output manifest's `scenario_count`.
Each `CandidateAssignment` represents one coalesced candidate. Its source
block dates are ordered by date, and assignment records are ordered by their
earliest source block date. `source_probability` is their summed normalized
input mass. `retained_scenario_id` identifies the final scenario receiving
that candidate's mass. A retained candidate has `retained=True` and
`transferred_probability_mass=0.0`; a removed candidate has `retained=False`
and transfers its full `source_probability`. `reduction_cost` is the finite,
nonnegative final `cost(retained)` from §6.2, with value `0.0` when every
candidate is retained. Candidate `source_probability` values are positive,
normalized before reduction, and sum to one within the probability tolerance
in §2.3. `scenario_probabilities` follows `scenarios.csv` order and contains
every output scenario's positive final probability; their sum satisfies the
same tolerance.

`fifo_adjustments` includes one `FifoAdjustment` for each boundary value
changed by projection, so each `absolute_change` is positive.
`absolute_change` equals the absolute difference
between `original_travel_time` and `projected_travel_time`; all values use
`time_unit`. The records are ordered by candidate block start,
`arc_ids` order, day index, then boundary index. `maximum_absolute_change` is
the largest recorded absolute change and `total_absolute_change` is their
sum; both are `0.0` when there are no adjustments. `interpolations` are
ordered by historical date, arc-catalog order, day index, and boundary index.
Block-oriented tuples use ascending block-start date. `protected_traffic_groups`
preserves the option order. `protected_group_representatives` maps each
`group_id` to the ordered tuple of retained `scenario_id` values satisfying
its quota. The `effective_fifo_projection_weights` mapping has one entry for
each projected profile coordinate, including default weights; its keys are
`(block_start_date, arc_id, day_index, boundary_index)`. The
`unused_fifo_projection_weight_keys` tuple contains supplied keys for
date-selected blocks excluded before projection. The
`effective_scenario_distance_weights` mapping contains normalized effective
weights, including defaults, for each nonconstant profile dimension and is
empty when there are no such dimensions.

The imputer ID and configuration ID are both `None` when no joint imputer is
configured; otherwise both are its registered nonempty strings. The
post-horizon rule ID and configuration ID are always nonempty strings and
include the built-in pair when the default is used. Every other field is
non-null on a successful result. Optional collections use empty tuples or
read-only mappings, and their associated counts or adjustment totals use
zero when no entries apply.

`ScenarioGenerationOptions` is fixed for one call; the generator snapshots its
option mappings at call entry. Extension instances follow their callable
state contract below. Defaults are:

| Option | Type and meaning | Default |
|---|---|---|
| `joint_imputer` | One object implementing `JointImputer` below. | `None`; unresolved blocks are excluded. |
| `fifo_projection_weights` | Mapping from `(block_start_date, arc_id, day_index, boundary_index)` to a positive `FiniteNumber`. | Every projection weight is `1.0`. |
| `scenario_distance_weights` | Mapping from `(arc_id, day_index, boundary_index)` to a nonnegative `FiniteNumber`. | Every dimension has raw weight `1.0`. |
| `protected_traffic_groups` | Sequence of `ProtectedTrafficGroup` records below. | Empty sequence. |
| `post_horizon_rules` | Mapping of application-defined custom rules from `(rule_id, configuration_id)` to a `PostHorizonRule` object. | Empty mapping. The package provides `('constant_last_boundary', 'builtin-v1')` internally. |

The public record and callback shapes are:

```text
ProfileCoordinate = tuple[str, int, int]
HistoricalCoordinate = tuple[date, str, int, int]
FifoWeightKey = tuple[str, str, int, int]
SourceLabel = Literal['observed', 'interpolated', 'missing']
FiniteNumber = int | float  # bool is rejected; accepted values become float

AlignedHistoricalData:
    time_zone: str
    time_unit: str
    historical_dates: tuple[date, ...]
    arc_ids: tuple[str, ...]
    target_boundary_times: Mapping[tuple[int, int], datetime]
    values: Mapping[HistoricalCoordinate, float | None]
    source: Mapping[HistoricalCoordinate, SourceLabel]

ImputationRequest:
    block_start_date: str
    values: Mapping[ProfileCoordinate, float | None]
    source: Mapping[ProfileCoordinate, SourceLabel]

ProtectedTrafficGroup:
    group_id: str
    block_start_dates: frozenset[str]
    minimum_representatives: int

class JointImputer(Protocol):
    extension_id: str
    configuration_id: str
    def fit(
        self,
        aligned_history: AlignedHistoricalData,
        random_source: random.Random,
    ) -> None: ...
    def impute_missing(
        self,
        request: ImputationRequest,
        random_source: random.Random,
    ) -> Mapping[ProfileCoordinate, FiniteNumber]: ...

class PostHorizonRule(Protocol):
    rule_id: str
    configuration_id: str
    def travel_time_at(
        self,
        arc_id: str,
        departure_time: datetime,
        final_boundary_time: datetime,
        final_boundary_profile: Mapping[str, float],
        time_unit: str,
    ) -> FiniteNumber: ...
```

Option weights and callback results accept built-in Python `int` or `float`
values, with `bool` rejected. The generator converts them to binary64 `float`
once, then checks finiteness and declared ranges; conversion overflow is an
error. FIFO weights are strictly positive, and distance weights are
nonnegative. All Python API indices and integer counts, including
`minimum_representatives`, use built-in `int` values with `bool` rejected. The
generator converts no option identifiers implicitly.
`ScenarioInputError` reports invalid files, option keys or values, duplicate
group IDs, unresolved rule registrations, and an invalid initial output
destination under §2.3. `ScenarioGenerationError`
reports empty eligible input, extension exceptions or malformed outputs, and
invalid generated profiles. It also reports staging, file-write, serialized-
bank validation, and publication failures, including the operation and path;
destination conflicts detected at publication are `ScenarioGenerationError`.
`ScenarioReductionError` is a
`ScenarioGenerationError` for an infeasible representative-count request. An
extension failure includes the extension or rule ID, configuration ID,
method name, and applicable block, profile coordinate, arc, or departure
instant. A raised callback exception is chained as the cause; a malformed
return value has no cause.

`block_start_date` and `day_index` in option keys use the ISO date string and
one-based integer from the input data. A profile coordinate is the exact
`(arc_id, day_index, boundary_index)` tuple used in `travel-times.csv`. A
missing key in either weight mapping uses its stated default. A weight key
in `scenario_distance_weights` must name an arc, day, and boundary in the
input catalogs. A `fifo_projection_weights` key must additionally name a
date-selected candidate block. A key with an unknown arc, day, boundary, or
block is an input error; a FIFO weight for a date-selected block later excluded
for an incomplete date span or missing coverage is listed in
`unused_fifo_projection_weight_keys`. FIFO weights
are applied to their corresponding arrival-time values before isotonic
projection. Distance weights are applied to their corresponding travel-time
dimensions; the reduction normalizes supplied and default raw weights over
nonconstant dimensions, as specified in §6.1.

A `ProtectedTrafficGroup` is immutable and has three fields: a nonempty
unique `group_id`, a nonempty set of `block_start_dates`, and a positive integer
`minimum_representatives`. Every listed date must identify a date-selected
candidate block. Group membership is attached to the candidate produced from
that block; if identical profiles are coalesced, the coalesced candidate gets
the union of their memberships. The requested minimum counts distinct
representative profiles after coalescing and after incomplete blocks have
been excluded. The generator raises `ScenarioReductionError` when the
remaining candidates and `scenario_count` cannot satisfy every group minimum.

Extension IDs and configuration IDs are nonempty, stable strings.
`extension_id` or `rule_id` identifies the extension implementation and
version. A configuration ID identifies the captured parameters and data that
affect an extension's behavior. An extension returns the same result for the
same inputs and random draws. Every stochastic extension uses the one
`random.Random` instance created from the manifest's effective `random_seed`;
extension code uses that instance for all random draws. A callback exception or
invalid return value becomes `ScenarioGenerationError` with the extension
identity and operation.
The `(rule_id, configuration_id)` registry key must equal the registered
object's attributes. The built-in rule pair is reserved for the package rule.
After conversion, numeric extension results and weights are finite Python
`float` values within their declared ranges. `fit` returns `None`.

`AlignedHistoricalData` covers every date in the manifest's inclusive
historical date span, including dates outside selected candidate blocks. Its
`target_boundary_times` maps each `(day_index, boundary_index)` to the
corresponding time-zone-aware target instant, with one entry for each target
grid boundary. The mapping key for `values` and
`source` is `(historical_date, arc_id, day_index, boundary_index)`; `values`
maps to a finite float or `None`, and `source` maps to `observed`,
`interpolated`, or `missing`. Both mappings contain every combination of a
historical date, arc, day position, and target boundary. `observed` and
`interpolated` entries have a finite travel-time value; `missing` entries have
`None`. `ImputationRequest` contains `block_start_date`, `values`, and
`source` for every `(arc_id, day_index, boundary_index)` in that block, with
the same value/source pairing. The mappings are read-only views for the
callback.

The generator calls `fit` once when at least one selected candidate block has
unresolved values and an imputer is configured. It passes the aligned history
after short-gap interpolation, with each value labeled as `observed`,
`interpolated`, or `missing`. The imputer replaces its fitted state on each
`fit` call. For each selected block that still has unresolved values, the
generator calls `impute_missing` once with an immutable request containing
that block's ISO start-date ID and the complete value and source maps. The
returned mapping has exactly the coordinates labeled `missing` as keys; each
value converts to a finite nonnegative float. Observed and interpolated values
remain unchanged. An empty or partial mapping, an extra key, or a malformed
value is a generation error. With no unresolved block, the configured imputer
is not called. After `fit`, `impute_missing` leaves the fitted model state
unchanged; its block-specific output depends on the request and the supplied
random source.

The manifest's rule ID and configuration ID select exactly one object from
`post_horizon_rules`. The callback receives time-zone-aware datetimes in the
bank's IANA time zone and a departure instant at or after the final horizon
boundary. `final_boundary_profile` is an immutable mapping from every catalog
`arc_id` to that arc's travel time at the final boundary. The callback returns
a value that converts to a finite nonnegative travel time in the bank's unit.
At the final boundary it
returns `final_boundary_profile[arc_id]`, within the numeric tolerance in §2.
The rule is deterministic for the same inputs and its arrival-time function is
nondecreasing for every departure instant from
the final boundary onward. The extension author guarantees this global FIFO
property; validation of individual returned values is defined in §5. The
consuming application uses the same rule and configuration identified by the
bank manifest.

## 3. Scenario and block definitions

Let `D` be the number of calendar days in the planning horizon. A historical
day profile is the set of observations for one historical date across all arcs
and departure times. A historical block is an ordered sequence of `D`
consecutive calendar dates. A block therefore contains one joint network-wide
traffic trajectory for every day of the planning horizon.

The public `block_start_date` identifier is the block's first local calendar
date in ISO `YYYY-MM-DD` form. For a historical date `r`, arc `a`, and
departure time `t`, write
`x[r, a, t]` for the observed travel time. The aligned historical data form a
partially observed array indexed by date, arc, and departure time. The
observation mask records which entries are present.

Constructing each candidate from a whole date or block preserves the empirical
co-movement among observed links and times. For example, congestion observed
simultaneously on several connected arcs remains present in the same scenario.

For a multi-day horizon, blocks are formed from consecutive dates so that the
observed dependence between adjacent days remains represented. The user may
restrict blocks to calendar patterns compatible with the planning dates, such
as matching weekdays or season. Each eligible block becomes one candidate
scenario after completion, profile construction, and FIFO processing. When
date restrictions and block weights are unspecified, all eligible blocks
receive equal probability.

## 4. Missing-data treatment

A missing observation represents an unknown travel time. The procedure records
it in the observation mask and applies these steps in order.

1. **Interpolate short internal gaps.** For one arc on one historical date, an
   ordinary missing target time may be linearly interpolated from the nearest
   observed times on both sides. Convert the bracketing observation timestamps
   and target timestamp to UTC instants. Their elapsed separation must be at
   most `maximum_interpolation_gap` in `time_unit`. With instants `t_L < t <
   t_R` and observed values `x_L` and `x_R`, compute
   `x(t) = x_L + ((t - t_L) / (t_R - t_L)) * (x_R - x_L)`. Both observations
   and the target must correspond to actual instants on that source date. A
   coordinate labeled missing because of spring-forward alignment in §2.2
   remains missing through this step. Each interpolated value and its
   bracketing observations appear in `GenerationReport.interpolations` (§2.4).
2. **Impute remaining gaps jointly, when configured.** A joint imputation model
   implements the `JointImputer` contract in §2.4. It returns values for all
   unresolved coordinates in one candidate block, conditional on its known
   values and fitted historical data. This stochastic draw carries the
   imputer's dependence across arcs, times, and days into the candidate bank.
3. **Exclude unresolved blocks.** When no joint imputation model is configured,
   a block with any unresolved value needed to fill the declared arc catalog
   and target grid is ineligible. Its missing coordinates appear in
   `GenerationReport.excluded_blocks` (§2.4).

A joint imputation model should represent shared traffic conditions, for
example through common network-state factors with temporal persistence and
arc-specific residual variation. Its joint draws represent cross-link and
time dependence. If missingness is
associated with traffic conditions, the imputation model must represent that
missingness mechanism or the user must select a date subset with an appropriate
coverage pattern. The report's source coverage and exclusion fields (§2.4)
show the resulting selection effects. The fit and per-block call boundaries
are defined in §2.4.

An in-horizon profile requires bracketing observations within the configured
maximum gap or completion by the joint imputer. A target boundary with an
unbracketed gap is ineligible until the joint imputer completes it.

## 5. Profile construction and FIFO processing

For each eligible historical block, the procedure constructs values at the
boundaries in `target-grid.csv`. It maps each historical date to its
corresponding target-day position and local clock time, then applies the
alignment and missing-data rules in §§2.2 and 4 for each arc. Jointly imputed
values complete unresolved coordinates when configured. The resulting profile
uses the target day's boundaries, including its nonuniform intervals.

The output contract requires FIFO. For a profile with boundary times
`theta[j]` and travel times `tau[j]`, define the boundary arrival values
`A[j] = theta[j] + tau[j]`. FIFO requires these arrival values to be
nondecreasing across the full planning horizon, including the left-hand and
right-hand values at each midnight transition.

The default FIFO processing projects the full sequence of boundary arrival
values for each scenario and arc onto the set of nondecreasing sequences, using
weighted isotonic regression. Each value has weight `1.0` by default; callers
may supply positive weights using the key contract in §2.4. The projection
enforces `A[j] >= theta[j]`, preserving nonnegative travel times. The corrected travel values are
`tau[j] = A[j] - theta[j]`; linear interpolation between corrected boundary
values then gives FIFO arrival functions on each interval. The maximum,
aggregate, and coordinate-level FIFO changes appear in
`GenerationReport.fifo_adjustments` (§2.4). A profile whose projected values
fail the travel-time contract causes scenario generation to stop with a
diagnostic.

For departures at or after the final horizon boundary, the default extension
uses the scenario's final-boundary travel time as a constant. An alternative
application rule must match the final-boundary travel time and preserve FIFO
for all later departure times, including across the final-boundary transition.
The registered callback signature and configuration matching are defined in
§2.4. The generator queries a custom rule at the final boundary and checks
that its result matches the stored final travel time within the numeric
tolerance. It checks this returned value for finiteness and nonnegativity.
During VRP evaluation, the application checks each later queried value under
[`multiday_vrp.md` §1.2](multiday_vrp.md#12-scenario-based-time-dependent-travel-data). The
extension author guarantees FIFO over the rule's entire post-horizon domain;
finite boundary and query checks support that guarantee without replacing it.

## 6. Scenario reduction

Scenario reduction replaces a large weighted empirical bank with a smaller
weighted bank whose scenarios are selected from the original candidate bank.
Each retained scenario is a complete historical block, so its link, time, and
day relationships stay together. The probability assigned to a retained
scenario is the total original probability of the candidate scenarios it
represents.

This follows the probability-metric formulation of scenario reduction:
choose a subset of prescribed cardinality and probabilities that approximate
the original discrete distribution. Dupačová, Gröwe-Kuska, and Römisch develop
this formulation and heuristic backward and forward reduction methods using
probability metrics [1]. The procedure below uses a transparent greedy
backward rule with a weighted transportation-distance objective. Its distance
and representative bank are tailored to this travel-time application. The
greedy rule approximates the minimum-cost subset; finding a global minimum is a
separate combinatorial optimization problem.

### 6.1 Distance between candidate scenarios

Represent candidate scenario `i` by its travel-time values `x[i, c]`, where a
dimension `c` identifies an arc, planning day, and time-grid boundary. The same
post-horizon rule applies to every candidate; scenario variation in its default
extension comes from the final-boundary travel times included in `x`. For each
dimension, let `range[c]` be the maximum candidate value minus the minimum.
Constant dimensions contribute zero and are omitted. For the remaining set
`C_var`, define the default distance

```text
d(i, j) = sum over c in C_var of
          weight[c] * abs(x[i, c] - x[j, c]) / range[c]
```

Each nonconstant dimension has raw weight `1.0` by default. A caller may
override selected dimensions with finite nonnegative weights under the key
contract in §2.4. The generator normalizes all effective weights over
`C_var`; their sum must be positive when `C_var` is nonempty. An empty
`C_var` gives distance zero for every pair. The weights may emphasize
selected arcs, days, or times. Range scaling puts the dimensions on comparable
scales; the weights state which dimensions matter to the application. The distance formula is
fixed for version 1, and weights are its customization point. Candidate scenarios with
identical travel-time profiles and identical post-horizon rules are coalesced
before reduction, with their probabilities added.

The representative-selection unit is the complete historical block. Its link
and time values stay together, preserving the observed joint traffic pattern.
The mode of an individual link-time distribution may be reported as a marginal
summary. The scenario bank represents joint combinations by selecting complete
recorded block profiles, so the observed co-occurrence among link and time
values remains visible. A representative is a selected historical profile
that stands for nearby profiles under `d`.

### 6.2 Greedy backward selection and probabilities

Let candidate scenarios be indexed by `i = 1, ..., M`, with probabilities
`q[i] > 0` summing to one. For a retained index set `R`, define its reduction
cost as

```text
cost(R) = sum over i of q[i] * min over j in R of d(i, j)
```

This cost moves each candidate's probability mass to its nearest retained
scenario. Starting with every candidate retained, the greedy backward method
tests each possible single deletion, removes the candidate that yields the
smallest resulting `cost(R)`, and repeats until `K` candidates remain. Ties
are resolved by the declared candidate-block order. If `M <= K`, all
candidates remain.

After selection, each removed candidate is assigned to its nearest retained
scenario. Distance ties go to the retained scenario that appears first in the
declared order. Each retained candidate represents itself, including when its
distance to another retained candidate is zero. A retained scenario's
probability is the sum of the probabilities assigned to it. Thus the retained
probabilities stay positive and are normalized to sum to one. If the user
supplies block weights, those weights define the original probabilities
`q[i]` before reduction.

Protected traffic groups use the record and feasibility rule in §2.4. They can
preserve named traffic regimes while reduction applies the distance rule to
the remaining choices. Their representative scenario IDs and probabilities
are available through the corresponding `GenerationReport` fields (§2.4).

For example, suppose six equally weighted candidate blocks form two traffic
regimes, with four blocks close to one retained profile and two close to
another. With `K = 2`, the representatives receive probabilities `4/6` and
`2/6`. Each representative retains the full observed trajectory of its source
block, including the cross-link and cross-day pattern.

The reduction cost controls average profile discrepancy under the selected
distance and weights. Accuracy for individual plan evaluations and rare
quantile objectives requires a separate comparison against the full candidate
bank. A very small `K` gives coarse probability resolution for high quantiles. The user
should compare the banks for application-relevant statistics and risk levels,
then increase `K` or add protected groups when the observed error is material.

## 7. Scenario-generation algorithm

Let `B` be the set of eligible historical blocks after date filtering and
missing-data coverage checks. Each block `b ∈ B` has a positive weight `q[b]`.
The weights are normalized over eligible blocks; equal weights are the default.

```text
function GENERATE_SCENARIO_BANK(
    input_manifest_path,
    output_directory,
    options
):
    if options is None:
        options = ScenarioGenerationOptions()
    validate output_directory under §2.3
    read and validate the TOML manifest
    read arc catalog, target grid, observations, and optional block weights
    K = manifest.scenario_count
    seed = manifest.random_seed or 0
    time_zone = manifest.time_zone or 'Europe/Zurich'
    time_unit = manifest.time_unit
    joint_imputer = options.joint_imputer
    scenario_distance_weights = options.scenario_distance_weights
    protected_traffic_groups = options.protected_traffic_groups
    rule_id = manifest.post_horizon_rule_id or 'constant_last_boundary'
    if rule_id == 'constant_last_boundary':
        rule_configuration_id =
            manifest.post_horizon_rule_configuration_id or 'builtin-v1'
    else:
        require manifest.post_horizon_rule_configuration_id
        rule_configuration_id = manifest.post_horizon_rule_configuration_id
    if (rule_id, rule_configuration_id) ==
       ('constant_last_boundary', 'builtin-v1'):
        post_horizon_rule = package_constant_last_boundary_rule
    else:
        post_horizon_rule = resolve exact pair in options.post_horizon_rules
    validate all option keys, values, and extension identities
    random_source = random.Random(seed)
    validate timestamps, time zone, arc identifiers, and travel-time values
    align observations by historical date, arc, and local departure time
    apply §2.2's UTC-offset rule to distinguish repeated local times
    apply §4 interpolation to eligible missing coordinates using elapsed time
    keep spring-forward-unmatched target coordinates labeled missing
    label each aligned value as observed, interpolated, or missing
    record the observation mask and data-coverage report

    date_selected_block_start_dates =
        manifest block-start dates in ascending order, when supplied
        otherwise all starts of complete D-date blocks in ascending order
    eligible_blocks = []
    excluded_blocks = []
    for block_start_date in date_selected_block_start_dates:
        if D consecutive historical dates are unavailable from block_start_date:
            report block_start_date as excluded for incomplete date span
            continue
        block = the D consecutive historical dates from block_start_date
        block_data = extract all aligned observations for block

        if block_data has unresolved missing values:
            if joint_imputer is absent:
                report block_start_date as excluded for missing coverage,
                    including all unresolved historical coordinates
                continue
            mark block as requiring joint imputation
        else:
            mark block as complete

        add (block, block_data) to eligible_blocks

    if eligible_blocks is empty:
        raise ScenarioGenerationError with coverage and exclusion report

    q = normalize block_weights over eligible_blocks
         or assign equal weights when block_weights is absent
    if any eligible block_data has unresolved missing values:
        joint_imputer.fit(aligned_historical_data, random_source)
    candidates = []

    for block, block_data in eligible_blocks ordered by start date:
        if block_data has unresolved missing values:
            imputed_values = joint_imputer.impute_missing(
                imputation_request(block_data), random_source
            )
            merge imputed_values into missing coordinates of block_data
            if the imputer raises or returns invalid values or keys:
                raise ScenarioGenerationError with block and missing-value details

        profile = map block_data to target-grid boundaries by day position
        profile = FIFO-project each arc's full horizon arrival-time sequence
                   using options.fifo_projection_weights

        query post_horizon_rule at each final boundary and check that it matches
            the final travel-time value within the numeric tolerance
        validate finite nonnegative travel times, boundary coverage, and FIFO
        append (profile, q[block], block_id, group_memberships[block]) to candidates

    candidate_profile_count = number of candidates before coalescing
    coalesce identical complete profiles by summing their probabilities
    validate protected-group quotas against distinct candidate profiles and K
    if all candidates coalesce to one profile:
        assign probability 1 to that profile
        assign scenario ID 'scenario-000001'
        create its retained CandidateAssignment and ScenarioProbability
        set reduction_cost = 0.0
        generation_report = GenerationReport using the complete schema in §2.4
        staging_directory = create unique temporary sibling of output_directory
        write all bank files into staging_directory
        close files and validate the serialized bank in staging_directory
        publish staging_directory to output_directory under §2.3
        return GenerationResult(output_directory, generation_report)

    compute the scenario-distance matrix using §6.1 and
        scenario_distance_weights
    retained = all candidate scenarios

    while number of retained scenarios > K:
        for each removable candidate r in retained:
            proposed = retained without r
            if proposed violates a protected traffic-group requirement:
                continue
            deletion_cost[r] = cost(proposed), using all original candidates
        if no candidate is removable:
            raise ScenarioReductionError with protected-group requirements
        remove the candidate with the smallest deletion_cost
        resolve ties by candidate-block order

    assign each retained candidate to itself
    assign each removed candidate to its nearest retained scenario
    resolve distance ties by retained candidate order
    create one CandidateAssignment for each coalesced candidate
    set reduction_cost = cost(retained)
    assign each retained scenario the sum of its assigned candidate weights
    assign scenario identifiers in retained candidate order
    create ScenarioProbability records in output scenario order
    verify all scenario probabilities are positive and finite
    verify their sum differs from one by at most 1e-12
    normalize probabilities to sum to one
    generation_report = GenerationReport using the complete schema in §2.4
    staging_directory = create unique temporary sibling of output_directory
    write all bank files into staging_directory
    close files and validate the serialized bank in staging_directory
    publish staging_directory to output_directory under §2.3
    return GenerationResult(output_directory, generation_report)
```

The reduction starts from the empirical distribution of all eligible blocks,
with optional user weights. Candidate profiles are identical when their
values agree at every required boundary and their post-horizon rules agree.
`GenerationReport.reduction_cost` and `candidate_assignments` (§2.4) record the
final value of `cost(retained)`, each candidate's representative, and its
transferred probability mass.

## 8. Interpretation and limitations

The number `K` controls the size of the finite approximation. Its accuracy
depends on the historical coverage, traffic variability, imputation model,
distance weights, and objective statistics used by the application. A small
bank may omit rare but consequential traffic patterns. The user chooses `K` in
view of evaluation cost and desired precision. A later application study can
compare application-relevant statistics and decisions under the full
candidate bank and its reduction.

Whole-block construction and reduction preserve dependence present in the
aligned historical records. Rare joint conditions absent from the selected history require
supplemental stress scenarios or a calibrated traffic model. Joint imputation
adds assumptions for missing entries; its model, conditioning variables, and
uncertainty representation belong in the provenance report. If a future
traffic regime differs materially from the selected period, date filtering,
block weighting, or a calibrated traffic model represents that change.

The scenario bank is fixed before a consuming application evaluates decision
alternatives and is reused for each evaluation. Reproduction uses the input
files, effective seed, generation options, candidate-block ordering,
extension IDs and configuration IDs, the same Python runtime, and the same
extension implementations.
These provenance and outcome details are carried by the `GenerationReport`
fields defined in §2.4.

## References

1. J. Dupačová, N. Gröwe-Kuska, and W. Römisch (2003). “Scenario Reduction in
   Stochastic Programming: An Approach Using Probability Metrics.”
   *Mathematical Programming*, 95(3), 493–511.
   [https://doi.org/10.1007/s10107-002-0331-0](https://doi.org/10.1007/s10107-002-0331-0).
