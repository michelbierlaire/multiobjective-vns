# Multi-day VRP solution codec specification

## 1. Purpose and authority

This document fixes the application-specific `SolutionCodec` contract for the
multi-day VRP. The general callback, checkpoint, and failure contracts remain
authoritative in [`multiobjective-vns.md`](multiobjective-vns.md). The plan
fields and their relationships remain authoritative in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md), and the
problem semantics remain authoritative in [`multiday_vrp.md`](multiday_vrp.md).

The VRP codec represents each immutable `VrpSolution` as a versioned UTF-8
JSON byte string. This application-defined encoding is used by Pareto
snapshots and full checkpoints. The generic package stores the bytes without
interpreting the VRP solution.

## 2. Solution value covered by the codec

The codec's application solution type is `VrpSolution`. Its authoritative data
are the ordered `VehicleDayRoute` records and ordered `TransferStep` records
described in `multiday_vrp_solution_design.md` §4. Each route contains its
ordered stops and legs. Identifier values use the string field types in the
solution record contract; the evaluator checks their references against the
fixed VRP instance.

Every solution value is deeply immutable while it can be retained or exposed
by the search package. In the Python representation:

- `VrpSolution` and its nested record types are frozen value records;
- ordered fields such as routes, stops, legs, and transfer steps use tuples;
- set-valued fields such as `team_member_ids` use `frozenset`;
- identifiers use the UTF-8 string values defined by
  `multiday_vrp_input_format.md`;
- scalar values stored in the solution are immutable values.

Derived indexes, scenario schedules, evaluation results, and mutable caches
live outside the authoritative solution value. Derived views are rebuilt from
the restored plan and instance.

A semantically invalid plan can still be a `VrpSolution` when its records have
the declared field types and immutable collection forms. Such a value may
contain out-of-domain values, unresolved references, or inconsistent
relationships for the evaluator to assess. Public record constructors reject
values with an incorrect Python field type before search. The VRP
`SolutionIdentity` callback accepts every constructed `VrpSolution`, including
semantically invalid plans, and compares it by the canonical identity rule in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md) §4.6. It
raises `TypeError` for every other root type. The engine records a wrong root
type as an invocation-scoped identity failure. Codec capture receives values
only after identity resolution succeeds.

## 3. Codec identity and callable contract

The VRP codec registration uses these stable identifiers:

| Field | Value |
|---|---|
| `component_id` | `'multiobjective-vns:multiday-vrp:solution-codec'` |
| `configuration_id` | `'multiday-vrp-solution-json-v1'` |

Its concrete callback contract is:

```text
capture(solution: VrpSolution) -> bytes
restore(payload: bytes) -> VrpSolution
```

`capture` requires the exact runtime root type `VrpSolution` and raises
`TypeError` for another root type. `restore` accepts only bytes containing the
format defined below and raises `TypeError` or `ValueError` for another type,
unsupported version, or malformed content.

These concrete types specialize the package contract
`capture(solution: Solution) -> bytes` and
`restore(payload: bytes) -> Solution`. Capturing then restoring a value
preserves every authoritative field and its application-defined identity.

## 4. Capture procedure

For each `capture(solution)` call, the codec:

1. checks that `type(solution) is VrpSolution`;
2. encodes the fields in the version-1 JSON representation;
3. returns the UTF-8 encoding of the compact JSON object.

Capture preserves all authoritative values and their order, including route
presence, route and stop order, each leg's arc order, planned times and waits,
crew assignments, transfer-step order, transfer references, and values in a
semantically invalid plan. The application evaluator assesses representation
validity and operational feasibility; Operators perform plan changes; derived
views are rebuilt from the plan and instance.

The engine invokes capture for each Pareto member after a committed archive
admission and for each identity-resolved registry record during full
checkpoint preparation. The general capture timing, save trigger, and failure
effects are specified in `multiobjective-vns.md` §§8.1 and 8.3.

## 5. Restore procedure

For each `restore(payload)` call, the codec validates the UTF-8 JSON object,
checks the format name and version, then reconstructs the immutable record
tree from its tagged route-stop values. It validates field types through the
`VrpSolution` record constructors. Each stored float is represented by its
Python hexadecimal float string and restored with `float.fromhex`; this
preserves binary64 values and also round-trips `nan` and infinities in
semantically invalid plans.

Restore preserves the plan's field values, record kinds, order, and
application-defined identity. Evaluation records are stored separately by the
search checkpoint; the codec handles only the application solution value.

The engine invokes restore after checkpoint compatibility validation and
before a resumed invocation begins. A payload of another root type or an
exception from restore raises `CheckpointRestoreError` with a
`ComponentFailure` identifying this codec and phase `checkpoint_restore`, as
defined in `multiobjective-vns.md` §§5.11 and 8.1.

## 6. Checkpoint-store boundary

The payload supplied to `CheckpointStore` is a byte string. The codec's
version-1 JSON object has this shape:

```text
{
  "format": "multiobjective-vns-multiday-vrp-solution",
  "version": 1,
  "routes": [
    {
      "vehicle_id": str,
      "day_index": int,
      "overnight_depot_id": str,
      "start_time": hexadecimal float string,
      "driver_id": str,
      "stops": [tagged stop objects in route order],
      "legs": [[arc_id, ...] in route order]
    }
  ],
  "transfer_steps": [
    {"package_id": str, "step_number": int, "facility_id": str}
  ]
}
```

An activity stop has `kind="activity"`, an `activity_ref` object with
`kind` and `activity_id`, sorted `team_member_ids`, `pre_service_wait`,
`wait`, `break_duration`, and `tour_end`. A transfer-operation stop has
`kind="transfer_operation"`, a `transfer_step_ref` object with `package_id`
and `step_number`, `role`, the three timing values, and `tour_end`. A depot
stop has `kind="depot_only"`, `node_id`, the three timing values, and
`tour_end`. Every real-valued timing field is encoded as a hexadecimal float
string. Object keys use the names above; arrays preserve the corresponding
tuple order, and set-valued team IDs use sorted arrays.

The store preserves the bytes unchanged. The application codec restores their
plan meaning. Pareto snapshot files wrap the bytes in base64 and include the
codec configuration ID so a changed encoding is rejected during warm-start
loading.

The generic package treats the solution bytes as opaque in both in-memory and
durable checkpoints. The codec reconstructs a new immutable `VrpSolution`
value when restoration is requested.

If snapshot or checkpoint publication fails, the engine applies the
corresponding general persistence failure rule. A malformed encoded solution
raises a codec restoration diagnostic under the Pareto-snapshot or checkpoint
restore contract, depending on the loading operation.

## 7. Identity and resume compatibility

`SolutionCodec` preserves solution values; `SolutionIdentity` decides whether
two submitted values represent the same VRP plan. The codec preserves every
value that the identity callback, evaluator, or Operators may inspect.
`SolutionIdentity.equivalent` uses the canonical identity rule in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md) §4.6. A
configured lookup key follows that same equivalence rule.

The built-in `VrpSolutionIdentity` registration uses
`component_id = 'multiobjective-vns:multiday-vrp:solution-identity'` and
`configuration_id = 'multiday-vrp-canonical-pickup-identity-v4'`. It compares
the canonical forms defined in solution-design §4.6, using the
`PickupSymmetryGroup` declarations bound to its `LoadedVrpInstance`. All other
authoritative fields use the exact equality rules in that section. It accepts
every type-correct `VrpSolution`, including a semantically invalid plan, and
raises `TypeError` for another root type. These IDs are the registrations
assembled by `build_vrp_application` and `load_vrp_application`.

The identity object is bound to the validated instance:

```text
VrpSolutionIdentity(instance: LoadedVrpInstance)

equivalent(left: Solution, right: Solution) -> bool
```

The constructor captures `instance.pickup_symmetry_groups`. The comparison
uses the canonical pickup sub-blocks in solution-design §4.6 and the exact value
rules for every remaining field.

Resume requires exact matches for the codec's `component_id` and
`configuration_id`. The configuration identifier names the version-1 JSON
`VrpSolution` payload contract. A change to payload interpretation, the
solution record model, or the meaning of stored fields requires a new
`configuration_id`; a checkpoint created under another identifier raises
`ResumeCompatibilityError` and the application starts a fresh search. The
`SolutionIdentity` registration has its own `configuration_id`, which names
the canonical identity rule and any configured lookup-key behavior. A change
to either requires a new identity `configuration_id`, so a resumed search
always applies the same duplicate-resolution rule as the search that created
its checkpoint. Group membership is part of the instance data identified by
`problem_instance_id`; changing a group's members requires a new
`instance_id`.

The VRP identity registration declares v2-to-v4 and v3-to-v4
pickup-identity configuration aliases for promoted composition catalogs. A
catalog created with either prior identity configuration is accepted only when
replacing that configuration ID everywhere in its compatibility record
produces an exact match for the current v4 declarations; its component
configuration IDs are rewritten in memory before promotions are restored.
Checkpoint resume remains exact and uses the identity configuration recorded
by the checkpoint.

Any future payload migration requires an explicitly designed compatibility
contract and a new codec specification.

## 8. Implementation conformance checklist

An implementation conforms to this specification when:

1. its codec exposes the identifiers and callback signatures in §3;
2. every captured and restored application solution is a deeply immutable
   `VrpSolution` value;
3. `capture` returns bytes in the version-1 JSON format and `restore` rebuilds
   an equivalent immutable `VrpSolution` value;
4. semantically invalid, type-correct `VrpSolution` values retain their
   complete data through checkpoint capture and restore;
5. the configured checkpoint store preserves the codec bytes unchanged;
6. codec component and configuration identifiers participate in the
   architecture's exact resume compatibility check.
