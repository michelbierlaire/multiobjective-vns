# Algorithm design: behavior and defaults

**Status:** Version 1 algorithm design; implementation update pending
**Scope:** Search flow, policy defaults, outcome effects, and continuation

This document defines algorithm behavior for the objects in the
[architecture design](multiobjective-vns.md). The architecture remains the
reference for object responsibilities, callable signatures, record fields,
nullability, identifier value and serialization contracts, and shared
terminology; §6.6 is the authoritative interface contract.

Section 1 gives the control-flow overview. Normative details are owned by the
corresponding rule sections in §2. Public construction defaults are listed in
architecture §6.6; policy and service defaults are stated with their governing
rules below. Solution values and candidate metadata follow the ownership
contracts in architecture §§5.1 and 5.4.

## 1. Search procedure

Each search iteration performs one base selection, one Operator-choice
selection, and one `CandidateAttempt`. Outside an infeasible excursion, the
base comes from the current Pareto archive; during an excursion, its current
infeasible solution supplies the base. Choice selection first selects the
standalone or composition category, then selects a choice within that
category. Each choice has its own checkpointed current size, keyed by its
`ChoiceId`. That size carries with the choice across iterations and base
selections.

After candidate processing, a new admission to the known Pareto archive resets
the selected choice's cursor to `k=1`. Every other committed attempt advances
only that choice's cursor by one, including `m=0`, partial modification,
duplicates, dominated, invalid, infeasible, and failed candidates. Other
choices' cursors remain unchanged. The next iteration selects a base and
choice anew. Cursor initialization, progression, exhaustion, and category
selection are defined in §2, Choice sizes and feedback.

The default base selector chooses from current Pareto archive members. A
caller-provided selector follows the eligible-record boundary in §2, Archive,
active pool, and base selection. An active infeasible excursion supplies its
current infeasible solution as the iteration base until that excursion ends.
This is the agreed excursion continuation rule.

The pseudocode defines one run() invocation. Detailed transition, budget,
failure, and event behavior is owned by the rules in §2. The caller supplies
an immutable MultiobjectiveVNSConfig to the constructor; omitting it selects
the field defaults in architecture §6.6. The run caller may supply
log_directory and StopToken as defined in the architecture.

Numeric configuration inputs and callback-returned probabilities follow the
FiniteReal and Integer contracts in architecture §6.6. Validate all
configuration fields and cross-field rules before loading a checkpoint or
processing initial solutions. A relaxed configuration requires a positive
max_infeasible_steps and at least one selectable feasibility-restoration
Operator choice. Invalid configuration raises ValueError before any
Operator call.

```text
run(input):
    record the monotonic clock reading at run() entry
    validate MultiobjectiveVNSConfig, budget limits, and checkpoint settings
    resolve the effective neighborhood-size cap

    if input is a checkpoint:
        load it when input is a CheckpointReference
        raise CheckpointRestoreError if loading fails
        validate problem, objective, and continuation-component compatibility
        restore SearchState, including choice_size_cursors keyed by ChoiceId,
            Operator state keyed by OperatorId, policy and extension state,
            and the saved RandomSource stream state
        rebuild the private lookup-key index when configured
        checkpoint_ready = true
    else:
        restore registered stateful components to construction baselines
        create empty SearchState with all registered standalone choices and
            ordered pairs of registered Operators in the dynamic catalog,
            initializing every choice cursor to 1 and component state to its
            baseline
        latest_checkpoint_reference = none
        checkpoint_ready = false

    create InvocationId and begin RunLogger
    if logger setup fails:
        return component_failure RunResult with current state and diagnostics

    reset per-invocation counters
    append invocation_started and emit a due progress report
    restore active InfeasibleExcursion from SearchState, if any

    if input starts a new search:
        process the complete initial-solution set
        if initialization stops on a component failure:
            finalize and return component_failure RunResult
        if no valid feasible initial solution was found:
            emit initialization_failed and invocation_stopped
            finalize logs and raise InitialSolutionsError
        checkpoint_ready = true

    initial_archive_ids = current Pareto archive IDs
    initial_pareto_count = length(initial_archive_ids)
    previous_iteration_context = SearchState.base_selection_context

    while termination_reason is none:
        if StopToken requests a stop:
            termination_reason = interrupted
            break
        if an engine-enforced ceiling prevents another CandidateAttempt:
            termination_reason = budget_exhausted
            break
        ask BudgetPolicy.decide(counters, limits, elapsed_seconds)
        emit budget_decision
        if the policy denies the next iteration:
            termination_reason = budget_policy_stop
            break

        emit a due progress report
        recheck engine-enforced ceilings
        if a ceiling prevents another CandidateAttempt:
            termination_reason = budget_exhausted
            break

        base = select_base(SearchState, previous_iteration_context)
        if base selection fails:
            termination_reason = component_failure
            break

        restoration_required = active excursion has reached its step limit
        selectable_choices = choices that are enabled, have current_k at or
            below their effective maximum, and satisfy the restoration
            requirement
        if selectable_choices is empty:
            termination_reason = no_available_restoration_operator if
                restoration_required else no_available_operator
            break
        eligible_choices = selectable_choices that pass the remaining
            evaluation-request ceiling
        if eligible_choices is empty:
            termination_reason = budget_exhausted
            break

        emit choice_set_available with all eligible ChoiceDescriptors,
            including each choice's current_k
        choose standalone or composition category using
            composition_selection_probability
        ask FeedbackPolicy for a distribution within that category
        sample one choice with the shared RandomSource
        emit choice_category_selected and choice_selected

        if an engine-enforced ceiling now prevents another CandidateAttempt:
            termination_reason = budget_exhausted
            break

        k = choice_size_cursors[choice.choice_id]
        increment candidate_attempts
        attempt = call choice(base, k, ensure_feasible)
        process its final candidate, if any
        if the final candidate was admitted to the Pareto archive:
            next_k = 1
        else:
            next_k = k + 1
        prepare one SearchCommit containing candidate processing, feedback,
            component and excursion state, next_k for this ChoiceId, the base
            selection context, and the ordered event batch
        if the SearchCommit succeeds:
            publish its replacement SearchState
            save a checkpoint when due
            emit a due progress report
        else:
            retain the prior committed SearchState
            termination_reason = component_failure
            break
        previous_iteration_context = SearchState.base_selection_context

        if attempt reports a terminal invocation failure:
            termination_reason = attempt.termination_reason
        elif StopToken requests a stop:
            termination_reason = interrupted

    if termination_reason is none:
        termination_reason = budget_exhausted

    save the final checkpoint when due
    emit invocation_stopped and finalize logs
    return RunResult with committed SearchState, committed CandidateOutcomes,
        termination reason, latest checkpoint reference, log artifacts, and
        diagnostics
```

process_initial_solutions() returns completed after processing the full
initial set and finding at least one valid feasible solution,
no_valid_feasible_solution after processing the full set without one, or
component_failure when an invocation-scoped failure stops initialization.
Initial submissions are mandatory initialization work, so the engine processes
the full supplied set even when the wall-time limit elapses during
initialization. The run driver handles no_valid_feasible_solution by emitting
initialization_failed and invocation_stopped, finalizing the invocation, and
raising InitialSolutionsError as shown above. A completed valid initial
set emits initialization_completed under architecture §6.6.
process_final_result() applies candidate processing to a choice's final
output and applies the modification-count and composition rules in §2.
select_base() follows the archive, active-pool, base-selection, and
infeasible-excursion rules in §2. select_operator_choice() applies the
two-stage category and within-category selection rule defined there. The
selected choice's current_k is its committed cursor, not a per-iteration
counter reset by choosing a different base or choice.

The candidate-processing, feedback, excursion, cursor, and event changes are
prepared and committed under the single transition rule in §2, Search commit
boundary and ordering. Invocation event triggers and event order are
authoritative in architecture §6.6. Each attempted composition contributes
one composition_result event to its successful CandidateAttempt commit,
including a composition that returns m=0 or records a component failure. A
first promotion contributes one composition_promoted event to the same
commit as its archive admission.

### Archive, active pool, and base selection

The engine defines active-pool eligibility; `ExplorationPolicy` chooses
membership within that boundary. A record is eligible when its completed
assessment is `valid`, or when its completed assessment is `infeasible` and
the run uses relaxed feasibility. Records with an `invalid` assessment,
`ProcessingStatus.failed`, or an `infeasible` assessment in strict mode are
ineligible. This also excludes a representation-valid infeasible solution
whose objective values are unusable and whose assessment is therefore
`invalid`. During reconciliation, every current Pareto archive member is
mandatory, and the policy may include any subset of the eligible IDs. The
request and result fields that enforce this rule are defined in architecture
§6.6.

| Concern | Rule |
|---|---|
| Objective comparison | Pareto dominance uses the ordered comparison supplied by each `ObjectiveFunction`: every coordinate must compare `less` or `equal`, with at least one `less`. |
| Archive | Unlimited capacity; every distinct feasible solution whose objective vector is maximal under dominance among feasible solutions in `SolutionRegistry` is retained, including distinct solutions with equal objective vectors. |
| Active pool | The default policy retains currently active eligible records, adds each newly registered eligible record and every current Pareto archive member, and makes no other automatic removals. Records removed by cleanup remain inactive under the default policy. The pool is unbounded by default. |
| Cleanup | Set the active pool to exactly the current Pareto archive; preserve the complete registry. |
| Base eligibility | Outside an infeasible excursion, eligible bases come only from the current Pareto archive. After a non-admission, the previous base is omitted when another archive member remains; the sole archive member remains eligible when no alternative exists. The active pool remains available to exploration policies and custom search extensions, while archive membership defines the base universe. During an excursion, its current infeasible solution is the sole eligible base, supplied directly by excursion state even when its `invalid` assessment keeps it outside the pool. When the excursion ends, base eligibility returns to the current Pareto archive. An infeasible record with a completed `infeasible` assessment remains in the pool until cleanup; an objective-invalid infeasible record remains in the registry only. |
| Base selection | At every iteration outside an active excursion, call `BaseSelectionPolicy` with the eligible current Pareto members and the previous iteration's base and admission result. After archive progress, the previous base remains eligible when it belongs to the current archive. After no progress, omit it only when another archive member remains. The package default chooses the eligible member with the fewest prior `CandidateAttempt`s and breaks ties randomly. A custom selector may choose any member supplied in the request. Per-base counts persist across invocations. During an active excursion, its current infeasible solution supplies the base directly; after the excursion ends, that solution is ineligible for later base selection. |

Every `ArchivePolicy` uses the exact Pareto rule defined in architecture §6.4.
For a valid, feasible, newly identified solution, a dominated objective vector
receives `SearchDisposition.dominated` and `ArchiveStatus.Dominated`. A
nondominated vector is admitted with `SearchDisposition.archived` and
`ArchiveStatus.Pareto optimal`; the policy removes exactly the current archive
members that the new vector dominates, and those former members receive
`ArchiveStatus.Rejected`. Equal objective vectors from distinct solutions are
both admitted and remain Pareto optimal. A duplicate proposal receives
`SearchDisposition.duplicate` and does not invoke the archive policy.

The engine checks each `ArchiveDecision` against these rules before applying
it. A custom policy result with an inconsistent admission or removal set
follows the common policy failure behavior in Failure handling.


### Choice sizes and feedback

Each registered standalone Operator and each available composition supports
every integer size from 1 through its finite maximum. A choice's maximum is
the smaller of its declared component maxima and the run-level cap. When
config.max_neighborhood_size is None, the cap is the largest maximum among
user-registered Operators.

Every Operator choice has an independent checkpointed current-k cursor keyed
by ChoiceId. User-registered standalone choices start at k=1. A composition
choice starts at k=1 when it first enters the dynamic catalog, including
before that composition has been promoted. At each iteration, choices with
current_k above their effective maximum are exhausted and ineligible. A
choice remains available at each size as its cursor progresses; unsuccessful
attempts advance it by one. Pareto admission resets that selected choice to
k=1. Reaching the maximum size without an admission advances the cursor beyond
the maximum and exhausts the choice for the logical search. These cursor
changes commit with the CandidateAttempt and persist through checkpoint and
resume. An Operator used as a standalone choice and in one or more
compositions has a separate cursor for each corresponding ChoiceId.

At each iteration, the engine builds the ChoiceDescriptor tuple from choices
that are enabled, not exhausted, compatible with the current feasibility
requirement, and eligible under engine-enforced budgets. The
choice_set_available event records that full set, with each descriptor's
current_k and effective maximum. Its supported choice-size pairs contribute
to the invocation summary, including pairs with zero attempts.

Operator-choice selection has two stages. First, the engine partitions the
eligible descriptors into standalone and composition categories and chooses
a category using config.composition_selection_probability. When both
categories contain eligible choices, the composition category has that
probability and the standalone category has its complement. When only one
category is nonempty, the engine selects that category with probability one.
Selecting one registered or promoted Operator is a standalone choice;
selecting an ordered pair is a composition choice.
The engine records the selected category in choice_category_selected.
When both categories are nonempty and composition_selection_probability is
strictly between zero and one, the engine draws from RandomSource; a value
below the configured probability selects composition, and any other value
selects standalone. At probability zero or one, the configured category is
selected without a draw. If only one category is nonempty, the engine selects
it without a draw and records selection_source="only_available_category".
Second, it asks FeedbackPolicy.distribution to distribute probability only
among the eligible choices in the selected category, then samples one choice
from that distribution. This category rule applies with the built-in and
custom FeedbackPolicy; a custom policy controls only the second-stage
distribution. The selected descriptor's current_k is the size used in that
iteration.

| Search concept | Meaning |
|---|---|
| Search iteration | One selection of a base and an OperatorChoice, followed by exactly one CandidateAttempt. A new base and choice are selected for every iteration. |
| OperatorChoice | One selectable user-registered or promoted Operator, or one ordered composition of two available Operators. Its ChoiceId identifies its feedback and current-k cursor. |
| PromotedOperator | A package-identified Operator that executes a previously admitted composition and can itself be selected or composed. It has a standalone ChoiceId and cursor in addition to the source composition's choice cursor. |
| CandidateAttempt | One invocation of a selected Operator or composition, including an invocation that raises. Each started search iteration has one CandidateAttempt. |
| InfeasibleExcursion | A bounded sequence of infeasible candidate transitions from a feasible anchor, tracking the anchor, current infeasible solution, and step count until a feasible result ends the sequence. |

#### Feasibility and excursions

- feasibility_mode has two values: strict and relaxed. Strict mode is the
  default. It requests ensure_feasible=True for every standalone Operator
  call and for the second Operator in a composition; the first Operator in a
  composition receives False because its output is intermediate. Each
  submitted infeasible candidate is recorded in the registry and log. Its
  active-pool and archive membership follows the rules in the preceding
  section.
- Relaxed mode requests ensure_feasible=False for ordinary Operator calls.
  The first Operator in a composition also receives False; the second
  receives the composition's request. Each identity-resolved final candidate
  is recorded in the registry under the normal submission rules. When its
  feasibility finding is False, a completed infeasible assessment makes it
  eligible for relaxed-mode active-pool membership under the preceding
  section. A representation-valid candidate with a known infeasibility
  finding and unusable objective values has an invalid assessment; it stays
  outside the active pool but can still become the current base of an
  infeasible excursion.
- A feasibility-restoration Operator is designated to work from an infeasible
  base. The engine supplies the current infeasible excursion solution to that
  Operator and sets ensure_feasible=True. This request applies to generated
  candidates and the returned result; the base remains the starting point for
  the repair attempt. The Operator returns a candidate only after its
  candidate assessment confirms feasibility, and returns m=0 when its
  bounded attempt finds no qualifying candidate.
- An InfeasibleExcursion begins when an ordinary OperatorChoice from a
  feasible base produces an infeasible final candidate. Its anchor is the
  selected feasible base's SolutionId; its current solution is the latest
  infeasible candidate. Each later infeasible candidate becomes the current
  solution while the anchor remains fixed. A feasible candidate ends the
  excursion.
- In relaxed mode, max_infeasible_steps is a required positive limit. Each
  ordinary OperatorChoice whose final result is known to be representation-
  valid and infeasible adds one step when its effective m is positive,
  regardless of the value of m. This includes a result whose objective values
  are unusable and whose summary assessment is invalid, provided the stored
  application findings establish representation_valid=True and feasible=False.
  A duplicate infeasible proposal also counts as a step. A zero-modification
  result adds no step. Once the limit is reached, the next iteration selects
  a feasibility-restoration Operator choice whose call requests
  ensure_feasible=True.
- A restoration choice uses its own current-k cursor. The cursor follows the
  same advancement and reset rule as every other choice. The excursion stays
  active until a feasible candidate closes it; another iteration selects a
  restoration choice when the current result remains infeasible and budget
  remains.
- If a restoration final result has the anchor's SolutionId, record a return
  to the starting solution, reuse its stored evaluation through duplicate
  handling, end the excursion, and leave archive membership unchanged. A
  different feasible result follows ordinary registry and archive handling
  and ends the excursion.
- A feasible final candidate produced before the limit also follows ordinary
  registry and archive handling and ends the excursion.
- Excursion transitions use the final choice output only when its
  representation is valid, its feasibility finding is available, and the
  Operator met its ensure_feasible request. An objective-value diagnostic
  alone does not prevent a transition when representation and feasibility are
  known. Thus, a representation-valid result known to be infeasible can start
  or advance an excursion when its objective values are unusable and its
  assessment is invalid. The registry stores that assessment and diagnostic;
  the engine reads representation and feasibility findings from the new or
  reused EvaluationRecord.application_evaluation rather than inferring them
  from the summary assessment. Excursion state supplies the result as the
  next iteration's base without adding it to the active pool. It remains
  registry-only after the excursion ends.

#### Modification counts and cursor updates

- m=0 means the selected OperatorChoice produced no candidate in this call
  under the requested k and feasibility requirement. It does not establish
  that all possible moves at that size were examined or that another call at
  that size would also return zero. Its result has an empty final-candidate
  field. For a standalone Operator, this produces no candidate submission.
  For a composition, f's output remains an intermediate value when g returns
  m=0 or is skipped; no candidate is submitted.
- A positive m carries the choice's final candidate into normal evaluation
  and archive handling. For a composition, this is only g(f(x)); f(x) is
  passed directly to g without candidate processing. For 0 < m < k, the
  reported partial count does not change size progression: an archive
  admission resets the cursor to 1, and every other committed result advances
  it to k+1.
- A final candidate is successful exactly when its newly registered solution
  is admitted to the known Pareto archive. A duplicate of an existing Pareto
  solution is not a new admission. Any result without a new admission advances
  only the selected ChoiceId cursor by one. This includes m=0, duplicate,
  invalid, infeasible, dominated, failed evaluation, and a committed Operator
  failure. When k is the choice's effective maximum, this increment marks the
  choice exhausted.
- Every attempted choice call consumes one invocation candidate-attempt count,
  including m=0, a duplicate, invalid, infeasible, dominated, failed, newly
  archived, and an Operator exception. An attempt whose SearchCommit rolls
  back does not publish cursor, feedback, or candidate-state changes; its
  invocation counter remains counted as specified by the commit rule.
- Operator-call failures and contract violations follow Failure handling.
  A committed failed attempt advances its choice cursor under the same
  non-admission rule. Invocation-level policy, selection, or RandomSource
  failures that occur before the CandidateAttempt starts do not advance any
  cursor.
- On each committed CandidateAttempt, SearchState.base_selection_context
  records the selected base and whether the attempt admitted a new Pareto
  solution. The same commit updates the selected cursor and all candidate,
  archive, active-pool, excursion, feedback, Operator, extension, RandomSource,
  and event effects. Initial-submission and maintenance commits preserve this
  context. Resume starts a new search iteration using the saved context and
  every choice's saved cursor. A fresh search starts standalone choices at
  k=1; each composition choice starts at k=1 when it first enters the
  dynamic catalog.
- When the enabled eligible choice set is empty, stop with
  no_available_operator. When the infeasible-step limit is reached and the
  enabled, unexhausted feasibility-restoration choice set is empty, stop with
  no_available_restoration_operator.

#### Feedback distribution and reward

config.feedback_selection_mode selects the built-in rule for the second
stage: choosing a choice within the category selected by the engine. Let C be
the eligible choices in that selected category and n = |C|:

```text
uniform:
    p(choice | category) = 1 / n

performance_matching:
    score(choice) = (recent_reward_sum(choice)
                     + config.feedback_prior_attempts * prior_reward(choice))
                    / (recent_attempt_count(choice)
                       + config.feedback_prior_attempts)
    p(choice | category) = config.feedback_epsilon / n
                + (1 - config.feedback_epsilon) * score(choice)
                  / sum of choices in C scores
    if all scores in C are zero, use uniform p(choice | category)
```

The category parameter is composition_selection_probability, a FiniteReal
in [0, 1] with default 0.20. When both categories are eligible, the
unconditional probability of a choice is the configured probability of its
category multiplied by its conditional probability within that category.
When only one category is eligible, that category has probability one. In the
built-in performance_matching mode, feedback_epsilon reserves that fraction
of probability mass for uniform exploration within the selected category, so
each choice in it receives at least feedback_epsilon / n conditionally. The
category parameter controls how often each category is explored;
feedback_epsilon controls exploration among choices in the selected category.

The built-in policy assigns one reward to each committed attempt. Let s be the
number of committed CandidateAttempts since the most recent Pareto admission,
measured immediately before the current attempt. An attempt that admits a
Pareto solution receives the following reward; an attempt with no admission
receives zero:

```text
reward(s) = feedback_admission_reward_floor
            + (1 - feedback_admission_reward_floor)
              * s / (s + feedback_stagnation_scale)
```

Every archive admission earns the configured positive baseline, and an
admission after a longer period without archive progress earns more reward.
The count spans choices, neighborhood sizes, and feasibility excursions.
Every committed attempt advances the gap by one, except that an admitting
attempt resets it to zero. Failed and zero-modification attempts count as
attempts. Initial-solution submissions establish the starting archive and set
the gap to zero.

For performance_matching, the built-in policy keeps the most recent
feedback_recent_attempt_window committed attempts across all choices. The
choice score is the mean reward in that window for the choice, smoothed by
feedback_prior_attempts virtual attempts at its prior reward. Choices with
no observations in the window receive their prior reward. Expired observations
no longer affect selection. The configured prior rewards are
standalone_prior_reward for a standalone Operator,
grouped_composition_prior_reward for a composition whose component
Operators share an OperatorGroup, and
ungrouped_composition_prior_reward for any other composition. A shared group
gives the pair one hint even when the pair shares multiple groups.
Uniform mode assigns equal conditional probability to all choices in the
selected category, independent of group membership. If all available scores
within that category are zero, the built-in policy also selects uniformly
within it. feedback_epsilon reserves probability mass for within-category
uniform exploration. Group hints affect composition choices through their
prior rewards and therefore influence selection only after the composition
category has been selected.

The engine validates a custom or built-in distribution for complete choice
coverage, finite probabilities in [0, 1], and a sum within 1e-12 of 1. It
divides each accepted probability by the computed sum before sampling, so the
distribution used by the shared RandomSource sums to exactly 1 within the
engine's arithmetic.

An admission means that the choice's submitted final candidate adds a new
solution to the known Pareto archive. A choice attempt can admit at most one
solution, including a composition attempt, because only its final output is
submitted. Every committed attempt contributes one reward observation and an
archive-admission indicator when its final candidate enters the archive.
Committed zero-modification, zero-admission, and failed attempts contribute
zero reward and occupy one place in the recent-attempt window; a rolled-back
attempt contributes nothing. The raw admission indicator and the
difficulty-weighted reward are both reported, so users can distinguish
frequency of archive progress from progress made after sustained stagnation.
The log retains raw attempt and archive-admission counts by choice and
requested size, plus each attempt's reward. The policy aggregates reward
observations for a choice across sizes within its recent window. An infeasible
proposal receives zero reward when it adds no archive member; a later
restoration admission rewards the restoration choice that produced it.
Excursion outcomes are reported separately from choice reward. Resetting
feedback clears the recent window and returns each choice to its configured
prior score.

### Candidate submission, commit boundary, and maintenance

The architecture defines the candidate and evaluation records and the
identity, evaluation, duplicate, archive, and status contracts. The algorithm
uses that same submission path for each initial solution and each final
candidate returned by a standalone Operator or composition. A composition's
intermediate value remains internal to the composition. A call returning
m=0 has no submission; a call returning m>0 submits only its final candidate.

Every candidate attempt is prepared as one SearchCommit. Candidate processing,
archive and active-pool changes, excursion state, the selected choice's size
cursor, feedback observation, component state, the shared RandomSource state,
and the corresponding event batch form that commit. The engine prepares all
effects before publishing the replacement SearchState. It appends the event
batch atomically and publishes the new state and CandidateOutcome collection
only after the append succeeds. The event order and commit sequence are
defined in architecture §6.6.

For a committed attempt, the selected cursor is set to 1 when the submitted
candidate is newly admitted to the Pareto archive. Every other committed
attempt sets it to k+1. This update shares the commit with the attempt's
candidate and feedback effects. A rolled-back attempt retains its previous
cursor along with the previous SearchState; its invocation attempt counter
remains counted. An archive-comparison failure follows the failure table:
roll back the candidate submission, then commit the failed-attempt record and
its non-admission cursor update when that transition can be recorded safely.

Initial solutions use the normal submission path and are all processed in
input order. The complete set must contain at least one valid feasible
solution. A candidate-processing component failure stops initialization at
that submission and returns the previously committed initial outcomes. If the
full set has no valid feasible solution, the engine emits
initialization_failed and invocation_stopped, finalizes logging, and raises
InitialSolutionsError. No Operator call or checkpoint save occurs on that
path. Initial evaluation requests are excluded from the evaluation-request
budget; their processing time counts against the wall-time limit.

Maintenance runs against a working copy of the supplied SearchState. Active-
pool cleanup and feedback reset are separate SearchCommits; when both are
requested, cleanup commits first. Cleanup sets active-pool membership to the
current Pareto archive exactly. Feedback reset clears feedback observations
and the archive-stagnation count while preserving the registry, archive,
promoted composition catalog, composition identities, and all choice-size
cursors. Maintenance commits produce their standard events and immediate
checkpoint saves when a CheckpointStore is configured. The supplied state
remains unchanged.

### Operator-result guarantee verification

The architecture defines Operator and feasibility-restoration guarantees.
For a newly identified final result, the engine checks available application
findings against the selected Operator choice's guarantee. A proven violation
is attached to the current CandidateOutcome and registry record, and disables
the selected standalone Operator or composition for the invocation. A
duplicate reuses its stored processing outcome and receives no new guarantee
check. A composition's final Operator is responsible for the final result's
guarantee. A committed non-admitting result follows the same k+1 cursor update
as every other non-admission.

### Operator groups and compositions

An `OperatorGroup` is a user-provided set of registered Operators with a
stable `group_id`; membership expresses which combinations may be promising.
A composition is an ordered pair `(f, g)` that applies `g` to the value
generated by `f`, written `g(f(x))`. The order matters: `(f, g)` and `(g, f)`
are separate choices with separate feedback. A shared group gives a pair a
soft selection hint.

The available Operator catalog starts with the user's registrations and grows
when a composition's final candidate receives the `archived` disposition and
enters the known Pareto archive. At that first admission, the algorithm
promotes the composition as a package-identified Operator whose identity is
deterministically derived from its source `CompositionId`.
The promoted Operator executes the same ordered composition and retains its
size limit, modification-count rule, feasibility-restoration capability, and
the `SolutionId` of the first final candidate that entered the archive. When
used in a later composition, it receives the same requested `k`; its internal
calls follow the composition rules recursively, and only the last component
receives the outer feasibility request. Its returned modification count is the
minimum realized count across its component calls.
It becomes available as a standalone choice and as a component in future
compositions after the admitting `CandidateAttempt` commits. Each promoted
composition expression has one catalog entry; later admissions from that
expression reuse it. The promoted Operator starts with no standalone feedback
observations and receives the configured standalone prior reward. Its
composition retains its own pair feedback record and the prior determined by
the immediate components' shared group membership. It also becomes available
as a standalone choice and as a component of new ordered compositions.

Every ordered pair of currently enabled available Operators is eligible,
including self-pairs. Each ordered pair of available Operator IDs has its own
`CompositionId` and promotion entry, including expressions that expand to the
same sequence of user-registered Operators through different composition
trees. This rule applies to user-registered and promoted
Operators, so a promoted composition can combine with another Operator or
with another promoted composition. A composition that is itself admitted is
promoted in the same way, allowing the catalog to grow through successive
successful combinations. A promoted Operator inherits the group IDs shared by
both of its immediate components; those inherited groups continue to provide
soft hints in later combinations. User-registered Operators retain their
declared group memberships.

A standalone Operator failure disables that Operator for the invocation.
Promoted Operators that use the disabled Operator become unavailable for the
invocation, along with compositions containing any unavailable Operator. A
failure in a component call made by a selected composition disables that
selected composition choice for the invocation; its available component
Operators remain usable in other choices. These effects follow the failure
scopes in Failure handling.

For a new logical search, the dynamic choice catalog is seeded with every
ordered pair of user-registered Operators before the first choice selection.
When a composition is promoted, the catalog adds every ordered pair in which
the new promoted Operator appears, paired with every Operator in the logical
search's Operator catalog, including itself, before the next iteration.
Existing ordered-pair identities are reused. Each new pair receives its
composition identity, feedback record, and `k=1` cursor before it is
selectable. A pair with an Operator temporarily disabled for the current
invocation remains in the catalog and becomes eligible again when that
Operator is enabled in a later invocation.
Composition attempts update their pair feedback record; standalone attempts
update the selected Operator ID record. Each promoted Operator's standalone
cursor is separate from the source composition's cursor. Component-call
details are retained for diagnostics. Pair scores use the prior and
selection-mode rules in the preceding section.

For requested size `k`, both component Operators run at size `k`; the
composition's maximum supported size is the smaller of their maxima. The first
Operator `f` receives the selected base and `ensure_feasible=False`. Its
returned value serves as the input base to `g` and is represented by its
component-call event. It is an internal value of the composition: it receives
no application assessment, identity resolution, registry record, or candidate
outcome. The second Operator `g` receives the composition's `ensure_feasible`
request. The engine submits and evaluates only `g`'s returned solution through
ordinary candidate processing. Application validity and feasibility are
checked on that final result, as for a standalone Operator. Identity or
evaluation failure applies to that final submission.

If `f` returns `m=0`, the composition skips `g` and returns `m=0` with no
candidate. If `g` returns `m=0`, the composition also returns `m=0` with no
candidate. In either case, the composition call produced no final candidate
under its requested size and feasibility requirement; the result does not
claim that another call at that size would also return zero. Neither case
produces a search-candidate submission. If both return
candidates, the composition returns `g(f(x))` and sets its own modification
count to `min(m_f, m_g)`. This composition-specific scale is the limiting
number of requested size units realized by both steps: it reaches `k` exactly
when both component calls reach `k`. The combined count appears on the
composition result event; the two individual counts appear on their respective
Operator-result events.

The composition's feasibility request applies to its final result. The engine
checks `g`'s output against the application's representation and feasibility
findings. A composition is eligible as a feasibility-restoration choice when
its second Operator `g` has the feasibility-restoration capability, because
`g` receives the request and produces the submitted candidate. A promoted
Operator carries the restoration capability of the final Operator in its
ordered expression.
`InfeasibleExcursion` and its step count track the final composition result as
one search transition. The final `CandidateOutcome` is the composition's
search outcome; when no final candidate is produced, the attempt has no
candidate outcome.

A successfully committed composition attempt contributes one feedback
observation with zero or one candidate outcome. A rolled-back composition
attempt contributes no committed feedback statistic. The first final
candidate with the `archived` disposition for a given `CompositionId` promotes
that composition. The new Operator entry and one `composition_promoted` event
are part of the same `CandidateAttempt` commit as that archive admission. The
event identifies the source `CompositionId`, generated Operator ID, ordered
component IDs, effective size limit, inherited group IDs, and admitted
solution through its `solution_id` envelope. The next iteration's choice set
includes the promoted Operator, so later attempts in that invocation can
select it directly or compose it with another available Operator.

The invocation summary reports the promoted catalog and its composition and
standalone performance as specified under Invocation log and executive
summary.

Both feedback-selection modes record committed feedback observations. Callers
reset them through `MultiobjectiveVNS.maintain(reset_feedback=True)`. A reset
clears the recent reward window and accumulated choice counts for all
user-registered Operators, promoted Operators, and tracked compositions;
preserves the promoted catalog and composition identities; resets the
archive-stagnation counter to zero; and leaves the solution registry, archive,
and active pool unchanged. Feedback history and the stagnation counter carry
across checkpoint resume unless explicitly reset.

### Budgets and defaults

Budgets apply per `run()` invocation, including a resumed invocation. The
invocation's budget counters start at zero; saved per-base attempt counts and
feedback statistics continue across the logical search.

Budget rules:

- The default limits are starting values for practical runs and may be
  overridden through `config.budget_limits`.
- When no custom `BudgetPolicy` is registered, the package default returns
  `BudgetDecision(allowed=True)` for every iteration request.
- The engine checks all configured hard ceilings before asking `BudgetPolicy`
  for permission and again immediately before invoking the selected choice.
  The policy can stop the invocation earlier by returning `allowed=False`,
  which produces `budget_policy_stop`. An allowed result never overrides an
  engine-enforced ceiling. The engine enforces the
  `max_evaluation_requests` ceiling at evaluation-request boundaries.
- An engine-enforced ceiling that prevents another iteration produces
  `budget_exhausted`. Budget decisions are recorded in the event stream.
- At `run()` entry, the engine records the monotonic clock's starting reading,
  before configuration validation, checkpoint loading and restoration, logger
  setup, or initial-solution processing. Elapsed time includes all of those
  activities, including every initial-solution evaluation. The
  evaluation-request budget continues to exclude initial solutions. The engine
  processes the complete mandatory initial set, then checks the wall-time
  ceiling before starting any search iteration; if initialization has used
  the available time, the run ends with `budget_exhausted` and performs no
  Operator search. An initial set that lacks a valid feasible solution raises
  `InitialSolutionsError` after full processing, even if the time limit elapsed
  during initialization.
- During search, the wall-time limit is checked before each iteration and
  immediately before its `CandidateAttempt`, including after base-selection,
  feedback-policy, random-source, and budget-policy calls.
  An Operator, evaluator, or other synchronous callback already in progress
  completes and its result is processed; the engine then starts no further
  search work once elapsed time reaches the limit. Final checkpoint handling
  and log finalization follow search termination and may complete after the
  search-work time limit. The stop event and executive summary record elapsed
  time at invocation stop; their remaining time is zero when the limit has
  elapsed.
- Wall-time budget checks, `BudgetRequest.elapsed_seconds`, progress intervals,
  and reported elapsed and remaining times all use the monotonic clock defined
  in architecture §6.6. Its readings advance independently of calendar-clock
  adjustments. A resumed invocation records a new starting reading, so time
  spent in an earlier invocation is not charged to the resumed call.
- The total-attempt cap keeps work finite as the active pool grows or operators
  continue returning candidates.
- A standalone choice or composition can require at most one new evaluation
  request, because a composition submits only its final output. Before
  selection, the engine makes a choice eligible only when the remaining
  request budget can cover that request. Duplicate final outputs may use no
  request. If no enabled choice fits the remaining capacity, the invocation
  ends with `budget_exhausted` because the configured evaluation-request
  ceiling blocks further work. This check is independent of `BudgetPolicy`.
  The default request allowance equals the total-attempt cap.
- The per-invocation `max_evaluation_requests` cap uses the counter and source
  rules in Evaluation rules above. Resume starts a fresh counter; committed
  registry evaluations remain reusable.

The engine applies `composition_selection_probability` regardless of which
`FeedbackPolicy` is registered. The built-in `FeedbackPolicy` reads the
selection mode, exploration floor, admission-reward floor, prior weight,
recent-attempt window, stagnation scale, and prior rewards from `config`. A
custom `FeedbackPolicy` uses its registered configuration for the
within-category distribution; the built-in within-category feedback fields
then have no effect.

### Periodic time reporting

`progress_report_interval_seconds` is a per-invocation `run()` argument with
a default of `10.0` seconds; it must be a positive `FiniteReal` under
architecture §6.6. The
first interval is measured from the monotonic clock reading recorded at
`run()` entry, and the setting may change on resume. At the first safe point
after an interval elapses, the engine emits a
`progress_report` event containing the wall-time limit, elapsed time, and
remaining time. Remaining time is `max(0, wall_time_limit_seconds -
elapsed_seconds)`. The event is appended to the structured event log and
delivered to observers under the architecture §6.6 event rules.

Safe points follow each initial-submission commit and each completed
`CandidateAttempt` (whether its transition commits or rolls back), and occur
before starting the next search iteration. A
synchronous callback already in progress completes before reporting. If several intervals pass during one operation,
the engine emits one report at the next safe point and schedules the next
interval from that report; it does not emit catch-up events. A report emitted
during mandatory initialization may show `remaining_seconds=0` while the
engine finishes processing the supplied initial set.

`invocation_started` records the interval, wall-time limit, and elapsed and
remaining time at event creation. `invocation_stopped` records the elapsed
time and remaining time after final checkpoint handling and immediately
before log finalization; log-rendering time is outside the reported duration.
The structured `ExecutiveSummary` and its human-readable rendering include
elapsed time, the wall-time limit, and remaining time at stop. Maintenance
invocations record elapsed time with `wall_time_limit_seconds=None` and
`remaining_seconds=None`; they have no periodic progress reports.

### Graceful stop requests

`run()` accepts an optional `StopToken`. The caller requests termination by
calling `request_stop()`; a terminal Ctrl-C handler or a graphical stop action
can use the same request. The engine checks the token before starting another
search iteration. A request received while an attempt is in progress takes
effect after that attempt's normal processing and `SearchCommit` boundary; the
engine then starts no further iteration. A new search completes
initial-solution processing and validates its initial set before honoring a
stop request.

A graceful stop returns a `RunResult` with
`termination_reason=interrupted`, records invocation termination, performs the
configured final checkpoint save for the latest committed state, and finalizes
the invocation log and summary. The token belongs to one `run()` invocation;
checkpoint state does not include it, and a resumed invocation receives a new
token when the caller wants another stop handle.

### Failure handling

Candidate record fields, application assessment, and evaluation-request
accounting are defined in Candidate processing and initial-input validation
and Evaluation rules. Architecture §5.11 defines diagnostic classification and
placement; the rules below define algorithm responses, with the listed source
and phase identifying the affected operation.

Every policy result is validated against its record contract and the semantic
constraints in architecture §6.4 and §6.6 before the engine applies it. This
rule covers `ArchivePolicy`, `ExplorationPolicy`, `BaseSelectionPolicy`,
`BudgetPolicy`, and every `FeedbackPolicy` method, including `observe()` and
`reset()`. Policy failures use `phase=policy_decision` and identify the policy
in `source`.

For either failure, the engine discards the invalid decision, records the
diagnostic in the invocation log and diagnostics, and stops with
`termination_reason=component_failure`. During a pending `SearchCommit`, the
engine discards its state changes and event batch and restores participating
stateful components to their pre-commit state. For a policy decision call
outside a `SearchCommit`, the engine restores that policy to its state at call
entry if it is stateful, then retains the latest committed `SearchState`;
`maintain()` returns the latest committed checkpoint in its
`MaintenanceResult`. Decision callbacks are read-only with respect to policy
state, so their successful calls never require a state commit. The failure
event has `commit_sequence=None`. A `BudgetPolicy` decision that satisfies its contract
continues through the regular budget rules; the engine-enforced ceilings
remain in force independently of that decision.

Every shared random draw is checked at the draw boundary under architecture
§6.6. If a draw fails before an `OperatorChoice` has been selected—including
during base-selection tie-breaking, the category draw, or while the engine
samples the within-category distribution returned by `FeedbackPolicy`—the invocation stops with
`termination_reason=component_failure`. The engine records the
`RandomSource` `ComponentFailure` and appends a `component_failure` invocation
event with `commit_sequence=None`; its `base_solution_id` is `None` before a
base is selected and identifies the selected base afterward. The event has no
`choice_id`, `solution_id`, `k`, or `component_position`; the
`ComponentFailure` likewise has no `solution_id` or `choice_id`. Its event
`source_id` identifies the `RandomSource`, and `diagnostic_origin` is
`current_operation`. The ordinary `invocation_stopped` event follows during
finalization. No `CandidateAttempt`, candidate outcome, feedback observation,
or `SearchCommit` is created, and no candidate-attempt counter is incremented.
The latest committed
`SearchState` remains the result state, and final checkpoint saving follows
the ordinary final-save rule. If the draw occurs inside a policy callback, the
callback propagates the checked draw failure and the diagnostic remains
attributed to `RandomSource`. The selection events
already emitted remain in the invocation log:
a failed `BaseSelectionPolicy` call has no `base_selected` event. An active
excursion's forced base does emit `base_selected` with
`selection_source="active_excursion"`; a policy-selected base uses
`selection_source="policy"`. A failed category draw follows
`base_selected` and `choice_set_available` and has no
`choice_category_selected` event. A failed within-category draw follows
`choice_category_selected` and has no `choice_selected` event.
Selection events use the next prospective iteration ordinal. The
candidate-attempt counter increments only when a selected choice passes the
final hard-budget check and its `CandidateAttempt` starts. A stop before that
point leaves the counter unchanged, as defined in architecture §6.6.

Invalid representations supplied initially receive an application assessment.
Operator-result guarantee violations follow the rule in “Operator-result
guarantee verification” above.

| Failure source | Scope and effect |
|---|---|
| `ApplicationEvaluator` raises, `Executor` fails, or the evaluator result has a malformed shape or classification field | Candidate-scoped `ComponentFailure` naming the source; processing status `failed`; assessment unavailable. An objective vector that is `None`, is not a tuple, has the wrong length, or contains an unusable value follows the completed `invalid` normalization in Evaluation rules. Request counting follows Evaluation rules; the run continues according to its schedule. |
| `ObjectiveFunction.compare` raises, returns a value outside `ObjectiveComparison`, or fails the self-equality check while validating an evaluated value | Normalize the objective vector to `None`, retain the representation and feasibility findings, complete the outcome with assessment `invalid`, and attach a `ComponentFailure` from that objective function with `phase=objective_comparison`. The run continues. |
| `ObjectiveFunction.compare` raises or returns a value outside `ObjectiveComparison` while testing archive dominance | During initial-solution processing, apply the early-stop rule in the initial-solution section above. During a selected search attempt, roll back the candidate submission, then commit a failed `CandidateAttempt` with its valid modification count, an empty outcome tuple, and one feedback observation containing the failure. Preserve the registry, archive, active pool, and excursion; count any evaluation request already made. Stop the invocation with `component_failure`. If this failed-attempt commit is itself rolled back, its feedback state and event are discarded under the commit rule above. |
| Any registered policy callback raises or returns an invalid result | Apply the common policy-failure rule above; it covers search and maintenance. |
| Standalone user-registered or promoted Operator raises | Count one failed `CandidateAttempt`; its successful commit includes one feedback observation with `m=None`, no candidate outcome, and the failure diagnostic. Record an invocation-scoped diagnostic, disable the selected Operator for this invocation, and continue with the next iteration. A disabled user-registered Operator also makes every promoted Operator that depends on it unavailable. Other enabled choices remain available to subsequent iterations. |
| Component Operator raises or returns a malformed result during a composition | Count one failed composition attempt; record an invocation-scoped `ComponentFailure` with `source` set to the failing component's `component_id`, `choice_id` set to the composition variant of `ChoiceId`, `phase=operator_call`, and a `reason` describing the failure. The failed-attempt commit includes a feedback observation with `m=None`, no candidate outcome, and the failure diagnostic. Disable only that selected composition for this invocation and continue with the next iteration. Its component Operators remain available in other choices; see Operator groups and compositions. |
| Standalone Operator returns a malformed `OperatorResult` | Malformed means the result violates the `OperatorResult` contract in architecture §2, Core terminology. Count one failed `CandidateAttempt`; its successful commit includes one feedback observation with `m=None`, no candidate outcome, and the failure diagnostic. Record an invocation-scoped `ComponentFailure` with the Operator's `component_id` as `source`, `phase=operator_call`, `choice_id` set to the choice ID, and a `reason` describing the violation. The failure is reported without a candidate outcome; only a candidate from a well-formed result is submitted and receives a `CandidateOutcome`. Disable the Operator for this invocation and continue with the next iteration. |
| A checked `RandomSource` draw raises or returns a non-finite value, a non-`float`, or a value outside `[0, 1)` | Attribute the failure to `RandomSource` with `phase=random_generation`. Before a choice is selected, apply the out-of-attempt rule above: stop with `component_failure`, append the invocation failure event, preserve the latest committed state, and create no attempt or feedback observation. During a selected `CandidateAttempt`'s Operator invocation, commit the attempt as failed with `m=None`, no candidate outcome, and one feedback observation containing the failure; capture post-call state for any stateful Operator that was invoked. Record no Pareto progress or candidate-state change, then stop with `component_failure`. The selected choice is not disabled. If preparing or appending this attempt commit fails, apply the ordinary rollback rule and discard the feedback update and event. |
| `RandomSource.capture_state()` raises or returns a malformed payload while preparing a `SearchCommit` | Record a `ComponentFailure` for the source with `phase=random_state_capture`. Discard the pending transition and event batch, stop with `component_failure`, and return the previous committed `SearchState`, including its prior stream payload. A resumed run restores that payload; a fresh run restores the construction baseline. |
| A stateful Operator's `capture_state` raises or returns a value outside its contract while preparing an attempt | Record a `ComponentFailure` for that Operator with `phase=operator_state_capture` and the active `choice_id`; discard the pending attempt transition, restore participating components to their pre-attempt state, and stop with `component_failure`. The invocation counters retain the attempted call; `SearchState` remains at the previous commit. |
| The `FeedbackPolicy`'s `capture_state` raises or returns a value outside its contract after `observe()` or `reset()` | Record a `ComponentFailure` for the feedback policy with `phase=policy_state_capture`. Discard the pending candidate-attempt or maintenance transition, restore the policy to its pre-commit state, and stop with `component_failure`. A candidate-attempt counter remains counted; maintenance counters remain zero. The last committed `SearchState` is returned. |
| A `SearchExtension.prepare_commit()` raises, returns an undeclared event or invalid payload, or fails state capture | Record a `ComponentFailure` whose `source` is the extension's `component_id` and whose phase is `extension.<namespace>.prepare_commit` or `extension.<namespace>.state_capture`. Discard the pending transition and event batch, restore the extension and other participating stateful components to their pre-commit state, and stop with `component_failure`. Preserve invocation counters for work already attempted and retain the last committed `SearchState`. Record the failure as an invocation event with `commit_sequence=None` when logging remains available. |
| `EventObserver` fails | Record a `ComponentFailure` with `phase=event_delivery`. Deactivate that observer, notify remaining observers when available, and continue the search. |
| `SolutionIdentity.lookup_key` raises or returns a value outside its contract while rebuilding the index from restored solutions | Raise `ComponentSetupError` with a `ComponentFailure` whose `source` is the `SolutionIdentity` component and whose phase is `identity_index_rebuild`. This occurs after compatibility validation and solution restoration, but before invocation ID creation or logger setup; no `RunResult` or `MaintenanceResult` exists. |
| `RunLogger.begin` fails | Record a `ComponentFailure` with `phase=run_logging`. Stop with `component_failure` before invocation work; return the current state in `RunResult` or `MaintenanceResult` with the logger diagnostic, and set `run_log`, `executive_summary`, `event_log_path`, and `summary_path` to `None`. |
| `RunLogger` fails while appending a transition batch | Record a `ComponentFailure` with `phase=run_logging`. Abort that pending `SearchCommit`, restore staged component state, and stop with `component_failure`; return the prior committed state and partial log in `RunResult` or `MaintenanceResult`. |
| `RunLogger` fails while appending an invocation event | Record a `ComponentFailure` with `phase=run_logging`. Keep committed search state, stop with `component_failure`, and return the latest successful checkpoint reference and partial log. |
| `RunLogger` fails during finalization | Preserve the search or maintenance `termination_reason` already written in `invocation_stopped`; finalization is a reporting operation after search termination. Add the logger `ComponentFailure` with `phase=run_logging` to `RunResult`, `MaintenanceResult`, or `InitialSolutionsError` diagnostics, retain the committed state and in-memory `RunLog`, and return any artifact paths, summary record, and report diagnostics carried by `LogFinalizationError`. A logger exception without that structured error supplies no artifact paths or summary. No later event is required when the logger cannot append. |
| Checkpoint preparation or `CheckpointStore.save` fails | Record a `ComponentFailure` with `phase=checkpoint_save`. Stop with `component_failure`; retain every committed transition in the in-memory `SearchState` and return the latest successfully saved `CheckpointReference`, if any. Checkpoint preparation and capture behavior follow architecture §8.1; the failure leaves candidate commits that triggered the save intact. A failed save leaves the previous reference usable. `MaintenanceResult` also carries the latest in-memory `Checkpoint`. |
| A `RandomSource`, stateful `Operator`, `FeedbackPolicy`, or stateful `SearchExtension` fails while capturing its construction baseline or restoring baseline/checkpoint state | Raise `ComponentSetupError` with a `ComponentFailure` identifying the component, phase, kind, and reason. Baseline capture failure occurs during engine construction; restore failure occurs before the applicable `run()` or `maintain()` invocation ID, logger setup, or result exists. A fresh search restores the `RandomSource` baseline; resume restores the stream state in the checkpoint. Setup stops at the first failure. Any component already called may hold partially restored state; a later invocation repeats the complete restore from its selected baseline or checkpoint before doing work. |
| `CheckpointStore.load` fails or returns a value outside its contract, or `SolutionCodec.restore` fails or returns a value outside its contract | Raise `CheckpointRestoreError(checkpoint_reference, failure)` before the invocation starts. The `ComponentFailure` identifies the store or codec and uses `phase=checkpoint_restore`. Compatibility mismatches continue to raise `ResumeCompatibilityError`; component-state restoration continues to use `ComponentSetupError`. |

### Checkpoints and continuation

A checkpoint is a snapshot of one completed `SearchCommit`. Its opaque
`SearchState` retains the complete solution registry and evaluation records,
archive and active-pool membership, continuation counters such as per-base
attempt counts, the base-selection continuation context, and the next-size
cursor keyed by every registered or dynamically created `ChoiceId`. A
candidate-attempt commit advances or resets that cursor under the rule in
Choice sizes and feedback. The state also retains the latest committed
`StatePayload` for each stateful Operator keyed by `operator_id`, the latest
committed `StatePayload` for each stateful SearchExtension keyed by
`component_id`, and the latest committed `RandomSource` stream payload.
Invocation-scoped disabled-choice flags reset at the start of each `run()`;
after resume, an otherwise available choice is reconsidered at its saved
cursor, while an exhausted cursor remains exhausted for the logical search.
It also retains feedback statistics for user-registered and promoted
Operators, the dynamic composition portfolio, the promoted Operator entries
with their inherited group IDs and first admitted `SolutionId`, the feedback
policy's recent reward window and aggregate statistics by requested size, and
the count of committed attempts since the most recent Pareto admission,
the active `InfeasibleExcursion` (anchor, current solution, and step count),
the compatibility manifest, and the internal commit sequence. The event batch for that commit is already present in the
invocation's ordered `RunLog` before the snapshot is offered to
`CheckpointStore`.

For `run()`, the save trigger is explicit. With a `CheckpointStore`
configured, `checkpoint_interval=None` disables interval saves. At invocation
end, save the final committed state if its `commit_sequence` is newer than the
latest successfully stored snapshot, or if no store reference exists. A
positive `checkpoint_interval=N` saves whenever the
current `commit_sequence` is at least `N` greater than the latest snapshot
sequence, then uses the new snapshot sequence as the next interval origin.
Invocation end applies the same final-save condition. When resuming from a
`CheckpointReference` without new commits, retain and return that reference
without writing another snapshot. When resuming from an in-memory `Checkpoint`
with no store reference, save the final state even if its sequence is
unchanged. The interval counts commits made by that `run()`
invocation, including initial submissions, candidate attempts, and state
changes performed during that run. Maintenance is a separate invocation and
follows its immediate-save rule below. For a new search, saving becomes
eligible only after the full initial set passes its valid-solution
precondition; a due
interval save then occurs before the first Operator call. A precondition
`ValueError` or an earlier initialization failure produces no checkpoint.
For a resumed search, saving becomes eligible after checkpoint restoration and
compatibility validation. Resume launches a next iteration using the saved cursor for every choice, and its base selection
follows the saved context and the base-selection rules above. The
`CandidateAttempt` commit updates that context and the selected choice's cursor
before its interval check. Initial-submission and maintenance commits preserve
both; the next base selection occurs at the start of the next iteration. Without a configured store (the constructor
default `checkpoint_store=None`), no automatic save occurs, and supplying an
interval is a configuration error. For a resumed search, the supplied
checkpoint's sequence is the initial interval origin; for a new search, the
origin is zero. `RunResult` returns the latest successful checkpoint
reference. On resume from a `CheckpointReference`, that input reference
remains the result reference until a newer save succeeds;
a direct `Checkpoint` input starts with no store reference. For
`maintain()`, a configured store saves immediately after each maintenance
commit; `checkpoint_interval` does not apply. The `MaintenanceResult`
contains the latest in-memory `Checkpoint` and latest successful store
reference, if any.

Checkpoint persistence follows the commit boundary. After a save trigger, the
engine prepares the complete checkpoint as specified in architecture §8.1,
then calls `CheckpointStore.save`. Capture and save happen between search
transitions; they preserve the candidate commit that made the save due. A
failure leaves the in-memory state at its latest commit, preserves previously
returned references, and stops the invocation with `component_failure`; the
`RunResult` carries the in-memory state and last successful reference, and the
`MaintenanceResult` carries the in-memory checkpoint and last successful
reference. The store publishes a successful snapshot atomically and returns a
reference after it is durable. A `checkpoint_saved` invocation event is
appended to the current `RunLog`
after the snapshot is saved; its event-level
`commit_sequence` is `None`, and its data contains the snapshot's
`commit_sequence`. Observers receive that event after it is appended.
Checkpoint persistence never rewinds or partially applies a search transition.

Each invocation's counters, event history, and termination reason are recorded
in its `RunLog`; `RunResult` and `MaintenanceResult` include their counters
and termination reason. Maintenance counters are zero. Invocation-scoped
Operator and composition disabling resets at the start of a resumed search
invocation. The saved base-selection context spans invocations, so a prior
no-progress iteration continues to exclude its base when another record remains
in the policy-specific eligible-record set: current Pareto members for the
package default selector, or feasible active-pool members for a caller-provided
selector. The previous base remains eligible when the set has only that record.

`RunResult.candidate_outcomes` returns the `CandidateOutcome` records from
committed submissions in processing order, including identity failures with
no `SolutionId` and an initial-solution prefix when initialization stops on a
component failure. `InitialSolutionsError` carries the committed initial
outcomes only when a fully processed initial set fails its validity
precondition. `RunLog` records the compact
`candidate_processed` event for each committed submission; the full solution
and metadata remain in the caller-facing outcome collection. Solution records
and checkpoints preserve evaluation and search state.

Before restoring solutions or component state, apply the resume-compatibility
checks in architecture §5.8. A mismatch raises `ResumeCompatibilityError`
during pre-run validation. For a store-backed checkpoint, call
`SolutionCodec.restore` once for each saved payload after compatibility checks
and before the invocation begins, associating each restored solution with its
saved `SolutionId`. A caller-created in-memory checkpoint already contains its
live solution values and resumes from them directly. Restore the dynamic
composition portfolio's tracked ordered pairs, promoted Operator entries,
inherited group memberships, and the saved feedback-policy state, including
its recent reward window; eligible pairs absent from the saved portfolio remain
untried and use their configured prior.
Budget counters start fresh for each invocation. `budget_limits` and the
run-level `checkpoint_interval` may change when resuming.

- The application defines what its `ProblemInstanceId` represents and binds it
  to the data used by its evaluator and operators.
- The bound `SolutionCodec` restores solutions with their application-defined
  meaning and identity.
- Codec-payload evolution follows the exact configuration match in architecture
  §5.8: a changed codec identity or configuration raises
  `ResumeCompatibilityError`, and adopting a new encoding starts a fresh
  search. `SearchState`'s internal field layout remains package-owned under
  the opaque snapshot contract. The durable-store persistence boundary and
  payload support requirements are defined in architecture §8.1; concrete
  storage formats remain a store implementation choice.

Continuation rules:

- Checkpoints preserve logical search history and the random stream position
  associated with the latest committed search state.
- A fresh search starts the shared `RandomSource` from its construction
  baseline, which the package default initializes from `random_seed`. Resume
  restores the checkpointed stream payload. Under identical package and
  Python runtime versions, problem, configuration, component, and
  initial-solution inputs, this
  reproduces package random draws across fresh runs and continuation after
  resume when callbacks are deterministic given their inputs and shared-source
  draws. Draws made since the last commit are repeated after resume.
- A repeated proposal reuses its registry record as a duplicate.
- Each search or maintenance invocation has its own log and summary.

### Invocation log and executive summary

At invocation stop, `RunLogger` returns the structured `ExecutiveSummary`
defined in architecture §6.6 and renders that record as human-readable text.
It writes the ordered `AlgorithmEvent` stream as JSON Lines. The architecture's
§6.6 event schema is authoritative for event names, phases, fields, and
nullability. Architecture §7 defines the default `log_directory`, file naming,
and how callers receive the paths. `InitialSolutionsError` exposes the same
artifact paths when initial-solution validation fails. The logger finalizes
for normal search completion, budget exhaustion, component failure, a graceful
stop request after the current commit boundary, and maintenance completion.

The structured event file identifies solutions through stable IDs and records
their outcome details. For the human-readable summary, the
optional `SolutionFormatter` can provide a description for each final Pareto
solution. The report also shows its `SolutionId` and objective values. When
`solution_formatter` is `None`, the report lists each solution by those
identifiers and objective values. If formatting a solution raises or returns
a value outside its contract, the report records a `ReportDiagnostic`, uses
the identifier and objective values for that solution, and preserves the
search result.

The architecture's `ExecutiveSummary` schema defines the report fields and
their aggregate records. The algorithm derives the initial archive count from
the archive after initial-solution processing for a new search, from restored
state for a resumed search, and from the pre-maintenance state for a
maintenance invocation.

The algorithm adds `promoted_compositions:
tuple[PromotedCompositionReport, ...]` to the architecture-defined
`ExecutiveSummary`. It lists the complete promoted-Operator catalog in the
final search state, including entries restored from earlier invocations. Each
`PromotedCompositionReport` contains `operator_id: OperatorId`,
`source_composition_id: CompositionId`,
`component_operator_ids: tuple[OperatorId, OperatorId]`,
`first_admitted_solution_id: SolutionId`,
`max_neighborhood_size: positive integer`, `feasibility_restoration: bool`,
`inherited_group_ids: frozenset[OperatorGroupId]`, and
`promoted_in_current_invocation: bool`. Entries follow promotion order across
the logical search. The Boolean is true when the invocation contains the
entry's `composition_promoted` event.

Current-invocation `ChoiceStatistics` remain grouped by source composition
and promoted Operator choice IDs, so users can distinguish combination
performance from the promoted Operator's standalone performance. The
`composition_promoted` event is emitted once, on first promotion, after the
candidate's archive-admission records in the same committed event batch. Its
payload contains the generated Operator ID, source `CompositionId`, ordered
component IDs, effective size limit, restoration capability, and inherited
group IDs; the admitted candidate appears as the event envelope's
`solution_id`.

Each `ChoiceStatistics` record is grouped by `ChoiceId` and requested size
`k`. For these statistics, an attempt is one `CandidateAttempt` whose
`SearchCommit` succeeded, identified by its `feedback_observed` event. A
selected attempt whose transition rolls back is excluded, even if its
`choice_selected` event remains in the invocation log. An archive
admission is one final candidate entering the known Pareto archive; an attempt
can admit at most one solution. The admission rate is the admission count
divided by attempts and is `None` when there are no committed attempts.
The reward total and average are also reported by choice and size; the average
is `None` when there are no committed attempts. Reward statistics explain the
difficulty-adjusted selection score, while admission rate preserves raw
success frequency.
Modification-count frequencies retain `m=0`, partial realization, full
realization, and `m=None` when an attempt produces no valid combined count. A
choice with committed attempts and no admissions is distinguishable from one
with no committed attempts; `choice_selected` events show any selected
attempts whose transitions rolled back. The eligible choice-size set is the union of descriptors and
supported sizes reported by all `choice_set_available` events, as defined in
architecture §6.6. Each `ChoiceStatistics` record carries the corresponding
choice's `modification_count_definition`, so its `m` frequencies are
interpretable from the summary. The remaining fields use the attempt,
admission, and modification records defined by the shared reporting contract.

Assessment and disposition counts follow candidate-outcome occurrences;
failure counts follow recorded component-failure events. Excursion frequencies
count each active or completed `InfeasibleExcursion` once and retain its step
count, feasible exit kind, and whether the exit admitted a Pareto solution.
Modification-count values remain specific to each choice's own scale.
The corresponding scale description appears in the choice metadata and in
each `ChoiceStatistics` record; a composition's description gives the
`min(m_f, m_g)` rule and points to its components' registered descriptions.
Compare Operators using outcome and archive statistics rather than comparing
their `m` values directly.
