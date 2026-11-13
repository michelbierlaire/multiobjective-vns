# Algorithm design: behavior and defaults

**Status:** Version 1 algorithm design; Pareto snapshot persistence included
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

Each search iteration selects one eligible base, one Operator choice, and one
size for that base-choice pair, then makes one `CandidateAttempt`. Outside an
infeasible excursion, eligible bases are current Pareto members with at least
one enabled, unexhausted choice at that base-choice pair's stored cursor. During an
excursion, its current infeasible solution supplies the base. Choice
selection first selects the standalone or composition category, then selects
a choice within that category. The standalone category includes pre-registered
composite Operators, which are available from the first iteration.

The engine evaluates and processes every candidate returned by the selected
choice in list order. The first candidate newly admitted to the known Pareto
archive sets the attempt's success flag; processing continues through every
later candidate in the same result. Each candidate updates the attempt's
staged registry and archive before the next candidate is considered, so later
candidates are compared with the archive produced by earlier ones. The next
search iteration starts after the complete result list has been processed,
subject to the terminal-failure rule in §2. That admission establishes success
for the application even if a later candidate removes it from the archive.
A successful application increases the choice's feedback score and resets
its size for that base to `k=1`. When the full list contains no archive
admission, the application is unsuccessful: its score decreases and its size
for that base advances by one. The next iteration selects a base, choice, and
that pair's current size anew. Size initialization, progression, exhaustion,
and feedback updates are defined in §2, Choice sizes and feedback.

The default base selector chooses from current Pareto archive members. A
caller-provided selector follows the eligible-record boundary in §2, Archive,
active pool, and base selection. An active infeasible excursion supplies its
current infeasible solution as the iteration base until that excursion ends.
This is the agreed excursion continuation rule.

The pseudocode defines one run() invocation. The caller must supply an
immutable `MultiobjectiveVNSConfig` and set its required
`max_candidates_per_attempt`; its other fields use the defaults in
architecture §6.6. Detailed transition, budget, failure, and event behavior
is owned by the rules in §2. The run caller may supply
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
        restore SearchState, including choice_size_cursors keyed by
            (base_solution_id, ChoiceId),
            Operator state keyed by OperatorId, policy and extension state,
            and the saved RandomSource stream state
        rebuild the private lookup-key index when configured
        checkpoint_ready = true
    else:
        restore registered stateful components to construction baselines
        create empty SearchState with every registered ordinary and
            pre-registered composite standalone choice, plus ordered pairs of
            available catalog Operators in the dynamic catalog, excluding
            pairs represented by pre-registered composites; initialize the
            choice catalog and component state to their baselines
        latest_checkpoint_reference = none
        checkpoint_ready = false

    create InvocationId and begin RunLogger
    if logger setup fails:
        return component_failure RunResult with current state and diagnostics

    reset per-invocation counters
    append invocation_started and emit a due progress report
    set pareto_snapshot_path to <log_directory>/<invocation-id>.pareto.json
    set snapshot_saved = false
    restore active InfeasibleExcursion from SearchState, if any

    if input starts a new search:
        process the complete initial-solution set
        after each initial-solution SearchCommit that admits a Pareto solution,
            atomically publish the current Pareto snapshot
        if initialization stops on a component failure:
            publish final viewer scopes from the last committed state,
                marking each successful export is_final=true
            finalize and return component_failure RunResult
        if no application-valid, application-feasible initial solution
            satisfies every configured objective upper bound:
            emit initialization_failed and invocation_stopped
            publish final viewer scopes from the last committed state,
                marking each successful export is_final=true
            finalize logs and raise InitialSolutionsError
        checkpoint_ready = true
        initialize every (Pareto SolutionId, ChoiceId in the logical-search
            choice catalog) size cursor to 1

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

        if an infeasible excursion is active:
            base = the current infeasible excursion solution
        else:
            eligible_bases = current Pareto members with at least one enabled,
                unexhausted choice at that base-choice pair's stored cursor
            if eligible_bases is empty:
                termination_reason = no_available_operator
                break
            base = select_base(eligible_bases, previous_iteration_context)
            if base selection fails:
                termination_reason = component_failure
                break

        restoration_required = active excursion has reached its step limit
        selectable_choices = choices whose cursor for (base.solution_id,
            choice_id) is at or below the effective maximum, are enabled, and
            satisfy the restoration requirement
        if selectable_choices is empty:
            termination_reason = no_available_restoration_operator if
                restoration_required else no_available_operator
            break
        remaining_requests = max_evaluation_requests - evaluation_requests
        call_candidate_limit = min(
            config.max_candidates_per_attempt, remaining_requests
        )
        eligible_choices = selectable_choices when call_candidate_limit > 0
        if eligible_choices is empty:
            termination_reason = budget_exhausted
            break

        emit choice_set_available with eligible counts by category and
            descriptors for choices first eligible in this invocation
        choose standalone or composition category using
            composition_selection_probability
        ask FeedbackPolicy for a distribution within that category
        sample one choice with the shared RandomSource
        emit choice_category_selected and choice_selected

        if an engine-enforced ceiling now prevents another CandidateAttempt:
            termination_reason = budget_exhausted
            break

        k = choice_size_cursors[(base.solution_id, choice.choice_id)]
        increment candidate_attempts
        attempt = call choice(
            base, k, ensure_feasible,
            max_candidates=call_candidate_limit,
        )
        process every returned candidate in list order through the normal
            identity, evaluation, registry, archive, pool, and outcome path;
            retain one CandidateOutcome per proposal and make each resulting
            staged registry and archive state the input state for the next
            proposal
        process the complete result list before beginning another search
            iteration; an archive admission sets success and processing
            continues through the remaining proposals
        a terminal component-processing failure follows its special rule
        first_admitted_candidate_index = the 1-based index of the first
            proposal newly admitted to the Pareto archive, or none
        application_success = first_admitted_candidate_index is not none
        update the selected ChoiceId's feedback score once for this attempt:
            increase for application_success; decrease otherwise
        if base remains a current Pareto solution:
            choice_size_cursors[(base.solution_id, choice.choice_id)] = 1
                when application_success, else k + 1
        if the same infeasible excursion solution remains the base:
            update its selected choice cursor by the same success rule
        for each solution newly entering the Pareto archive:
            initialize every ChoiceId in the current logical-search choice
                catalog to size 1 for that solution, including re-entry
        for every newly created ChoiceId, initialize size 1 for each current
            Pareto solution and active excursion base
        if an excursion starts or advances to a new infeasible solution:
            initialize every ChoiceId in the current choice catalog for that
                solution to 1
        remove cursor rows for Pareto solutions leaving the archive and for
            infeasible excursion solutions leaving the excursion
        prepare one SearchCommit containing the ordered candidate processing,
            feedback,
            component and excursion state, the updated base-choice cursors,
            the base
            selection context, and the ordered event batch
        if the SearchCommit succeeds:
            publish its replacement SearchState
            if one or more candidates were admitted to the Pareto archive:
                call RunLogger.publish_view for scope=pareto using this
                committed state and is_final=false
            if one or more candidates were admitted to the Pareto archive:
                atomically publish the current Pareto snapshot
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

    if the final archive is nonempty and no Pareto snapshot was published:
        atomically publish the final Pareto snapshot
    call RunLogger.publish_view for scope=registry and scope=pareto using the
        final committed state and is_final=true
    save the final checkpoint when due
    emit invocation_stopped and finalize logs
    return RunResult with committed SearchState, committed CandidateOutcomes,
        termination reason, latest checkpoint reference, log artifacts,
        viewer-export paths, Pareto snapshot path, and diagnostics
```

process_initial_solutions() returns completed after processing the full
initial set and finding at least one application-valid, application-feasible
solution that satisfies every configured objective upper bound,
no_eligible_initial_solution after processing the full set without one, or
component_failure when an invocation-scoped failure stops initialization.
Initial submissions are mandatory initialization work, so the engine processes
the full supplied set even when the wall-time limit elapses during
initialization. The run driver handles no_eligible_initial_solution by emitting
initialization_failed and invocation_stopped, finalizing the invocation, and
raising InitialSolutionsError as shown above. A completed initial set emits
initialization_completed under architecture §6.6.
process_operator_result() applies candidate processing to every proposal in a
choice's ordered output list and applies the modification-count and
composition rules in §2.
select_base() follows the archive, active-pool, base-selection, and
infeasible-excursion rules in §2. select_operator_choice() applies the
two-stage category and within-category selection rule defined there. The
selected choice's current_k is its committed cursor, not a per-iteration
counter reset by choosing a different base or choice.

The candidate-processing, feedback, excursion, cursor, and event changes are
prepared and committed under the single transition rule in §2, Search commit
boundary and ordering. Invocation event triggers and event order are
authoritative in architecture §6.6. Each attempted dynamic composition,
pre-registered composite, or promoted Operator expression contributes one
composition_result event to its successful CandidateAttempt commit,
including a composite that returns an empty list or records a component failure. A
first promotion contributes one composition_promoted event to the same
commit as its archive admission.

Pareto snapshots follow successful commits. An initial-solution commit that
adds an archive member publishes the snapshot before the next initial
submission. A CandidateAttempt may admit several solutions while its ordered
candidate list is processed; after that single attempt commit, the engine
publishes one snapshot containing the resulting current archive. A run with a
nonempty archive publishes a final snapshot if the invocation has not already
published one, which also gives a resumed run a usable snapshot when it makes
no archive admission. The package writes snapshots regardless of whether a
full `CheckpointStore` is configured. The file format, compatibility checks,
load operation, and failure behavior are authoritative in architecture §8.3.

### Archive, active pool, and base selection

The engine defines active-pool eligibility; `ExplorationPolicy` chooses
membership within that boundary. A record is eligible when it has a completed
`valid` assessment and satisfies every configured objective upper bound, or
when it has a completed `infeasible` assessment, satisfies every configured
objective upper bound, and the run uses relaxed feasibility. Records with an
`invalid` assessment, `ProcessingStatus.failed`, an objective-bound finding,
or an `infeasible` assessment in strict mode are ineligible. A current
infeasible excursion may still supply its own application-infeasible solution
directly as the next base, even when that record has an objective-bound
finding and remains outside the active pool. During reconciliation, every
current Pareto archive member is mandatory, and the policy may include any
subset of the eligible IDs. The request and result fields that enforce this
rule are defined in architecture §6.6.

| Concern | Rule |
|---|---|
| Objective comparison | Pareto dominance uses the ordered comparison supplied by each `ObjectiveFunction`: every coordinate must compare `less` or `equal`, with at least one `less`. |
| Visited-plan history | `SolutionRegistry` retains one record for every distinct plan established by `SolutionIdentity`, including infeasible, bound-exceeding, dominated, and objective-vector-duplicate plans. Repeated visits to the same logical plan reuse that record. |
| Pareto set | Unlimited capacity; retain one representative for each distinct objective vector among application-feasible, within-bounds registry records that are maximal under strict Pareto dominance. If another distinct plan compares equal in every objective to a current member, keep the earlier registered plan in the Pareto set and retain the new plan only in the registry. |
| Active pool | The default policy retains currently active eligible records, adds each newly registered eligible record and every current Pareto archive member, and makes no other automatic removals. Objective-bound-exceeding records remain outside the pool. Records removed by cleanup remain inactive under the default policy. The pool is unbounded by default. |
| Cleanup | Set the active pool to exactly the current Pareto archive; preserve the complete registry. |
| Base eligibility | Outside an infeasible excursion, eligible bases come only from the current Pareto archive and have at least one enabled, unexhausted Operator choice at that base-choice pair's stored cursor. The active pool remains available to exploration policies and custom search extensions, while archive membership defines the base universe. During an excursion, its current application-infeasible solution is the sole eligible base, supplied directly by excursion state even when objective-bound findings or an `invalid` assessment keep it outside the pool. When the excursion ends, base eligibility returns to the current Pareto archive. An infeasible record with a completed `infeasible` assessment and no objective-bound finding remains in the pool until cleanup; an objective-invalid or objective-bound-exceeding infeasible record remains in the registry only. |
| Base selection | At every iteration outside an active excursion, call `BaseSelectionPolicy` with the eligible current Pareto members and the previous iteration's base and admission result. The previous base remains eligible whenever it still belongs to the archive and has an enabled, unexhausted choice. The package default chooses the eligible member with the fewest prior `CandidateAttempt`s and breaks ties randomly. A custom selector may choose any member supplied in the request, including the previous base. Per-base counts persist across invocations. During an active excursion, its current infeasible solution supplies the base directly; after the excursion ends, that solution is ineligible for later base selection. |

Every `ArchivePolicy` uses the exact Pareto rule defined in architecture §6.4.
The solution registry is the history of distinct visited plans; the Pareto set
is its current, objective-vector-unique frontier. For a newly identified,
application-feasible solution satisfying every objective upper bound, a
vector strictly dominated by a current Pareto member receives
`SearchDisposition.dominated` and `ArchiveStatus.Dominated`. A vector that
compares equal in every objective to a current member receives
`SearchDisposition.objective_vector_duplicate` and
`ArchiveStatus.Objective-vector duplicate`; it remains in the registry, while
the earlier registered member stays in the Pareto set. A nondominated vector
not already represented is admitted with `SearchDisposition.archived` and
`ArchiveStatus.Pareto optimal`; the policy removes exactly the current Pareto
members that the new vector strictly dominates, and those former members
receive `ArchiveStatus.Rejected`. A newly identified solution with one or
more `ObjectiveBoundFinding` records receives
`SearchDisposition.objective_bound_exceeded`, remains in the registry, and
bypasses the archive policy. A duplicate proposal receives
`SearchDisposition.duplicate` and does not invoke the archive policy; it
retains the stored bound findings.

When a distinct plan ties a current Pareto objective vector, issue a Python
`UserWarning` immediately and record the disposition in the candidate event
stream. At finalization, count the distinct registry records marked
`Objective-vector duplicate`. If the count is positive, include it in
`ExecutiveSummary.objective_vector_duplicate_count` and render the warning in
the default summary: tied objective vectors may indicate search symmetry and
can spend evaluations on equivalent trade-offs, slowing progress.

An attempt succeeds for size and feedback purposes only through one or more
Pareto admissions. Therefore, an attempt whose returned candidates all exceed
configured objective bounds and produce no archive admission follows the
ordinary no-admission score and cursor update.

The engine checks each `ArchiveDecision` against these rules before applying
it. A custom policy result with an inconsistent admission or removal set
follows the common policy failure behavior in Failure handling.


### Choice sizes and feedback

Each ordinary, pre-registered composite, and promoted standalone Operator,
and each available dynamic composition supports every integer size from 1
through its finite maximum. A composite or dynamic composition's maximum is
the smaller of its component maxima; a promoted Operator retains its source
composition's maximum. The run-level cap can reduce any maximum. When
`config.max_neighborhood_size` is `None`, the cap is the largest maximum
declared by an ordinary Operator registration.

The checkpointed size cursor is keyed by `(base_solution_id, ChoiceId)`.
Each Pareto solution has a separate cursor for every currently available
ordinary, pre-registered composite, promoted, and dynamic-composition choice.
Whenever a solution enters the Pareto archive, including when a previously
removed solution re-enters, the engine initializes every choice in the
logical-search catalog for that solution to `k=1`, including a choice
temporarily disabled for the current invocation. A solution that remains in
the archive keeps its existing cursors when another solution is admitted. A
choice created later receives a `k=1` cursor for every current Pareto solution
and active excursion base. A
candidate list is processed fully and in order. Its first newly admitted
candidate marks the selected base-choice application successful; the selected
pair's cursor resets to `k=1`. If none of the candidates is admitted, that
pair's cursor advances to `k+1`. A duplicate of a known Pareto solution is
not a new admission. The pair's cursor advances on every no-admission
application, including an empty result, duplicate, invalid, infeasible,
dominated, failed evaluation, or committed Operator failure. A pair whose
cursor exceeds its effective maximum is exhausted for that base. Base
selection excludes a Pareto solution when every choice is exhausted or
disabled there; other Pareto bases remain eligible.

During an infeasible excursion, the current infeasible solution receives a
temporary cursor for each choice in the current logical-search catalog,
initialized at `k=1` when that solution becomes the excursion base. Success
and failure update the selected
excursion-base-choice pair under the same rule. A new infeasible excursion
solution starts with `k=1` cursors; the prior excursion solution's cursors are removed.
When the excursion ends, its temporary cursors are removed. A Pareto solution
that leaves the archive also loses its cursor row. Reaching the maximum size
without success advances that base-choice cursor beyond the maximum and
exhausts only that pair. Cursor changes, newly initialized rows, and row
removals commit with the `CandidateAttempt` and persist through checkpoint and
resume. An Operator used as a standalone choice and in one or more
compositions has a distinct `ChoiceId` cursor for each base and selectable
choice. Feedback score, by contrast, is attached to the `ChoiceId` across
bases and sizes.

At each iteration, the engine builds the eligible choice set for the selected
base from choices that are enabled, not exhausted for that base, compatible
with the current feasibility requirement, and eligible under engine-enforced
budgets. The
`choice_set_available` event records the set's total, standalone, and
composition counts. It also reports each choice's descriptor the first time
that choice becomes eligible during the invocation; later events report only
the counts. The descriptor includes the choice's current_k for the event's
selected base and its effective maximum. The union of first-reported
descriptors supplies the supported choice-size pairs for invocation
statistics, including pairs with zero attempts; those statistics aggregate
attempts across bases.

Operator-choice selection has two stages. First, the engine partitions the
eligible descriptors into standalone and composition categories and chooses
a category using config.composition_selection_probability. When both
categories contain eligible choices, the composition category has that
probability and the standalone category has its complement. When only one
category is nonempty, the engine selects that category with probability one.
Selecting one ordinary, pre-registered composite, or promoted Operator is a
standalone choice; selecting an ordered pair that has no pre-registered
composite is a dynamic composition choice. The dynamic pair matching a
pre-registered composite's exact ordered component IDs is omitted, so the
same operation has one choice identity and feedback record, with a separate
base-specific size cursor.
Thus `composition_selection_probability` applies only to dynamic composition
choices. Pre-registered composites are part of the standalone category.
OperatorGroup hints apply only to dynamic composition choices; the feedback
section below defines their sampling effect. A new Pareto admission initializes
cursor rows only for that admitted solution; it leaves cursor rows for all
other Pareto solutions unchanged.
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
| OperatorChoice | One selectable ordinary, pre-registered composite, or promoted Operator, or one ordered dynamic composition of two available Operators. Its ChoiceId identifies its feedback score; its size cursor is specific to each base solution. |
| PreRegisteredComposite | A package-executed ordered pair registered before search under its own OperatorId. It is selected as a standalone choice from the first iteration. |
| PromotedOperator | A package-identified Operator that executes a previously admitted dynamic composition and can itself be selected or composed. It has a standalone ChoiceId and a per-base cursor in addition to the source composition's separate ChoiceId score and per-base cursor. |
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
  candidates and every returned proposal; the base remains the starting point
  for the repair attempt. Every returned proposal is guaranteed feasible, and
  the Operator returns an empty list when its bounded attempt finds no
  qualifying candidate.
- An InfeasibleExcursion begins when an ordinary OperatorChoice from a
  feasible base produces an infeasible candidate. Its anchor is the selected
  feasible base's SolutionId; its current solution is the infeasible candidate
  selected from the attempt's ordered outcomes. For excursion progression,
  use the first processed outcome in list order whose stored application
  evaluation provides representation and feasibility findings. A known
  infeasible finding advances or starts the excursion; a known feasible
  finding ends it. Other list outcomes still receive ordinary registry,
  archive, pool, and outcome processing.
- In relaxed mode, max_infeasible_steps is a required positive limit. Each
  ordinary OperatorChoice whose first excursion-eligible outcome is known to
  be representation-valid and infeasible adds one step, regardless of the
  value of its modification count. This includes a result whose objective
  values are unusable and whose summary assessment is invalid, provided the
  stored application findings establish representation_valid=True and
  feasible=False. A duplicate infeasible proposal also counts as a step. An
  attempt with no excursion-eligible outcome adds no step. Once the limit is
  reached, the next iteration selects a feasibility-restoration Operator
  choice whose call requests ensure_feasible=True.
- A restoration choice uses its cursor for the current infeasible excursion
  base. The cursor follows the same advancement and reset rule as every other choice. The excursion stays
  active until a feasible candidate closes it; another iteration selects a
  restoration choice when the current result remains infeasible and budget
  remains.
- If the first excursion-eligible outcome from a restoration attempt has the
  anchor's SolutionId, record a return to the starting solution, reuse its
  stored evaluation through duplicate handling, end the excursion, and leave
  archive membership unchanged. A
  different feasible result follows ordinary registry and archive handling
  and ends the excursion.
- A feasible excursion-eligible candidate produced before the limit also
  follows ordinary registry and archive handling and ends the excursion.
- Excursion transitions use the first excursion-eligible outcome only when its
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

#### Candidate lists, modification counts, and cursor updates

- `MultiobjectiveVNSConfig.max_candidates_per_attempt` is a required positive
  integer selected by the caller. Before each choice call, the engine sets the
  effective output limit to the smaller of this value and the remaining
  evaluation-request allowance. A call returns at most that many proposals.
  Operators return fewer when they cannot produce more distinct candidates
  under their bounded move-generation rules. An empty list records that this
  call found no candidate; another call at size `k` may sample different
  moves.
- Every proposal has its own positive modification count `m` in `1..k`,
  measured on the selected choice's declared scale. An empty list represents
  zero modifications for the call. For a composition, each final proposal's
  `m` is `min(m_f, m_g)` for the component proposals that produced it.
  `m` describes the realized move; it does not determine application success,
  the built-in score update, or cursor progression. Those depend on whether
  any returned proposal is admitted to the Pareto archive. A partial move
  (`m < k`) with no admission advances the cursor, while an admitted proposal
  resets it, regardless of `m`. A custom `FeedbackPolicy` may use the
  modification counts supplied in its observation under its own policy.
- The engine submits every returned final proposal in list order through
  normal identity resolution, evaluation or stored-evaluation reuse,
  registry, objective-bound screening under architecture §5.6, archive,
  active-pool, and outcome processing. Each proposal has a
  1-based `candidate_index` in its `CandidateOutcome` and `candidate_processed`
  event. All proposals from one call are processed in order within one
  `CandidateAttempt` commit. After each proposal, its staged registry and
  archive changes become the state used to process the next proposal. A Pareto
  admission sets the attempt's success flag, and processing continues through
  the end of the returned list before another search iteration begins. The
  terminal-failure rule in §2 governs a proposal that encounters a terminal
  processing failure. The effective output limit never exceeds the remaining
  evaluation-request allowance, so the complete list can be processed without
  exceeding that hard ceiling; duplicate proposals may use fewer evaluation
  requests.
- An attempt is successful for cursor progression when one or more of its
  processed proposals are admitted to the known Pareto archive. Success resets
  the selected choice's cursor to `k=1`, even when several proposals are
  admitted. Otherwise the cursor advances to `k+1`. A duplicate of an existing
  Pareto solution is not a new admission. Any attempt without an admission
  advances the cursor, including an empty result, duplicate, invalid,
  infeasible, dominated, failed evaluation, or committed Operator failure.
  When `k` is the choice's effective maximum, this increment marks the choice
  exhausted.
- Every selected choice call consumes one invocation candidate-attempt count,
  regardless of the number of returned candidates. Its candidate outcomes and
  archive admissions may number from zero through the effective output limit.
  An attempt whose `SearchCommit` rolls back does not publish cursor, feedback,
  candidate, or state changes; its invocation counter remains counted as
  specified by the commit rule.
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
  every choice's saved cursor. A fresh search starts every ordinary and
  pre-registered composite standalone choice and every initial dynamic
  composition at `k=1`. A choice created later starts at `k=1` for every
  current Pareto solution and active excursion base.
- When the enabled eligible choice set is empty, stop with
  no_available_operator. When the infeasible-step limit is reached and the
  enabled, unexhausted feasibility-restoration choice set is empty, stop with
  no_available_restoration_operator.

#### Feedback selection and score updates

`config.feedback_selection_mode` selects the built-in rule for choosing among
the eligible choices in the category already selected by the engine. Let C be
those choices and n = |C|:

```text
uniform:
    p(choice | category) = 1 / n

performance_matching:
    group_prior(choice) = 1 if category is composition
                              and choice.shared_group_ids is not empty
                          else 0
    a_i = score(choice_i) + group_prior(choice_i), for each choice i in C
    a_max = max(a_i for i in C)
    w_i = exp(feedback_mu * (a_i - a_max))
    q_i = w_i / sum(w_j for j in C)
    p(choice_i | category) = feedback_epsilon
                           + (1 - n * feedback_epsilon) * q_i
```

The exponential expression is normalized across C; subtracting the maximum
adjusted score keeps its largest exponent at zero without changing the
distribution. `feedback_mu` is finite and nonnegative. At zero, the softmax is
uniform. Larger values put more probability on higher adjusted scores. As
`feedback_mu` grows, a unique highest-scoring choice approaches probability
`1 - (n - 1) * feedback_epsilon`, while every other choice approaches the
floor `feedback_epsilon`; tied highest scores share the probability above
their floors. `feedback_epsilon` is the conditional minimum probability of
each choice in the selected category and must satisfy
`0 < feedback_epsilon < 1 / n` for every eligible set of size n. The default
policy reports a policy-decision failure if a performance-matching request
contains a set too large for the configured floor. The engine applies the
standard policy-failure behavior: it records a `ComponentFailure` for the
`FeedbackPolicy` and stops the invocation with `component_failure`. Choose
epsilon below the inverse of the largest category size expected in the search.
The configuration default is `1e-6`.

Because the softmax values `q_i` sum to one and are nonnegative, the resulting
probabilities sum to `n * feedback_epsilon + (1 - n * feedback_epsilon) = 1`
and each is at least `feedback_epsilon`.

The added unit is a one-success prior for a composition choice whose component
Operators share at least one `OperatorGroup`; it is computed for selection and
is separate from the persisted score, which still starts at zero. The hint
multiplies that choice's unnormalized odds by `exp(feedback_mu)` relative to an
otherwise equal ungrouped choice. With the default `feedback_mu=1.0`, this is
about 2.72-to-1 odds before the floor correction. This makes the group
preference tunable by the same intensity parameter while accumulated success
scores shape later choices. `uniform` mode ignores scores, group hints,
epsilon, and mu. A custom `FeedbackPolicy` receives the group IDs in each
`ChoiceDescriptor` and defines its own use of them.

The engine selects the standalone or composition category first using
`composition_selection_probability`. If both categories are eligible, a
choice's unconditional probability is the category probability multiplied
by its conditional probability within that category. If only one category is
eligible, it has probability one. In `performance_matching` mode,
`feedback_epsilon` is the minimum conditional probability of each choice
within the selected category. A choice's unconditional probability is its
category probability multiplied by its conditional probability. In `uniform`
mode, every choice in the selected category has equal probability, regardless
of score.

Every choice has a nonnegative integer score that starts at zero, including
ordinary Operators, pre-registered composites, dynamic compositions, and
promoted Operators. On each successfully committed `CandidateAttempt`, the
engine processes the complete ordered list of candidates. The first candidate
newly admitted to the known Pareto archive marks that choice application
successful; later candidates are still evaluated and processed. For the
selected choice with current score `s`, one committed attempt updates its
score as follows:

```text
if at least one candidate is admitted to the Pareto archive:
    next_score = s + 1
else:
    next_score = max(0, s - 1)
```

Apply one score update per choice application, even if multiple candidates
are admitted. A duplicate of a known Pareto solution is not a new admission.
A rolled-back attempt changes no score.

Scores are attached to `ChoiceId` and shared across bases and sizes. Size
cursors are attached to `(base_solution_id, ChoiceId)`. Thus an unsuccessful
application decreases the choice score once and advances only the selected
base-choice cursor; a successful application increases the choice score once
and resets only that pair's cursor to `k=1`. The first Pareto admission in a
candidate list establishes success for the attempt even if a later candidate
removes that solution from the archive. An infeasible proposal with no Pareto
admission decreases the producing choice's score; a later restoration
application that admits a Pareto solution increases the restoration choice's
score.

The engine validates a custom or built-in distribution for complete choice
coverage, finite probabilities in `[0, 1]`, and a sum within `1e-12` of 1.
It divides each accepted probability by the computed sum before sampling, so
the distribution used by the shared `RandomSource` sums to 1 within the
engine's arithmetic. A custom `FeedbackPolicy` receives one observation for
each committed attempt and defines its own score update and score state. The
built-in score initialization, unit increases and decreases, zero floor,
softmax intensity, and per-choice probability floor above are the package
defaults.

The log reports attempts with at least one admission separately from the
number of individual archive admissions. Empty, duplicate, invalid,
infeasible, dominated, and failed attempts with no admission are
unsuccessful for score and size progression. A rolled-back attempt contributes
no committed feedback observation. Feedback reset sets every tracked choice
score to zero and clears attempt statistics; the promoted composition catalog
and its identities remain intact.

### Candidate submission, commit boundary, and maintenance

The architecture defines the candidate and evaluation records and the
identity, evaluation, duplicate, archive, and status contracts. The algorithm
uses that same submission path for each initial solution and, in returned-list
order, each final candidate from a standalone Operator or composition. A
composition's intermediate values remain internal to the composition. An
empty result has no candidate submission.

Every candidate attempt is prepared as one `SearchCommit`. Its ordered
candidate submissions, archive and active-pool changes, excursion state, the selected choice's size
cursor, feedback observation, component state, the shared RandomSource state,
and the corresponding event batch form that commit. The engine prepares all
effects before publishing the replacement SearchState. It appends the event
batch atomically and publishes the new state and CandidateOutcome collection
only after the append succeeds. The event order and commit sequence are
defined in architecture §6.6.

For a committed attempt, the selected cursor is set to 1 when one or more
submitted candidates are newly admitted to the Pareto archive. Every other committed
attempt sets it to k+1. This update shares the commit with the attempt's
candidate and feedback effects. A rolled-back attempt retains its previous
cursor along with the previous SearchState; its invocation attempt counter
remains counted. An archive-comparison failure follows the failure table:
roll back the candidate submission, then commit the failed-attempt record and
its non-admission cursor update when that transition can be recorded safely.

Initial solutions use the normal submission path and are all processed in
input order. The complete set must contain at least one application-valid,
application-feasible solution whose objective vector satisfies every
configured upper bound. A candidate-processing component failure stops
initialization at that submission and returns the previously committed
initial outcomes. If the full set has no such solution, the engine emits
initialization_failed and invocation_stopped, finalizes logging, and raises
InitialSolutionsError. No Operator call or checkpoint save occurs on that
path. Initial evaluation requests are excluded from the evaluation-request
budget; their processing time counts against the wall-time limit.

Maintenance runs against a working copy of the supplied SearchState. Active-
pool cleanup and feedback reset are separate SearchCommits; when both are
requested, cleanup commits first. Cleanup sets active-pool membership to the
current Pareto archive exactly. Feedback reset sets every tracked choice
score to zero and clears feedback counts while preserving the registry,
archive, promoted composition catalog, composition identities, and all
choice-size cursors. Maintenance commits produce their standard events and immediate
checkpoint saves when a CheckpointStore is configured. The supplied state
remains unchanged.

### Operator-result guarantee verification

The architecture defines Operator and feasibility-restoration guarantees.
For each newly identified final proposal, the engine checks available
application findings against the selected Operator choice's guarantee. A
proven violation is attached to that proposal's `CandidateOutcome` and
registry record, and disables the selected standalone Operator or composition
for later iterations in the invocation. A duplicate reuses its stored
processing outcome and receives no new guarantee check. A composition's final
Operator is responsible for the guarantee on every final proposal. A
committed attempt with no archive admission follows the same `k+1` cursor
update as every other non-admission.

### Operator groups and compositions

An `OperatorGroup` is a user-provided set of ordinary or pre-registered
composite Operators with a
stable `group_id`; membership expresses which combinations may be promising.
A composition is an ordered pair `(f, g)` that applies `g` to the value
generated by `f`, written `g(f(x))`. The order matters: `(f, g)` and `(g, f)`
are separate choices with separate feedback. A shared group gives a pair a
soft selection hint.

The available Operator catalog for a fresh search starts with the ordinary
and pre-registered composite Operators supplied by the caller, together with
promoted Operators loaded from the persistent catalog described below. It
grows when any final proposal
from a dynamic composition receives the `archived` disposition and enters the
known Pareto archive. At that first admission, the algorithm
promotes the composition as a package-identified Operator whose identity is
deterministically derived from its source `CompositionId`.
The promoted Operator executes the same ordered composition and retains its
size limit, modification-count rule, feasibility-restoration capability, and
the `SolutionId` of the first final candidate that entered the archive for the
current logical search. When
used in a later composition, it receives the same requested `k`; its internal
calls follow the composition rules recursively, and only the last component
receives the outer feasibility request. Its returned modification count is the
minimum realized count across its component calls.
It becomes available as a standalone choice and as a component in future
compositions after the admitting `CandidateAttempt` commits. Each promoted
composition expression has one catalog entry; later admissions from that
expression reuse it. The promoted Operator's standalone choice starts with
zero. Its source composition retains its separate score, increased once by
the attempt that admitted the candidate. Any new dynamic composition starts
with score zero. The promoted Operator also becomes
available as a standalone choice and as a component of new ordered
compositions.

Every ordered pair of currently enabled available Operators is eligible,
including self-pairs, except an exact ordered pair already represented by a
pre-registered composite Operator. Each other ordered pair of available Operator IDs has its own
`CompositionId` and promotion entry, including expressions that expand to the
same sequence of user-registered Operators through different composition
trees. This rule applies to ordinary, pre-registered composite, and promoted
Operators, so a promoted composition can combine with another Operator or
with another promoted composition. A composition that is itself admitted is
promoted in the same way, allowing the catalog to grow through successive
successful combinations. A promoted Operator inherits the group IDs shared by
both of its immediate components; those inherited groups continue to provide
soft hints in later combinations. Ordinary and pre-registered composite
Operators retain their declared group memberships. A pre-registered composite
also inherits the groups shared by its two component Operators.

A standalone ordinary Operator failure disables that Operator for the
invocation. Promoted Operators and pre-registered composites that use the
disabled Operator become unavailable for the invocation, along with dynamic
compositions containing any unavailable Operator. A failure in a component
call made by a selected dynamic composition or pre-registered composite
disables only that selected composite choice for the invocation; its component
Operators remain usable in other choices. These effects follow the failure
scopes in Failure handling.

For a new logical search, the dynamic choice catalog is seeded with every
ordered pair of available catalog Operators, including file-loaded promoted
Operators, before the first choice
selection, except pairs represented by pre-registered composite Operators.
When a composition is promoted, the catalog adds every ordered pair in which
the new promoted Operator appears, paired with every Operator in the logical
search's Operator catalog, including itself, before the next iteration.
Existing ordered-pair identities are reused. Each new pair receives its
composition identity and zero score before it is selectable;
its size cursor starts at `k=1` for every current Pareto base. A pair with an
Operator temporarily disabled for the current invocation remains in the
catalog and becomes eligible again when that invocation ends. Attempts
update the selected choice's score; component-call details are retained for
diagnostics. Each promoted Operator's standalone per-base cursor is separate
from the source composition's per-base cursor. Every new choice starts with
score zero and follows the selection and update rules in the preceding
section.

For requested size `k`, both component Operators run at size `k`; the
composite's maximum supported size is the smaller of their maxima. The first
Operator `f` receives the selected base, `ensure_feasible=False`, and the
effective candidate limit. It returns an ordered list of intermediate
proposals. For each intermediate in order, the second Operator `g` receives
that solution as its base, the selected choice's `ensure_feasible` request,
and a `max_candidates` equal to the remaining final-output capacity. The
composition appends each `g` proposal in order, then advances to the next
intermediate until it fills the configured limit or `f`'s list is exhausted.
Each final proposal's modification count is `min(m_f, m_g)` for its two
component proposals, and its metadata comes from `g`.

Every `f` output is an internal composition value: the engine does not submit
or evaluate it, resolve its identity, or create a registry record or
`CandidateOutcome`. The engine submits and evaluates each final `g` output
through ordinary candidate processing. Application validity and feasibility
are checked for each final candidate, as for a standalone Operator. If `f`
returns an empty list, the composition makes no `g` calls and returns an empty
list. If a `g` call returns an empty list, the composition continues with the
next intermediate. A failure or malformed result in any component call fails
the whole composition and discards all partial final outputs.

The selected composite's feasibility request applies to its final result. The
engine checks `g`'s output against the application's representation and
feasibility findings. A dynamic composition or pre-registered composite is
eligible as a feasibility-restoration choice when
its second Operator `g` has the feasibility-restoration capability, because
`g` receives the request and produces the submitted candidate. A promoted
Operator carries the restoration capability of the final Operator in its
ordered expression.
`InfeasibleExcursion` and its step count track the ordered final candidates
as one search transition. Each final candidate has its own `CandidateOutcome`;
an empty result has no candidate outcomes.

A successfully committed dynamic-composition or pre-registered-composite
attempt contributes one feedback observation containing all processed final
candidate outcomes. A promoted Operator uses its standalone choice's feedback record. A
rolled-back composite attempt contributes no committed feedback statistic. The first final
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

#### Promoted-composition catalog across independent searches

The configured `PromotedCompositionCatalog` file preserves promoted
composition definitions across fresh logical searches. Before processing the
initial solutions of a fresh search, the engine loads the entries associated
with the current `ProblemInstanceId` and adds those generated Operators to the
initial Operator catalog. Loaded entries are available as standalone choices
and as components of dynamic compositions from the first iteration. They
start with feedback score zero and size-one cursors for each Pareto base. The
file stores operator definitions; each logical search state carries its own
solution records, feedback statistics, and cursor history.

During resume, the checkpoint's `SearchState` supplies the promoted catalog
for that logical search. A promotion committed during either a fresh or
resumed search is also written to the file, so a later fresh search can use
it.

The file is UTF-8 JSON with top-level fields `format_version: 1` and
`catalogs`. `catalogs` maps the stable textual `ProblemInstanceId` to a record
with `compatibility` and `promotions` fields. `compatibility` has three arrays:
`objectives` contains objects with `objective_id` and `configuration_id`;
`operators` contains objects with `catalog_kind`, `operator_id`,
`configuration_id`, `component_operator_ids`, `max_neighborhood_size`,
`feasibility_restoration`, and `modification_count_definition`; and
`operator_groups` contains objects with `group_id` and `operator_ids`. The
`operators` array lists ordinary registrations followed by pre-registered
composites in registration order. `component_operator_ids` is `null` for an
ordinary Operator and an ordered two-element array for a pre-registered
composite. Group member IDs are sorted by stable text. The engine accepts a
catalog only when this declaration matches the current registrations. A
mismatch, malformed record, unsupported format version, or reference to an
unknown Operator or group raises `ComponentSetupError` before invocation
logging begins.

Each `promotions` entry is stored in promotion order and records the generated
Operator ID text, the ordered component Operator ID texts, the component
configuration ID texts, maximum neighborhood size, feasibility-restoration
flag, inherited group ID texts, and modification-count definition. Component
references may name earlier promoted entries, so the order reconstructs the
composition graph. The file retains the definition and provenance of each
promotion; the `SolutionId` that triggered a promotion remains local to the
logical search and is `None` in reports for a file-loaded entry.

After a `CandidateAttempt` commit adds a promotion, the engine atomically
replaces the catalog file while preserving entries for other problem IDs. A
missing file starts an empty catalog. `promoted_composition_catalog_path=None`
disables this cross-run persistence. The default path is
`./multiobjective-vns-promoted-compositions.json`, resolved when the engine is
constructed. A single writer may use a catalog file at a time; independent
writers use distinct paths. A read or compatibility error raises
`ComponentSetupError`. A write failure after a search commit records an
infrastructure `ComponentFailure`, keeps the committed in-memory state, and
stops the invocation with `component_failure`.

Both feedback-selection modes record committed feedback observations. Callers
reset them through `MultiobjectiveVNS.maintain(reset_feedback=True)`. A reset
sets the score of each ordinary and pre-registered composite Operator,
promoted Operator, and tracked dynamic composition to zero and clears
accumulated choice counts. It preserves the promoted catalog and composition
identities and leaves the solution registry, archive, active pool, and
choice-size cursors unchanged. Feedback scores and counts carry across
checkpoint resume unless explicitly reset.

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
  Operator search. An initial set without an application-valid,
  application-feasible solution satisfying every configured objective upper
  bound raises
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
- A choice call can return up to `max_candidates_per_attempt` final
  proposals. Before selection, the engine sets the call's effective candidate
  limit to the smaller of that configuration value and the remaining
  evaluation-request allowance. Thus every returned proposal can be
  processed even if each requires a new evaluation request; duplicate
  proposals may use fewer requests. When no request allowance remains, no
  choice is eligible and the invocation ends with `budget_exhausted`. This
  check is independent of `BudgetPolicy`. The default request allowance
  equals the total-attempt cap.
- The per-invocation `max_evaluation_requests` cap uses the counter and source
  rules in Evaluation rules above. Resume starts a fresh counter; committed
  registry evaluations remain reusable.

The engine applies `composition_selection_probability` regardless of which
`FeedbackPolicy` is registered. The built-in `FeedbackPolicy` reads the
selection mode and exploration floor from `config`. A
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
| `ObjectiveFunction.compare` raises or returns a value outside `ObjectiveComparison` while testing archive dominance | During initial-solution processing, apply the early-stop rule in the initial-solution section above. During a selected search attempt, stop processing the returned proposal list at the affected candidate. Roll back that candidate submission and discard later proposals; retain the successfully processed prefix. Commit the prefix, its outcomes, the valid result's modification counts, the failed-attempt diagnostic, and one feedback observation as one `SearchCommit`. If the prefix admitted any Pareto solution, reset the selected base-choice cursor and increase the selected choice's score once; otherwise advance the cursor and decrease the score once. Count any evaluation request already made, then stop the invocation with `component_failure`. If the prefix-and-failure commit is rolled back, its feedback state and events are discarded under the commit rule above. |
| `ObjectiveFunction.compare` raises or returns a value outside `ObjectiveComparison` while checking an objective upper bound | Attribute the failure to that objective with `phase=objective_bound_comparison`. During initial-solution processing, preserve previously committed initial outcomes, stop initialization, return a `component_failure` result, and make no checkpoint. During a selected search attempt, use the same prefix-and-failed-attempt commit as an archive-comparison failure: roll back the affected proposal and later proposals, retain the successfully processed prefix, count any evaluation request already made, and stop with `component_failure`. |
| `ObjectiveFunction.compare` raises or returns a value outside `ObjectiveComparison` while matching checkpoint and current objective bounds | Raise `ComponentSetupError` with a `ComponentFailure` at `phase=objective_bound_comparison` before invocation setup. A valid comparison that establishes unequal bounds raises `ResumeCompatibilityError`. |
| Any registered policy callback raises or returns an invalid result | Apply the common policy-failure rule above; it covers search and maintenance. |
| An ordinary standalone Operator raises | Count one failed `CandidateAttempt`; its successful commit includes one feedback observation with `modification_counts=None`, no candidate outcomes, and the failure diagnostic. Record an invocation-scoped diagnostic, disable the selected Operator for this invocation, and continue with the next iteration. Every composite choice that depends on the disabled Operator becomes unavailable. Other enabled choices remain available to subsequent iterations. |
| A component Operator raises or returns a malformed result during a dynamic composition, pre-registered composite, or promoted Operator execution | Count one failed `CandidateAttempt`; record an invocation-scoped `ComponentFailure` with `source` set to the failing component's `component_id`, `choice_id` set to the selected dynamic-composition or standalone-composite choice, `phase=operator_call`, and a `reason` describing the failure. The failed-attempt commit includes a feedback observation with `modification_counts=None`, no candidate outcomes, and the failure diagnostic. Disable only that selected composite choice for this invocation and continue with the next iteration. Its component Operators remain available in other choices; see Operator groups and compositions. |
| An ordinary standalone Operator returns a malformed `OperatorResult` | Malformed means the result violates the `OperatorResult` contract in architecture §2, Core terminology, including an over-limit list, invalid candidate proposal, or invalid modification count. Count one failed `CandidateAttempt`; its successful commit includes one feedback observation with `modification_counts=None`, no candidate outcomes, and the failure diagnostic. Record an invocation-scoped `ComponentFailure` with the Operator's `component_id` as `source`, `phase=operator_call`, `choice_id` set to the choice ID, and a `reason` describing the violation. Disable the Operator for this invocation and continue with the next iteration. |
| A checked `RandomSource` draw raises or returns a non-finite value, a non-`float`, or a value outside `[0, 1)` | Attribute the failure to `RandomSource` with `phase=random_generation`. Before a choice is selected, apply the out-of-attempt rule above: stop with `component_failure`, append the invocation failure event, preserve the latest committed state, and create no attempt or feedback observation. During a selected `CandidateAttempt`'s Operator invocation, commit the attempt as failed with `modification_counts=None`, no candidate outcomes, and one feedback observation containing the failure; capture post-call state for any stateful Operator that was invoked. Record no Pareto progress or candidate-state change, then stop with `component_failure`. The selected choice is not disabled. If preparing or appending this attempt commit fails, apply the ordinary rollback rule and discard the feedback update and event. |
| `RandomSource.capture_state()` raises or returns a malformed payload while preparing a `SearchCommit` | Record a `ComponentFailure` for the source with `phase=random_state_capture`. Discard the pending transition and event batch, stop with `component_failure`, and return the previous committed `SearchState`, including its prior stream payload. A resumed run restores that payload; a fresh run restores the construction baseline. |
| A stateful Operator's `capture_state` raises or returns a value outside its contract while preparing an attempt | Record a `ComponentFailure` for that Operator with `phase=operator_state_capture` and the active `choice_id`; discard the pending attempt transition, restore participating components to their pre-attempt state, and stop with `component_failure`. The invocation counters retain the attempted call; `SearchState` remains at the previous commit. |
| The `FeedbackPolicy`'s `capture_state` raises or returns a value outside its contract after `observe()` or `reset()` | Record a `ComponentFailure` for the feedback policy with `phase=policy_state_capture`. Discard the pending candidate-attempt or maintenance transition, restore the policy to its pre-commit state, and stop with `component_failure`. A candidate-attempt counter remains counted; maintenance counters remain zero. The last committed `SearchState` is returned. |
| A `SearchExtension.prepare_commit()` raises, returns an undeclared event or invalid payload, or fails state capture | Record a `ComponentFailure` whose `source` is the extension's `component_id` and whose phase is `extension.<namespace>.prepare_commit` or `extension.<namespace>.state_capture`. Discard the pending transition and event batch, restore the extension and other participating stateful components to their pre-commit state, and stop with `component_failure`. Preserve invocation counters for work already attempted and retain the last committed `SearchState`. Record the failure as an invocation event with `commit_sequence=None` when logging remains available. |
| `EventObserver` fails | Record a `ComponentFailure` with `phase=event_delivery`. Deactivate that observer, notify remaining observers when available, and continue the search. |
| `SolutionIdentity.lookup_key` raises or returns a value outside its contract while rebuilding the index from restored solutions | Raise `ComponentSetupError` with a `ComponentFailure` whose `source` is the `SolutionIdentity` component and whose phase is `identity_index_rebuild`. This occurs after compatibility validation and solution restoration, but before invocation ID creation or logger setup; no `RunResult` or `MaintenanceResult` exists. |
| `RunLogger.begin` fails | Record a `ComponentFailure` with `phase=run_logging`. Stop with `component_failure` before invocation work; return the current state in `RunResult` or `MaintenanceResult` with the logger diagnostic, and set `run_log`, `executive_summary`, `event_log_path`, `summary_path`, `registry_view_path`, and `pareto_view_path` to `None`. |
| `RunLogger.publish_view` fails | The selected `SearchState` is already committed. Record a `ComponentFailure` with `phase=registry_view_publish` or `pareto_view_publish`, preserve committed state and latest successful paths, stop with `component_failure`, and finalize the invocation when logging remains available. |
| `RunLogger` fails while appending a transition batch | Record a `ComponentFailure` with `phase=run_logging`. Abort that pending `SearchCommit`, restore staged component state, and stop with `component_failure`; return the prior committed state and partial log in `RunResult` or `MaintenanceResult`. |
| `RunLogger` fails while appending an invocation event | Record a `ComponentFailure` with `phase=run_logging`. Keep committed search state, stop with `component_failure`, and return the latest successful checkpoint reference and partial log. |
| `RunLogger` fails during finalization | Preserve the search or maintenance `termination_reason` already written in `invocation_stopped`; finalization is a reporting operation after search termination. Add the logger `ComponentFailure` with `phase=run_logging` to `RunResult`, `MaintenanceResult`, or `InitialSolutionsError` diagnostics, retain the committed state and in-memory `RunLog`, and return any artifact paths, summary record, and report diagnostics carried by `LogFinalizationError`, including the latest registry and Pareto-view paths. A logger exception without that structured error supplies no artifact paths or summary. No later event is required when the logger cannot append. |
| Checkpoint preparation or `CheckpointStore.save` fails | Record a `ComponentFailure` with `phase=checkpoint_save`. Stop with `component_failure`; retain every committed transition in the in-memory `SearchState` and return the latest successfully saved `CheckpointReference`, if any. Checkpoint preparation and capture behavior follow architecture §8.1; the failure leaves candidate commits that triggered the save intact. A failed save leaves the previous reference usable. `MaintenanceResult` also carries the latest in-memory `Checkpoint`. |
| Pareto snapshot codec capture or file publication fails after a committed archive change | Record a `ComponentFailure` with `phase=pareto_snapshot_save`. Preserve the committed in-memory state, stop with `component_failure`, and return the latest successfully published `pareto_snapshot_path`, if any. The failed publication leaves the prior snapshot file usable. |
| A `RandomSource`, stateful `Operator`, `FeedbackPolicy`, or stateful `SearchExtension` fails while capturing its construction baseline or restoring baseline/checkpoint state | Raise `ComponentSetupError` with a `ComponentFailure` identifying the component, phase, kind, and reason. Baseline capture failure occurs during engine construction; restore failure occurs before the applicable `run()` or `maintain()` invocation ID, logger setup, or result exists. A fresh search restores the `RandomSource` baseline; resume restores the stream state in the checkpoint. Setup stops at the first failure. Any component already called may hold partially restored state; a later invocation repeats the complete restore from its selected baseline or checkpoint before doing work. |
| `CheckpointStore.load` fails or returns a value outside its contract, or `SolutionCodec.restore` fails or returns a value outside its contract | Raise `CheckpointRestoreError(checkpoint_reference, failure)` before the invocation starts. The `ComponentFailure` identifies the store or codec and uses `phase=checkpoint_restore`. Compatibility mismatches continue to raise `ResumeCompatibilityError`; component-state restoration continues to use `ComponentSetupError`. |
| The promoted-composition catalog file cannot be read | Raise `ComponentSetupError` with `source="multiobjective-vns:promoted-composition-catalog"`, `kind=infrastructure_failure`, and `phase=promotion_catalog_load`, before invocation logging. |
| The saved promoted-composition catalog is malformed, uses an unsupported format version, or has a compatibility mismatch | Raise `ComponentSetupError` with `source="multiobjective-vns:promoted-composition-catalog"`, `kind=contract_violation`, and `phase=promotion_catalog_load`, before invocation logging. |
| The promoted-composition catalog cannot be atomically updated after a promotion commit | Keep the committed search state, record `ComponentFailure(source="multiobjective-vns:promoted-composition-catalog", kind=infrastructure_failure, phase=promotion_catalog_save)`, and stop with `component_failure`. |

### Checkpoints and continuation

A checkpoint is a snapshot of one completed `SearchCommit`. Its opaque
`SearchState` retains the complete solution registry and evaluation records,
archive and active-pool membership, continuation counters such as per-base
attempt counts, the base-selection continuation context, and the next-size
cursor keyed by `(base_solution_id, ChoiceId)` for every current Pareto base
and active excursion base. A candidate-attempt commit advances or resets the
selected base-choice cursor under the rule in
Choice sizes and feedback. The state also retains the latest committed
`StatePayload` for each stateful Operator keyed by `operator_id`, the latest
committed `StatePayload` for each stateful SearchExtension keyed by
`component_id`, and the latest committed `RandomSource` stream payload.
Invocation-scoped disabled-choice flags reset at the start of each `run()`;
after resume, an otherwise available choice is reconsidered at its saved
cursor, while an exhausted cursor remains exhausted for the logical search.
It also retains feedback scores keyed by `ChoiceId`, shared across bases and
sizes, and attempt, admission, and modification statistics keyed by choice
and requested size. The saved choice set covers ordinary and pre-registered
composite Operators, promoted Operators, and the dynamic composition
portfolio. It also retains promoted Operator entries with their inherited
group IDs and the first admitted `SolutionId` when the promotion occurred in
that logical search. File-loaded entries have no local admitting solution ID.
Each choice starts with score zero
when it is created. The checkpoint also retains the active `InfeasibleExcursion`
(anchor, current solution, and step count), the
compatibility manifest, and the internal commit sequence. The event batch for
that commit is already present in the
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
eligible only after the full initial set passes its application-valid,
application-feasible, within-bounds precondition; a due
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
invocation. The saved base-selection context spans invocations. A previous
base remains eligible whenever it belongs to the current eligible set; the
default selector chooses among current Pareto members with the fewest prior
attempts, and a custom selector may use the supplied prior-base context.

`RunResult.candidate_outcomes` returns the `CandidateOutcome` records from
committed submissions in processing order, including identity failures with
no `SolutionId` and an initial-solution prefix when initialization stops on a
component failure. `InitialSolutionsError` carries the committed initial
outcomes only when a fully processed initial set has no application-valid,
application-feasible, within-bounds solution. `RunLog` records the compact
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
inherited group memberships, and the saved feedback-policy scores and
statistics; eligible pairs absent from the saved portfolio start with score
zero.
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

Viewer-export publication scope and timing are algorithm decisions. The
algorithm selects `registry`, `pareto`, or both and publishes only committed
`SearchState` snapshots. The GUI displays the selected export's commit
sequence and uses the registry scope whenever it is available for the selected
invocation. A Pareto-only publication therefore remains useful while a
registry export is pending or when the algorithm publishes only the Pareto
scope.

The structured event file identifies solutions through stable IDs and records
their outcome details. For the human-readable summary, the optional
`SolutionFormatter` can provide a description for each final Pareto solution;
the Pareto-view export uses it for each visited solution. The report also
shows each final Pareto solution's `SolutionId` and objective values. When
`solution_formatter` is `None`, the summary lists each Pareto solution by its
identifier and objective values. If formatting a solution raises or returns
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
`ExecutiveSummary`. It lists the complete dynamically promoted-Operator
catalog in the final search state, including entries restored from earlier
invocations. Pre-registered composites appear in `RunProvenance.operators`
and their standalone `ChoiceStatistics`, rather than in this learned-promotion
list. Each
`PromotedCompositionReport` contains `operator_id: OperatorId`,
`source_composition_id: CompositionId`,
`component_operator_ids: tuple[OperatorId, OperatorId]`,
`first_admitted_solution_id: SolutionId | None`,
`max_neighborhood_size: positive integer`, `feasibility_restoration: bool`,
`inherited_group_ids: frozenset[OperatorGroupId]`, and
`promoted_in_current_invocation: bool`. Entries follow promotion order across
the logical search. The solution ID is `None` for an entry loaded from the
persistent catalog, whose original solution ID belongs to another logical
search. The Boolean is true when the invocation contains the entry's
`composition_promoted` event.

Current-invocation `ChoiceStatistics` include the standalone IDs of
pre-registered composites, the source composition IDs, and promoted Operator
choice IDs, so users can distinguish combination
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
`choice_selected` event remains in the invocation log. An archive admission
counts one proposal entering the known Pareto archive; one attempt can admit
several solutions. `admitting_attempt_count` counts attempts with at least
one archive admission. The admission rate is `admitting_attempt_count`
divided by attempts and is `None` when there are no committed attempts.
`archive_admission_count` separately reports the number of individual
admissions and can exceed the attempt count.
The number of no-admission attempts is `attempt_count -
admitting_attempt_count`; together with the admission rate, it shows each
choice's success and no-admission frequency by size. The built-in policy
updates a choice's score once for each committed attempt, independently of
the number of individual archive admissions.
Modification-count frequencies record each returned candidate's `m`, one
`m=0` for a valid empty result, and one `m=None` when an attempt produces no
valid result. A
choice with committed attempts and no admissions is distinguishable from one
with no committed attempts; `choice_selected` events show any selected
attempts whose transitions rolled back. The eligible choice-size set is the
union of newly eligible descriptors and their supported sizes reported by
`choice_set_available` events, as defined in architecture §6.6. Each
`ChoiceStatistics` record carries the corresponding
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
