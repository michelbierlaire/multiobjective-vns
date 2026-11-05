# Multi-day VRP solution codec specification

## 1. Purpose and authority

This document fixes the application-specific `SolutionCodec` contract for the
multi-day VRP. The general callback, checkpoint, and failure contracts remain
authoritative in [`multiobjective-vns.md`](multiobjective-vns.md). The plan
fields and their relationships remain authoritative in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md), and the
problem semantics remain authoritative in [`multiday_vrp.md`](multiday_vrp.md).

The VRP codec uses the immutable `VrpSolution` value itself as its
`CodecPayload`. Capture and restore preserve that value unchanged. The
`CheckpointStore` owns durable persistence of the opaque payload, as specified
by the general architecture.

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
`SolutionIdentity` callback accepts every constructed `VrpSolution` by its
exact structural identity, including semantically invalid plans, and raises
`TypeError` for every other root type. The engine records a wrong root type as
an invocation-scoped identity failure. Codec capture receives values only
after identity resolution succeeds.

## 3. Codec identity and callable contract

The VRP codec registration uses these stable identifiers:

| Field | Value |
|---|---|
| `component_id` | `'multiobjective-vns:multiday-vrp:solution-codec'` |
| `configuration_id` | `'multiday-vrp-solution-object-v1'` |

Its concrete callback contract is:

```text
capture(solution: VrpSolution) -> VrpSolution
restore(payload: VrpSolution) -> VrpSolution
```

Both callbacks require the exact runtime root type `VrpSolution`. They raise
`TypeError` when their argument has another root type.

These concrete types specialize the package contract
`capture(solution: Solution) -> CodecPayload` and
`restore(payload: CodecPayload) -> Solution`. The captured payload is the
same object as the input solution; restore returns the payload object
unchanged. In-process, both relationships are object identity:

```text
codec.capture(solution) is solution
codec.restore(payload) is payload
```

## 4. Capture procedure

For each `capture(solution)` call, the codec:

1. checks that `type(solution) is VrpSolution`;
2. returns that exact immutable value as the payload.

Capture preserves all authoritative values and their order, including route
presence, route and stop order, each leg's arc order, planned times and waits,
crew assignments, transfer-step order, transfer references, and values in a
semantically invalid plan. The application evaluator assesses representation
validity and operational feasibility; Operators perform plan changes; derived
views are rebuilt from the plan and instance.

The engine invokes capture during checkpoint preparation, once for each
identity-resolved registry record, in registry insertion order. The payload
table is keyed by `SolutionId`; proposal occurrences that resolve to an
existing record reuse that record's payload entry. The general checkpoint
contract in `multiobjective-vns.md` §8.1 defines this timing and the effect of
a capture failure.

## 5. Restore procedure

For each `restore(payload)` call, the codec:

1. checks that `type(payload) is VrpSolution`;
2. returns that exact immutable value unchanged.

Restore preserves the plan's field values, record kinds, order, and
application-defined identity. Evaluation records are restored by the search
checkpoint alongside the solution payloads; the codec handles the application
solution value.

The engine invokes restore after checkpoint compatibility validation and
before a resumed invocation begins. A payload of another root type or an
exception from restore raises `CheckpointRestoreError` with a
`ComponentFailure` identifying this codec and phase `checkpoint_restore`, as
defined in `multiobjective-vns.md` §§5.11 and 8.1.

## 6. Checkpoint-store boundary

The payload supplied to `CheckpointStore` is a `VrpSolution` object. A store
must preserve its complete immutable value graph, including tuple ordering,
set membership, record variants, identifiers, and scalar values. Real-valued
fields retain their exact binary64 values so solution identity remains stable
across restoration. After a
load, the codec receives a `VrpSolution` with the same plan meaning and
application identity. The store selects and implements its persistence
mechanism; the codec contract is independent of that mechanism.

An in-memory checkpoint can retain the live `VrpSolution` object directly. A
durable store may reconstruct an equivalent immutable object graph while
loading the payload. In either case, the codec returns the payload value
unchanged.

If `CheckpointStore.save` fails while preserving the payload, the engine
reports the store failure under the general `checkpoint_save` rules. If a
loaded payload cannot be supplied as a `VrpSolution`, restore fails under the
`CheckpointRestoreError` rule above.

## 7. Identity and resume compatibility

`SolutionCodec` preserves solution values; `SolutionIdentity` decides whether
two submitted values represent the same VRP plan. The codec preserves every
value that the identity callback, evaluator, or Operators may inspect.
`SolutionIdentity.equivalent` uses the exact structural equality rule in
[`multiday_vrp_solution_design.md`](multiday_vrp_solution_design.md) §4.6. A
configured lookup key follows that same equivalence rule.

The built-in `VrpSolutionIdentity` registration uses
`component_id = 'multiobjective-vns:multiday-vrp:solution-identity'` and
`configuration_id = 'multiday-vrp-structural-v1'`. Its comparison rule is
structural equality over every authoritative `VrpSolution` field: ordered
routes, stops, legs, arc sequences, and transfer steps compare in order;
crew-member sets compare by membership; corresponding NaN float fields compare
equal; and all other scalar values use exact equality. It accepts every
type-correct `VrpSolution`, including a semantically invalid plan, and raises
`TypeError` for another root type. These IDs are the registrations assembled
by `build_vrp_application` and `load_vrp_application`.

Resume requires exact matches for the codec's `component_id` and
`configuration_id`. The configuration identifier names the v1 immutable
`VrpSolution` payload contract. A change to payload interpretation, the
solution record model, or the meaning of stored fields requires a new
`configuration_id`; a checkpoint created under another identifier raises
`ResumeCompatibilityError` and the application starts a fresh search. The
`SolutionIdentity` registration has its own `configuration_id`, which names
the structural equality rule and any configured lookup-key behavior. A change
to either requires a new identity `configuration_id`, so a resumed search
always applies the same duplicate-resolution rule as the search that created
its checkpoint.

Any future payload migration requires an explicitly designed compatibility
contract and a new codec specification.

## 8. Implementation conformance checklist

An implementation conforms to this specification when:

1. its codec exposes the identifiers and callback signatures in §3;
2. every captured and restored application solution is a deeply immutable
   `VrpSolution` value;
3. `capture` and `restore` return the exact object supplied to each callback;
4. semantically invalid, type-correct `VrpSolution` values retain their
   complete data through checkpoint capture and restore;
5. the configured checkpoint store preserves the payload graph and its plan
   meaning;
6. codec component and configuration identifiers participate in the
   architecture's exact resume compatibility check.
