# Pareto set viewer

**Status:** Version 2 design

**Scope:** Standalone graphical inspection of a saved multiobjective VNS search

## 1. Purpose

The Pareto set viewer is a standalone graphical tool for inspecting the
solutions recorded by a multiobjective VNS invocation. It displays trade-offs
among three user-selected objectives and distinguishes current Pareto members,
solutions that previously belonged to the Pareto set, distinct plans tied in
all objective values with a current Pareto representative, and other visited
solutions.

The viewer reads a versioned JSON export. The export contains objective labels,
formatted values, normalized order positions, solution statuses, and optional
human-readable descriptions. These generic fields let the viewer work with
any application supported by the generic package.

The viewer follows one saved or running invocation. It refreshes the view when
the algorithm atomically publishes a newer export, and a user can load another
export at any time to inspect a different invocation.

## 2. User interaction

The viewer opens with a two-dimensional scatter plot and an objective
selector.
Each objective option shows its human-readable label and stable objective ID.
The user chooses three distinct objectives in order:

1. The first objective controls the horizontal coordinate.
2. The second objective controls the vertical coordinate.
3. The third objective controls marker area.

When the file contains at least three objectives with usable values, the
default selection is the first three objectives in declaration order. The user
can change each selection independently. When fewer than three objectives
have usable values, the viewer explains which selections are available and
shows the records in the details panel.

Hovering over a point displays its solution ID, status, and values for the
three selected objectives. Selecting a point displays its solution ID,
processing and archive statuses, optional description, and formatted value
for every objective. The details panel also supports selecting a solution
when several records share the same plotted coordinates.

## 3. Input file and discovery

### 3.1 File selection

The input is a UTF-8 JSON view export following the schema in §4. The generic
package uses one of two invocation-specific filenames:

```text
<invocation-id>.registry-view.json
<invocation-id>.pareto-view.json
```

The registry view contains all distinct visited solution records. The
Pareto-only view contains current Pareto-set members, with no two members
having objective vectors that compare equal in every coordinate. Distinct
plans tied on every objective remain visible in the registry view and are
marked as objective-vector duplicates. These presentation exports carry
objective values and display coordinates. A warm-start Pareto snapshot uses
its own solution-payload contract.

On launch, the viewer applies this path precedence:

1. A file path supplied on the command line or through an explicit Open
   action.
2. Registry or Pareto-view exports in the current working directory. When
   the directory contains exports for one invocation, the viewer uses the
   registry scope whenever it is available and otherwise uses the Pareto
   scope.
3. A file chooser when the directory contains exports for multiple
   invocations, or when no view export is present.

An explicitly supplied path identifies the invocation to inspect and takes
precedence over automatic discovery. The viewer then applies the registry-
first source rule for that invocation. It displays a clear error and offers
file selection again when a chosen file cannot be read, parsed, or validated.

The generic package writes view exports into the invocation's `log_directory`
using the invocation-specific filenames above. The default publication
schedule writes the Pareto scope after every committed archive admission, then
writes both registry and Pareto scopes from the final committed state before
the invocation stop event. The registry file therefore provides a complete
final inventory, while the Pareto file supports live monitoring. A user can
launch the viewer with that directory as its working directory or open an
exported path directly.

### 3.2 Export contents

The generic package creates each export from one committed `SearchState`.
Both scopes include the invocation ID, the commit sequence represented by the
file, and whether that state is final. Each distinct solution appears once;
repeated proposal occurrences remain in the invocation outcomes and event
log.

A registry-scope export includes every identity-resolved `SolutionRecord` in
registry insertion order, including application assessments of `valid`,
`invalid`, or `infeasible`, and records with processing status `failed`. A
Pareto-scope export includes only current Pareto archive members. When both
scopes are available for the same committed state, the registry export offers
the broader view and includes those archive members as well.

For each solution, the export includes its stable textual `SolutionId`,
processing status, application assessment when available, archive status when
available, objective values when available, and an optional description from
`SolutionFormatter`. The package obtains objective labels and display values
from the corresponding `ObjectiveFunction` contracts. Each objective is
represented by formatted text and a normalized position.

Every objective position is calculated across all usable values for that
objective among the records in that export. Each file carries a
self-contained scale for its scope.

## 4. JSON export schema

The top-level JSON object has these fields:

| Field | Type | Meaning |
|---|---|---|
| `format` | string | The literal `multiobjective-vns-pareto-view` |
| `format_version` | positive integer | The export schema version; version 2 uses value `2`. The reader continues to accept version 1 exports. |
| `invocation_id` | stable identifier text | Invocation that produced the export |
| `problem_instance_id` | stable identifier text | Problem instance used by the invocation |
| `record_scope` | string | `registry` or `pareto` |
| `commit_sequence` | nonnegative integer | Latest committed search transition represented by the export |
| `is_final` | Boolean | Whether the export represents the invocation's final state |
| `objectives` | array of objective records | Objective definitions in declaration order |
| `solutions` | array of solution records | Distinct records in registry order for `registry` scope, or archive order for `pareto` scope |

Each objective record has:

| Field | Type | Meaning |
|---|---|---|
| `objective_id` | stable identifier text | Stable objective identity |
| `label` | nonempty string | Human-readable name from `ObjectiveFunction.__str__()` |
| `direction` | string | The literal `minimize` |

Each solution record has:

| Field | Type | Meaning |
|---|---|---|
| `solution_id` | stable identifier text | Identity of the distinct registered solution |
| `processing_status` | string | `completed` or `failed` |
| `assessment` | string or `null` | `valid`, `invalid`, `infeasible`, or `null` when unavailable |
| `archive_status` | string or `null` | `pareto_optimal`, `dominated`, `objective_vector_duplicate`, `rejected`, or `null` when no archive classification applies |
| `objective_values` | array | One objective-value record per objective, in declaration order |
| `description` | string or `null` | Optional user-provided human-readable solution description |

The export spells the generic archive statuses `Pareto optimal`, `Dominated`,
`Objective-vector duplicate`, and `Rejected` as `pareto_optimal`, `dominated`,
`objective_vector_duplicate`, and `rejected` respectively. Version 1 exports
use the previous status vocabulary and remain readable.

Each entry in `objective_values` has:

| Field | Type | Meaning |
|---|---|---|
| `objective_id` | stable identifier text | Objective corresponding to this coordinate |
| `display_value` | string or `null` | Formatted objective value; `null` when no usable value exists |
| `normalized_position` | finite number in `[0, 1]` or `null` | Order-preserving position used by the viewer; `null` when no usable value exists |

Objective-value entries use the same order as the top-level `objectives`
array. Their IDs allow consumers to verify that correspondence. A registry-
scope solution with unavailable objective values remains in `solutions`; the
viewer presents it in the unplottable-records list with its status and
description. Every Pareto-scope record has a usable value for each declared
objective and `archive_status=pareto_optimal`.

## 5. Objective coordinates and display scale

The generic package permits application-defined objective values. For the
viewer, each `ObjectiveFunction.compare()` supplies a deterministic total
order, and `ObjectiveFunction.format()` supplies readable value text. The
exporter uses the order to assign normalized coordinates and exports those
coordinates with their formatted labels.

For each objective, the exporter sorts the distinct usable values according
to that objective's comparator. Values that compare as `equal` share one
position. With `r` distinct ordered values, value at zero-based rank `i` gets
position `i / (r - 1)` when `r > 1`. A sole distinct value gets position
`0.5`. Thus the smallest value is at `0`, the largest is at `1`, and every
intermediate value preserves its exact rank order.

The plot uses 100 evenly spaced intervals as a display grid. Points use their
continuous rank positions, including positions between grid ticks. Major ticks
occur at the endpoints and every tenth interval. The viewer assigns each
observed objective value to the major tick closest to its rank position. When
multiple values map to one tick, that tick shows the value whose rank position
is closest to it, with ties resolved toward the lower rank. This places each
displayed value near its plotted position and shows each rank value at most
once. The endpoint ticks always show the best and worst observed values. Some
major ticks may have no label. With one distinct observed value, the viewer
labels its position with that value. Axis labels use formatted objective
values from the current export; the selected-solution panel and hover details
also show the formatted original objective values.

The displayed axis values are the original formatted objective values; no
normalized objective values are requested for presentation. Normalized rank
positions serve only as internal layout coordinates for arbitrary totally
ordered objective values. The axis title names the selected objective, and
axis labels show its formatted observed values. Point spacing follows rank,
so the layout communicates order rather than differences in the original
values' magnitudes.

For the third objective, the same internal rank position controls marker area.
The renderer maps position `p` to `A_min + p * (A_max - A_min)`, where
`A_min` and `A_max` are positive minimum and maximum readable marker areas,
with `A_min < A_max`. Larger rank positions produce larger marker areas. The
size legend labels representative rank positions with formatted observed
objective values, using the same endpoint and duplicate-label rules as the
axes. The selected-solution panel always shows the exact formatted value.

Under the package's minimization convention, internal position `0` represents
the best observed value and position `1` the worst. When an export changes,
its set of distinct objective values can change, and the internal rank
positions are calculated from the updated set. Positions express relative
order within the loaded export; visible tick labels remain formatted values.

## 6. Point appearance and status

Shape, outline, and size distinguish records. Color supports recognition, with
red used for current Pareto members and neutral high-contrast styling used for
other categories. Every category remains distinguishable in grayscale.

When multiple status fields apply to a record, marker classification follows
this precedence: current Pareto member, previously Pareto and rejected,
objective-vector duplicate, processing failure, application-invalid,
application-infeasible, then other completed record.

| Record category | Marker |
|---|---|
| Current Pareto member (`archive_status=pareto_optimal`) | Filled circle with a strong contrasting outline and red accent fill |
| Previously Pareto, now rejected (`archive_status=rejected`) | Cross (`×`) |
| Distinct plan tied with a current Pareto representative on every objective (`archive_status=objective_vector_duplicate`) | Outlined diamond (`◇`) |
| Other completed, application-valid solution with usable objective values | Circle with neutral high-contrast styling |
| Infeasible solution with usable objective values | Outlined triangle with a centered dot |
| Invalid or failed solution with usable objective values | Broken-outline marker, with status text available on selection |
| Record missing a selected objective coordinate | Row in the unplottable-records list |

Records missing any selected coordinate appear in the unplottable list and
retain their status marker in that list.

The area of every marker shape encodes the third selected objective according
to §5. Status styling uses shape, outline, and fill treatment, so status
remains visually distinct from the objective scale. A legend identifies
every marker category. The Pareto circle's outline and accent fill make it
prominent while its area continues to represent the third objective.

Solutions with equal values on the selected objectives occupy identical plot
coordinates. The viewer preserves those coordinates and exposes overlapping
solutions through a count indicator and selectable details. Coincident records
appear in a stack or selection list at their shared coordinates.

## 7. Details and accessibility

The plot supports point selection and hover inspection. A selected point shows:

- solution ID;
- processing status, application assessment, and archive status;
- all objective labels and formatted values;
- the optional solution description.

The legend and status distinctions use text, shape, and outline as primary
cues. Color is supplementary. Axis titles include the selected objective's
human-readable name, and tick labels show formatted objective values. A brief
plot note explains that point spacing follows rank within the loaded export.

## 8. Export and error behavior

The algorithm owns export scope and publication timing. Each published file
represents one committed `SearchState` and is atomically replaced as a complete
JSON document. Its `commit_sequence` identifies that state, and `is_final`
indicates whether the invocation has stopped. The viewer labels the displayed
revision and final/running state so users can see how current the view is.

While an invocation is active, the viewer monitors its available exports. It
reloads the selected scope when a newer version is published and switches to
registry scope when that file becomes available. It displays the
`commit_sequence` of the selected export. When an invocation has only a Pareto
view available, the viewer displays those archive members alone. The default
run publishes the Pareto scope after each committed archive admission and
publishes both scopes with `is_final=true` before emitting
`invocation_stopped`. A custom algorithm may choose another schedule while
preserving atomic publication and final-state identification.

`RunResult.registry_view_path` and `RunResult.pareto_view_path` are each a
`Path | None`, identifying the latest successful export of their respective
scope. A missing path means that the algorithm did not publish that scope
during the invocation. An export failure is reported through the invocation's
reporting diagnostics and component-failure rules; the committed search state
remains available through `RunResult.search_state`.

The viewer validates the format name, version, required fields, objective
references, array lengths, status values, and numeric coordinate range before
rendering. A validation error identifies the offending field and permits the
user to select another file. Unsupported future format versions produce a
clear compatibility message.

Objective-value formatting failures use `[unformatted]` as the displayed text
and retain the usable normalized coordinate. A solution-description failure
produces a `null` description and a reporting diagnostic in the invocation
result; its solution ID remains available for display.

## 9. Implementation boundary

The viewer is an independent local GUI over the versioned export contract.
Its rendering framework, executable packaging, and visual styling details are
implementation choices. The export schema, file discovery precedence,
objective ranking, formatted-value axis labels, marker semantics, and
result-path contract in this document define the stable behavior expected by
both the generic package and the GUI.
