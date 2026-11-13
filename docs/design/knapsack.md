# Multiobjective 0–1 knapsack: formal model and design example

This document defines a multiobjective 0–1 knapsack problem and maps the
objects in the [multiobjective VNS architecture](multiobjective-vns.md) to
that problem. The worked example checks whether the architecture gives each
object enough information and a clear responsibility.

The callable signatures and shared record schemas follow architecture §6.6;
this document supplies the knapsack-specific values and examples.

## 1. Formal problem

This is a 0–1 multiobjective multidimensional knapsack model. The general
`p`-objective formulation follows the multiobjective knapsack model studied by
Bazgan, Hugot, and Vanderpooten [1]. The multiple-resource version is also
known as the multiobjective multiconstraint knapsack problem; Florios,
Mavrotas, and Diakoulaki [2] study a three-objective, three-constraint case.
This document writes the model for general `p >= 2` and `m >= 1`.

Let there be `n` items, `m` resource constraints, and `p` objectives, with
`n >= 1`, `m >= 1`, and `p >= 2`. For each item `i`, resource `j`, and
objective `r`, let:

- `a[j, i] >= 0` be the amount of resource `j` consumed by item `i`;
- `b[j] >= 0` be the available capacity of resource `j`;
- `c[r, i]` be item `i`'s contribution to objective `r`.

All resource amounts, capacities, and objective contributions are finite real
numbers. Feasibility uses the stated capacity inequalities. The worked
knapsack objective functions compare the negated numeric values by exact
ascending real-number order, with zero tolerance, as required by the package's
minimization convention.

The decision vector is `x = (x[1], ..., x[n])`, where each `x[i]` is binary:
`1` means item `i` is selected and `0` means it is excluded. The feasible set is

```text
X = { x in {0, 1}^n : sum_i a[j, i] x[i] <= b[j] for every resource j }
```

The multiobjective knapsack problem maximizes all `p` objective functions:

```text
z[r](x) = sum_i c[r, i] x[i],   for r = 1, ..., p
```

Define `y` to dominate `x` when `z[r](y) >= z[r](x)` for every objective `r`,
with strict inequality for at least one objective. A feasible solution is
Pareto-optimal when it is maximal under this dominance relation within `X`.
Two distinct item selections may be mathematically Pareto-optimal with equal
objective vectors. The generic algorithm's Pareto set keeps one representative
per objective vector; every distinct visited selection remains in the
`SolutionRegistry`.

The package treats every objective as a minimization objective. Because this
model maximizes profit, the application negates each profit sum and supplies
the ordered vector

```text
f(x) = (-z[1](x), ..., -z[p](x))
```

to the package. This preserves the Pareto trade-offs. The application is
responsible for providing objective values with the correct sign. Each
`ObjectiveFunction` compares two negated profit values, returns `less`,
`equal`, or `greater`, gives its objective name through `__str__()`, and
formats a value for reports. For example, the first function can be displayed
as `negative profit 1`. The item order, resource data, capacities, objective
meanings and coefficients, comparison rules, and evaluation rules define the
problem instance shared by the evaluator and operators. For a general
instance, the application supplies one ordered `ObjectiveFunction` for each
objective `(profit_1, ..., profit_p)`. The TOML input example in §1.1 uses the
published P410 instance with ten items, four resource constraints, and two
objectives [3]. Section 3.1 uses a separate four-item teaching instance for
readable solution and Operator examples. Their example identifiers are
`p410-literature-v1` and `knapsack-four-item-example-v1`, respectively.
Checkpoint comparison follows the contract in the algorithm design.

### 1.1 Problem-instance input file

The knapsack subpackage uses one UTF-8 TOML file (with a `.toml` extension)
for the fixed data of a problem instance. The file describes the items,
resource constraints, and objective contributions. This example uses P410, a
published bi-objective multidimensional knapsack instance reproduced in [3],
which attributes it to Gomes da Silva et al. [4]. Each of its four resources
is a separate capacity constraint, and a feasible selection must satisfy all
four. The complete P410 example, including a runnable script and its input, is
available in [`examples/knapsack/p410/`](../../examples/knapsack/p410/).
Version 1 has the following top-level fields:

| Field | Contents |
|---|---|
| `format_version` | Integer `1`. |
| `problem_instance_id` | Nonempty, application-chosen stable string. |
| `items` | Ordered list of unique, nonempty item names; position defines each solution bit. |
| `resources` | Ordered array of resource tables, each with `id`, `capacity`, and item-aligned `consumption` values. |
| `objectives` | Ordered array of objective tables, each with `id`, display `name`, and item-aligned `contribution` values. |

Search-control settings are supplied separately through
`MultiobjectiveVNSConfig` when the application constructs the generic search.
This configuration includes `composition_selection_probability`, which is
recorded in the invocation log. The generic algorithm design defines its
default and two-stage selection semantics.

The top-level fields are required. `format_version` is the integer `1`.
`problem_instance_id` is a nonempty, application-chosen stable string. `items`
is a nonempty ordered list of unique, nonempty strings; its order defines the
bit positions in every solution vector. Each resource is an independent
capacity limit applied to the selected items. `resources` is a nonempty ordered
list with unique nonempty `id` strings. Each capacity is nonnegative, and each
resource's `consumption` list has exactly one value per item, in `items` order.
`objectives` is an ordered list of at least two entries with unique nonempty
`id` strings and nonempty display `name` strings. Each `contribution` list has
exactly one value per item, also in `items` order. The objective list order is
the objective-vector order used by `ObjectiveDeclaration`.

Capacities and resource consumptions are nonnegative TOML numbers. Objective
contributions are TOML numbers and may be negative. A numeric field accepts an
integer or finite floating-point value; Boolean values, `NaN`, infinity, and
values of other TOML types are invalid. Resource and objective arrays must
have the dimensions specified in §1, and unknown fields are rejected in
format version 1 so misspelled names are reported as input errors. The loader
converts accepted numeric fields to finite Python `float` values and reports an
input error when conversion overflows or produces a non-finite value.

Every objective in this format is a maximized contribution. The application
builds one `ObjectiveFunction` per entry in file order and returns the
negatives of the selected-item contribution sums to the core package, as
defined in §1. The `problem_instance_id` must change when item order, resource
data, capacities, objective order or meaning, contributions, or evaluation
semantics change; resume compatibility follows the architecture contract.

The file contains fixed problem data only. Initial solution vectors, random
seed, Operator catalog, and search budgets are supplied separately when a
search is created or run. The application may prepare those vectors using the
item order declared in this file.

## 2. Knapsack solution and application contracts

For one fixed problem instance, each solution is an immutable Python tuple.
Well-formed vectors have the Python type `tuple[int, ...]`, contain exactly
`n` entries, and use only exact built-in integers `0` and `1` (`bool` entries
are excluded). For diagnosis, identity and codec callbacks also accept tuples
whose entries are built-in integers or Booleans. A wrong-length tuple, a
Boolean entry, or an integer outside `{0,1}` is a supported malformed
representation and receives an `invalid` application assessment. Identity
compares entry types as well as values, so `(True,)` and `(1,)` are distinct.
Non-tuples and entries of other Python types are outside the knapsack solution
interface and produce a component diagnostic at the callback boundary.
Operators return new tuples when they change a solution. This representation
follows the ownership contract in architecture §5.1.

A representation-valid solution is feasible when all resource-capacity
inequalities hold. Its objective values are the `p` sums above, returned in
the order declared by `ObjectiveDeclaration` after negation.

The application defines solution equivalence by exact equality of the submitted
vectors, including their lengths and entries even when a vector is malformed.
The lookup hint represents that full submitted vector; the identity rule
remains the equality test, and the hint only narrows lookup. This lets identity
be resolved before representation validation, so a repeated malformed vector
can reuse its first evaluation result.

One `ApplicationEvaluator` checks the vector's representation, determines
whether its resource use is feasible, and computes the ordered objective
vector. It returns those three findings separately in an `ApplicationEvaluation`.
A malformed vector is reported as invalid; a well-formed vector over capacity
is reported as infeasible; a well-formed, feasible vector with usable objective
values is valid.

The evaluator records this application finding as `invalid`; the outcome has
`ProcessingStatus` `completed` and assessment `invalid`. A `ComponentFailure`
records callback exceptions, infrastructure failures, and component contract
violations under architecture §5.11. Feasibility and objective findings may be
unavailable for a malformed vector.

For a user-supplied initial vector, `invalid` is the application finding. If a
standalone Operator or the final Operator in a composition returns a malformed
vector, the evaluator records the same `invalid` finding and the engine also
records an Operator contract failure under architecture §5.9. In a composition,
the first Operator's intermediate vector remains internal to the composition
and is passed to the second Operator as its base. It receives no application
assessment, identity, registry record, or candidate outcome. The evaluator
processes the composition's final vector, which is checked under the same
Operator contract as a standalone result.

The same identity rule applies to malformed vectors. A first submission of
`(0,2,0,0)` receives a `SolutionId` and registry record before the evaluator
reports it as invalid. Its outcome has status `completed`, assessment `invalid`,
and disposition `registered`. A later submission of that exact vector has the
same `SolutionId`. Its duplicate outcome reuses the stored processing status,
assessment, and evaluation diagnostics, and carries the current occurrence's
proposal metadata and `duplicate` disposition. It triggers no new evaluation
or Operator-guarantee check.
The knapsack `SolutionCodec` encodes each integer tuple as UTF-8 JSON bytes.
The JSON value is an array whose entries preserve integer versus Boolean
values. `restore()` reconstructs the tuple, including malformed tuple length,
Boolean entries, and nonbinary integer entries. Capture and restore preserve
the solution value; snapshot and checkpoint timing follow architecture
§§8.1 and 8.3.

The empty selection `(0, ..., 0)` is well-formed and consumes zero of every
resource. With nonnegative capacities, it is feasible, and the objective
formulas yield the usable vector `(0, ..., 0)`. It is therefore one example of
a valid, feasible initial solution. At least one solution in the user-provided
initial set must be well-formed, feasible, and evaluable for every declared
objective.

## 3. Architecture concepts in this problem

### 3.1 A compact four-item teaching instance

Use four items, two capacity constraints, and two maximization objectives
(`p=2`). Capacities are `(4, 4)`; each resource is an independent limit on
the selected items. The item columns list consumption of each resource,
followed by contributions to `profit_1` and `profit_2`:

The objective vectors below follow the declaration and sign convention in §1.

| Item | Resource 1 | Resource 2 | Objective 1 | Objective 2 |
|---|---:|---:|---:|---:|
| 1 | 2 | 2 | 8 | 2 |
| 2 | 2 | 1 | 5 | 6 |
| 3 | 1 | 2 | 4 | 4 |
| 4 | 3 | 3 | 9 | 10 |

The examples use named vectors for readability; actual `SolutionId` values
remain opaque.

| Name | Vector `(x1,x2,x3,x4)` | Resource use | Profits `(z1,z2)` | Package objective vector |
|---|---|---|---|---|
| `b` | `(0,1,1,0)` | `(3,3)` | `(9,10)` | `(-9,-10)` |
| `c` | `(1,1,0,0)` | `(4,3)` | `(13,8)` | `(-13,-8)` |
| `a` | `(1,0,1,0)` | `(3,4)` | `(12,6)` | `(-12,-6)` |
| `d` | `(0,0,0,1)` | `(3,3)` | `(9,10)` | `(-9,-10)` |
| `e` | `(1,1,1,0)` | `(5,5)` | `(17,12)` | `(-17,-12)` |

`e` is representation-valid because it is a four-entry binary vector. Its
resource use makes it infeasible, while its evaluator still returns the
objective vector `(-17,-12)`.

The vectors illustrate distinct architecture concepts. `b` and `d` have equal
objective vectors but different item selections, so the identity rule assigns
them distinct `SolutionId` values. `a` is dominated by `c`, while `b`, `c`,
and `d` have objective vectors maximal under dominance among the known
feasible examples. Thus, an archive over known solutions can
contain `b`, `c`, and `d`; the model-level Pareto set is defined over all of
`X`. For example, selecting items 3 and 4 gives a feasible vector with profits
`(13,14)` that dominates all three, illustrating how a model-level Pareto
solution may remain undiscovered by a search.

The catalog below gives concrete operator examples. The worked calls illustrate
their contracts; the following sections map them to the shared architecture.

### 3.2 Operator catalog

The item order in the instance defines positions `1` through `n`. Each call
builds proposals independently from the same base. It samples at most
`CandidateContext.max_candidates` distinct move patterns, applies each pattern
to a fresh working copy, filters results under the Operator's feasibility
rule, and skips unchanged results and results equivalent to an earlier
proposal in that call. It returns qualifying proposals in sampled order, up to
the same limit. An empty candidate list is represented by
`OperatorResult.candidates=()`.
Consequently, feasibility filtering or repeated solution identities can make
the returned list shorter than the limit. The shared `RandomSource` defines
the sample order. Each proposal carries its own `m`; `k` limits the move units
used to construct that proposal. The engine processes all returned proposals
in order and records one `CandidateOutcome` per proposal.

For example, with base `c`, `k=1`, and `max_candidates=2`, `flip_items` can
return `(0,1,0,0)` and `(1,0,0,0)` as two proposals, each with `m=1`; the
shared random source determines their order.

For `flip_items`, `add_items`, `remove_items`, `exchange_items`, and
`capacity_repair`, each move pattern first chooses a length uniformly from one
through `min(k, eligible move count)`, then samples that many elementary
changes in random order without replacement. A call samples at most
`CandidateContext.max_candidates` such patterns without replacement. Each
pattern starts from the same base; changes within a pattern are applied in
sampled order. `exchange_items` forms each pattern from disjoint selected and
unselected item pairs. `exact_flip` samples at most the output limit of
distinct `k`-item subsets without replacement. `capacity_repair` samples
ordered subsets of up to `k` selected items by the same length rule.
`profiled_capacity_repair` selects up to `k` lowest-scoring eligible items and
samples their removal orders. The profile-core Operators use the same pattern
rules as their unprofiled counterparts, restricted to the profile boundary
set. The table below defines the result rule for each sampled pattern and the
resulting list order. Candidate proposals equal to the base or equivalent to
an earlier result are skipped; the Operator does not resample beyond the
stated pattern-trial limit to fill the list.

| Operator | Candidate list construction and order |
|---|---|
| `flip_items` | Sample ordered sequences of up to `k` distinct item positions. Each changed vector appears in first-sampled order after the feasibility rule is applied. |
| `add_items` | Sample ordered sequences of up to `k` currently unselected positions; apply additions in sequence and retain qualifying changed vectors in sample order. |
| `remove_items` | Sample ordered sequences of up to `k` currently selected positions; apply removals in sequence and retain qualifying changed vectors in sample order. |
| `exchange_items` | Sample ordered sequences of up to `k` disjoint selected/unselected item pairs; apply exchanges in sequence and retain qualifying changed vectors in sample order. |
| `capacity_repair` | Sample ordered subsets of up to `k` selected positions. For an infeasible base, each pattern yields the first feasible prefix when one exists; otherwise it yields no proposal when feasibility is required. Results appear in pattern-sample order. |
| `exact_flip` | Sample distinct size-`k` position subsets uniformly without replacement. Each qualifying vector appears in subset-sample order. |
| `profile_core_flip` | Use the `flip_items` sequence rule with positions restricted to the profile boundary set. |
| `profile_core_add` | Use the `add_items` sequence rule with currently unselected positions restricted to the profile boundary set. |
| `profile_core_remove` | Use the `remove_items` sequence rule with currently selected positions restricted to the profile boundary set. |
| `profile_core_exchange` | Use the `exchange_items` sequence rule with both item positions restricted to the profile boundary set. |
| `profiled_capacity_repair` | Use the selected lowest-scoring eligible boundary items and sample removal orders. Each distinct resulting vector that meets the feasibility request appears in first-sampled order; from a feasible base, permutations yielding the same vector contribute one proposal. |

For `flip_items`, `add_items`, and `remove_items`, an elementary alternative
changes one item decision. For `exchange_items`, one alternative swaps one
selected item with one unselected item. A completed elementary change adds one
to `m`; an alternative skipped under the feasibility rule adds nothing. For
these Operators, `k` is the maximum number of elementary changes considered,
and `m` is the number actually applied. `exact_flip` is the exception: it
applies exactly `k` distinct flips or contributes no proposal.

When the base is feasible and `ensure_feasible=True`, an Operator other than
`exact_flip` tries each sampled alternative in order and keeps a change only
when the resulting working vector remains feasible. `exact_flip` applies its
complete sampled pattern and checks feasibility only on the final vector.
These rules apply independently to each candidate proposal. When the base is infeasible and
`ensure_feasible=True`, ordinary move Operators apply their sampled changes
and return the result only if the final vector is feasible. The two repair
Operators use the stop-at-first-feasible rule specified in the table below.
With `ensure_feasible=False`, the Operator applies its sampled alternatives
without feasibility filtering. A call returns an empty list when it cannot
produce a distinct candidate satisfying the requested output contract. This
defines the result of that bounded randomized call; another sample or size can
still produce candidates.

In a general instance, `exchange_items` supports sizes `1` through
`floor(n / 2)` and is registered when `n` is at least `2`. For this
four-item instance, `flip_items`, `add_items`, `remove_items`,
`exact_flip`, and `capacity_repair` support sizes `1` through `4`;
`exchange_items` supports sizes `1` through `2`. A particular base may offer
fewer alternatives at a supported size, in which case the Operator uses the
available alternatives and reports the actual `m`. The profile-guided size
ranges are derived from `core_width` in the profile section below. The examples
cover bit changes, exchanges, feasibility requests, restoration capability,
partial changes, zero changes, and composition. Section 3.3 maps these
examples to the shared architecture contracts.

| Operator | Meaning of `k` and `m` | With `ensure_feasible=True` | With `ensure_feasible=False` |
|---|---|---|---|
| `flip_items` | Sample up to `k` distinct item positions and flip them in the sampled order; `m` is the number of flips retained. | From a feasible base, skip a sampled flip if it would make the working vector infeasible. From an infeasible base, return the sampled-flip result only if it is feasible. From `c` with `k=1`, flipping item 1 off gives `(0,1,0,0)`, `m=1`. | Apply all sampled flips. From `c`, flipping item 3 on produces infeasible `e` with `k=1`, `m=1`. |
| `add_items` | Sample up to `k` distinct currently unselected items and consider them in order; `m` is the number added. | From a feasible base, skip an addition that exceeds any capacity. From `c` with `k=1`, either available item exceeds a capacity, so the Operator returns an empty candidate list. An infeasible base cannot be repaired by adding items. | Apply all sampled additions. From `c`, adding item 3 produces infeasible `e` with `k=1`, `m=1`. |
| `remove_items` | Sample up to `k` distinct currently selected items and remove them in order; `m` is the number removed. | From a feasible base, all sampled removals are retained. From an infeasible base, retain all sampled removals and return the result only if it is feasible. | Apply all sampled removals; the application assessment records the resulting feasibility. |
| `exchange_items` | Independently randomize the selected and unselected item positions, pair them in order, and take up to `k` disjoint pairs; `m` is the number of exchanges applied. Each pair removes one selected item and adds one unselected item. | From a feasible base, skip a pair if the resulting working vector exceeds any capacity. From an infeasible base, return the sampled-exchange result only if it is feasible. From `c`, the pair `(item 1, item 3)` returns `b` with `k=1`, `m=1`. | Apply all sampled disjoint exchanges; the result may be infeasible. |
| `capacity_repair` | Sample up to `k` distinct selected items in random order and remove them in that order; `m` is the number removed. | Registered as a feasibility-restoration Operator. From an infeasible base, stop after the first removal that makes the vector feasible; return no proposal if the sampled removals do not restore feasibility. From `e` with `k=2`, removing item 3 returns `c` with `m=1`. From a feasible base, apply all sampled removals. | From an infeasible base, stop at the first feasible vector; if the sampled removals leave it infeasible, return the result after all sampled removals. From a feasible base, apply all sampled removals. From `(1,1,1,1)` with `k=1`, removing item 4 gives resource use `(5,5)`, still infeasible. |
| `exact_flip` | Sample exactly `k` distinct item positions uniformly and flip all of them; `m=k` for a returned candidate. | Return a proposal only when its final vector is feasible; if no sampled pattern qualifies, return no proposal. From `(1,1,1,1)` with `k=1`, every one-bit flip remains infeasible. | Return the sampled vector at Hamming distance exactly `k`. |
| `profile_core_flip` | Apply the `flip_items` rule using only positions in the profile boundary set. | Apply the shared feasibility rule above. | Apply all sampled flips; the result may be infeasible. |
| `profile_core_add` | Apply the `add_items` rule using only currently unselected positions in the profile boundary set. | Apply the shared feasibility rule above. | Apply all sampled additions; the result may be infeasible. |
| `profile_core_remove` | Apply the `remove_items` rule using only currently selected positions in the profile boundary set. | Apply the shared feasibility rule above. | Apply all sampled removals; the result may be infeasible. |
| `profile_core_exchange` | Apply the `exchange_items` rule using only selected and unselected positions in the profile boundary set. | Apply the shared feasibility rule above. | Apply all sampled disjoint exchanges; the result may be infeasible. |
| `profiled_capacity_repair` | Rank currently selected boundary-set items by increasing profile score, take up to `k`, then randomize that subset for removal; `m` is the number removed. | Registered as a feasibility-restoration Operator. From an infeasible base, stop at the first feasible vector; return no proposal if the sampled subset does not restore feasibility. From a feasible base, apply all sampled removals. | From an infeasible base, stop at the first feasible vector; if the sampled subset leaves it infeasible, return the result after all sampled removals. From a feasible base, apply all sampled removals. |

Each `CandidateProposal` has a positive `m`. An empty `OperatorResult.candidates`
tuple records that the bounded randomized call returned no proposal; summary
modification-count frequencies represent that call with one `m=0`. Another
sample or later call at the same size may find a candidate. The examples above
for `add_items` and `exact_flip` happen to have no qualifying move in their
stated calls.

These examples show that `m` is interpreted with the operator identity and
requested size. For example, `exchange_items` reports one exchange as
`k=1, m=1`, while an operator that counts changed item decisions reports the
same transformation as `k=2, m=2`. A proposal such as the `capacity_repair`
call from `e` can have `m < k`. An empty result has no `CandidateProposal`;
summary statistics represent it with `m=0`. A returned candidate is evaluated
once; a duplicate proposal reuses the existing solution record while retaining
an outcome for the proposal occurrence.

Operators may also carry optional candidate metadata. For example,
`exchange_items` can attach the removed and added item indices so an outcome or
event can explain the move. That metadata follows the immutable ownership
contract in architecture §5.4. Its registered `operator_id`, rather than the
metadata, identifies the Operator component. The standalone choice is reported
and scored by its tagged `choice_id`; a composition has a separate
`choice_id`.

#### Profile configuration and solver-free core heuristic

Mavrotas, Figueira, and Florios [3] identify a core of decision variables using
weighted objective functions and LP dual information, then focus subproblem
search on those variables. The profile-guided Operators in the catalog adapt
the focus-on-uncertain-items idea with a solver-free greedy reference
selection. Their item scores and boundary set define a heuristic neighborhood;
the paper's reported coverage results concern its LP-based method.

A `PreferenceProfile` is fixed configuration for a profile-guided Operator. It
contains `p` objective weights `lambda[r]` and `m` resource prices `mu[j]`.
Both tuples contain finite nonnegative values; after conversion to Python
floats, the objective weights sum exactly to `1.0`. Its `core_width` is a
positive integer excluding Boolean values. For item `i`, the profile score is

```text
score[i] = sum_r lambda[r] * contribution[r, i]
           - sum_j mu[j] * consumption[j, i]
```

The application supplies the resource prices as heuristic values in the
Operator configuration. For a profile, a greedy reference selection starts
empty, visits items in decreasing score order (using the file's item order to
break score ties), and adds an item when its score is positive and its addition
respects every capacity. A positive integer `core_width` sets the number of
items selected from each side of the reference selection's score boundary.
The boundary set contains up to `core_width` selected items with the lowest
scores in that reference selection and up to `core_width` excluded items with
the highest scores; ties follow file order. This set is a solver-free
heuristic neighborhood focus.

The profile-guided Operators and their size and modification-count scales are
listed in the catalog above. Their profile determines the eligible item
positions, and the shared randomized-sampling rule above orders those
alternatives for a call. Let `Bmax = min(n, 2 * core_width)`, the largest
possible boundary-set size. `profile_core_flip`, `profile_core_add`, and
`profile_core_remove` support sizes `1` through `Bmax`;
`profile_core_exchange` supports sizes `1` through `floor(Bmax / 2)` and is
registered only when that maximum is positive; `profiled_capacity_repair`
supports sizes `1` through `core_width`. A particular profile and base may provide fewer eligible alternatives, producing proposals with partial `m` values or an empty result under the catalog rules; summary statistics use `m=0` for an empty result. With `ensure_feasible=True`, every returned vector satisfies
all capacities. A profile can be registered
more than once with different weights, resource prices, or `core_width`; each
registration has its own `OperatorId` and configuration identity, so its
feedback and reporting remain distinct. An `OperatorGroup` may pair
`profile_core_remove` and `profile_core_add` as a soft composition hint.

For the four-item example, take `lambda=(0.5, 0.5)`, `mu=(0, 0)`, and
`core_width=1`. The profile scores are `(5, 5.5, 4, 9.5)`. The greedy
reference selection contains item 4; items 1, 2, and 3 are excluded because
each would exceed at least one capacity when considered. The boundary set is
therefore `{2, 4}`: item 4 is the lowest-scoring selected item and item 2 is
the highest-scoring excluded item. Starting from `c`, `profile_core_remove`
can remove item 2 and return `(1,0,0,0)` with `k=1`, `m=1`. Starting from
infeasible `e`, `profiled_capacity_repair` can remove boundary item 2 and
return `a` with `k=1`, `m=1`; item 2 is the only selected item in the boundary
set for `e`.

The paper also adjusts core width according to spacing between efficient
extreme points of its relaxed Pareto front. A future profile-selection policy
could favor weight profiles associated with sparse regions of the observed
archive. That behavior needs archive-aware policy input and is outside this
solver-free Operator catalog.

An operator composition can be illustrated by `add_items(remove_items(x))`.
For example, removing item 1 from `c` and then adding item 3 produces `b` as
one possible final proposal. This one-step composition uses two Operators
from the original catalog. Both component calls receive the same requested
size `k`; each final proposal has count `min(m_remove, m_add)`, so this example
has `k=1` and composition `m=1`. The ordered candidates returned by
`remove_items` serve as inputs to successive `add_items` calls; each
intermediate count appears in its Operator-result event. The composition calls
`remove_items` with `ensure_feasible=False` and passes its feasibility request
to each `add_items` call. The engine evaluates and records each final vector
from `add_items` in result order, so `b` receives one candidate outcome in
this illustration, and each other final proposal receives its own outcome.
The composition is
identified by the ordered pair
`(remove_items, add_items)` and its feedback is separate from both standalone
Operators. An `OperatorGroup` containing these Operators provides a soft hint
for selecting among compositions under `performance_matching`. The engine
first selects the standalone or composition category using
`composition_selection_probability`; uniform feedback assigns equal
probabilities within the selected category, independent of group membership.
The algorithm design defines the category probability, composition selection,
and result handling.

### 3.3 Application components and exchanged data

| Architecture object | Concrete example |
|---|---|
| `SolutionCodec` | Encode each immutable solution tuple as UTF-8 JSON bytes, preserving valid and malformed vectors as defined in §2; see architecture §§8.1 and 8.3 for checkpoint and Pareto-snapshot timing. |
| `SolutionIdentity` | Compare submitted vectors by exact equality. `b` and `d` remain distinct despite equal objective vectors. |
| `ObjectiveDeclaration` | Use the ordered `ObjectiveFunction` definitions in §1: one function per negated profit objective, each using ascending numeric comparison and a readable name and value format. |
| `ObjectiveFunction` | Compare two negated profit sums in the usual ascending real order; return `less`, `equal`, or `greater`; provide a readable objective name through `__str__()` and a value rendering for reports. |
| `ApplicationEvaluator` | Apply the callback contract, including deterministic results for the fixed problem instance, in architecture §5.6 to the knapsack representation and rules in §2. |
| `CandidateContext` | Supply the base, size, `max_candidates` limit, feasibility request, operator-owned state, and shared `RandomSource`; the `capacity_repair` example above shows an infeasible base. The caller sets `MultiobjectiveVNSConfig.max_candidates_per_attempt`. The source seed and checkpoint continuation follow architecture §6.6 and §8.1. |
| `Operator` and `OperatorResult` | Apply the shared call contract and return an ordered tuple of `CandidateProposal` records, each with its own candidate and `m`. The catalog above shows per-operator size and modification-count scales. |
| `SolutionRegistry` and `EvaluationRecord` | Keep one record and one evaluation result for each distinct identified vector. A repeat of `b` refers to its existing record. |
| `CandidateOutcome` | Describe each proposal submission occurrence. An initial submission has `choice_id=None`; an Operator-produced candidate records the tagged `ChoiceId` and its 1-based `candidate_index` in the ordered list, as defined in architecture §6.6. `component_position=None` for an ordinary standalone Operator and `second` for a dynamic composition, pre-registered composite, or promoted Operator, identifying the component that produced the submitted candidate. The outcome also records the base, requested size, actual count, disposition, and `SolutionId` once resolved. |
| `ProblemInstanceId` | Use the identifier and instance contents defined in §1. |

The knapsack evaluator may include resource loads, remaining capacities, and
per-item objective contributions in `ApplicationEvaluation.analysis`.
Operators receive that immutable value through
`CandidateContext.base_evaluation`. For example, a repair Operator can use
resource slack to choose items for removal, while an exchange Operator can
focus on items that contribute strongly to competing objectives.

### 3.4 Coordination, policies, and reporting

| Architecture object | Concrete example |
|---|---|
| `MultiobjectiveVNS` and `MultiobjectiveVNSConfig` | Coordinate ordered candidate processing, identity lookup, and evaluation dispatch through `Executor`; expose search and maintenance operations; and hold search-wide parameters and per-invocation limits. The caller must set `max_candidates_per_attempt`. |
| `ArchivePolicy` | Determine archive membership from objective vectors of feasible, visited solutions. Here `b`, `c`, and `d` have maximal vectors under dominance, while `c` dominates `a`. |
| `ExplorationPolicy` | Before cleanup, the default pool contains feasible `a`, `b`, `c`, and `d`; relaxed mode also retains infeasible `e`. Cleanup leaves the known Pareto solutions `b`, `c`, and `d` active. Eligibility and custom-policy rules are defined in algorithm design §2. |
| `BaseSelectionPolicy` | In the relaxed-mode example, infeasible `e` is the current base during an excursion anchored at `c`. The shared eligibility and selection rules are defined in algorithm design §2. |
| `BudgetPolicy` | Allow the next search iteration or stop the invocation early; the engine enforces every configured ceiling, including evaluation-request limits. |
| `Executor` | Invoke the knapsack `ApplicationEvaluator` when the engine dispatches a request for a new `SolutionId`; the `SolutionRegistry` retains the resulting `EvaluationRecord` for reuse. |
| `RandomSource` | Supply the one shared random stream for Operator calls, policy decisions, and engine sampling. Its configured seed and checkpoint continuation follow architecture §6.6 and §8.1. |
| `FeedbackPolicy` | Maintain separate statistics for standalone Operators and ordered compositions, using each choice's modification-count scale; state changes follow architecture §6.4 and algorithm design §2. The engine selects a category first; `OperatorGroup` membership provides soft hints among compositions under `performance_matching`. |
| `SearchState` and `CheckpointStore` | Keep an opaque, immutable continuation snapshot. Use its queries in architecture §7 to inspect Pareto solutions, active solutions, and all visited records. Checkpoints follow the transaction and save rules in algorithm design §2. |
| `AlgorithmEvent` and `EventObserver` | Publish and receive ordered records of processing, proposals, assessments, archive and pool changes, failures, and termination; events from one committed transition share its `commit_sequence`. |
| `RunLogger`, `RunLog`, and `ExecutiveSummary` | Write the ordered event stream to a structured `.events.jsonl` file and render the structured summary record defined in architecture §6.6 as a human-readable `.summary.txt` file under the invocation's log directory. |
| `SolutionFormatter` | Optionally describe a final knapsack solution in the human-readable summary, for example by listing selected item IDs. The structured event file uses its `SolutionId` and outcome details. |
| `RunResult` | Expose final search state, committed candidate outcomes in processing order, termination reason, log and summary paths, diagnostics, and the latest successful checkpoint reference. |
| `MaintenanceResult` | Return the updated checkpoint after active-pool cleanup or feedback reset, with its log artifacts and latest successful checkpoint reference. |

For example, pass `run_result.search_state` to
`search.maintain(state=run_result.search_state, clean_active_pool=True)` to
apply the cleanup illustrated above. The returned checkpoint preserves all
registry records.

### 3.5 Public entry points

`load_knapsack_application(path, profiles=(), operator_groups=())` reads a
TOML file and assembles the instance-bound `SolutionCodec`, `SolutionIdentity`,
`ObjectiveDeclaration`, `ApplicationEvaluator`, and `OperatorRegistration`
catalog described above. `build_knapsack_application(instance, ...)` performs
the same assembly for already validated instance data. The returned
`KnapsackApplication` exposes the fields needed by `MultiobjectiveVNS`, plus
`problem_instance_id`, `operators`, and `operator_groups` convenience
properties. `load_instance(path)` and `build_operator_catalog(instance, ...)`
are available when an application needs more direct control over assembly.

The caller supplies initial solutions and search settings to the core search.
For example:

```python
from multiobjective_vns import MultiobjectiveVNS
from multiobjective_vns import MultiobjectiveVNSConfig
from multiobjective_vns.knapsack import load_knapsack_application

application = load_knapsack_application('path/to/instance.toml')
search = MultiobjectiveVNS(
    solution_codec=application.solution_codec,
    solution_identity=application.solution_identity,
    objective_declaration=application.objective_declaration,
    operators=application.operators,
    application_evaluator=application.application_evaluator,
    problem_instance_id=application.problem_instance_id,
    operator_groups=application.operator_groups,
    config=MultiobjectiveVNSConfig(max_candidates_per_attempt=5),
)
result = search.run(
    initial_solutions=((0,) * application.instance.n_items,),
)
```

The empty selection is a valid feasible initial solution under this model.
The P410 example is run from its own folder so that its default output
directory stays with the example:

```shell
cd examples/knapsack/p410
uv run --project ../../.. python run.py
```

`run.py` loads `instance.toml` beside the script and writes event and summary
files into `multiobjective-vns-logs/` under the current working directory. The
core constructor and `run()` arguments are defined in architecture §7.

## Bibliography

1. C. Bazgan, H. Hugot, and D. Vanderpooten. “Solving efficiently the 0–1
   multi-objective knapsack problem.” *Computers & Operations Research*,
   36(1):260–279, 2009. [https://doi.org/10.1016/j.cor.2007.09.009](https://doi.org/10.1016/j.cor.2007.09.009).
2. K. Florios, G. Mavrotas, and D. Diakoulaki. “Solving multiobjective,
   multiconstraint knapsack problems using mathematical programming and
   evolutionary algorithms.” *European Journal of Operational Research*,
   203(1):14–21, 2010. [https://doi.org/10.1016/j.ejor.2009.06.024](https://doi.org/10.1016/j.ejor.2009.06.024).
3. G. Mavrotas, J. R. Figueira, and K. Florios. “Solving the bi-objective
   multi-dimensional knapsack problem exploiting the concept of core.”
   *Applied Mathematics and Computation*, 215(7):2502–2514, 2009.
   [https://doi.org/10.1016/j.amc.2009.08.045](https://doi.org/10.1016/j.amc.2009.08.045).
4. C. Gomes da Silva, J. Climaco, and J. Figueira. “A scatter search method
   for the bi-criteria multi-dimensional {0,1}-knapsack problem using surrogate
   relaxation.” *Journal of Mathematical Modelling and Algorithms*, 3:183–208,
   2004.

