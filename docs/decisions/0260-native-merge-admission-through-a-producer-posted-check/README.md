# 0260 — Native merge admission through a producer-posted `merge-admission` check

- **Date:** 2026-11-12
- **Status:** Accepted (ARB-G1-2026-11-06 C2, majority 2-1, not validator-certified). This change ships the producer and the assertion's self-exclusion. It creates, edits, and enables no ruleset, and changes no organization property, variable, or conformance policy. Precondition (b), the owner's explicit acceptance of the stale-SUCCESS window, gates enabling the ruleset (item 7, second step); it does not gate shipping this producer, which posts a context no ruleset requires.
- **Issues:** [verJSON/.github#1990](https://github.com/verJSON/.github/issues/1990) (decision record), [verJSON/.github#2031](https://github.com/verJSON/.github/issues/2031) (the exact-head review receipt on the native path)
- **Extends:** [ADR 0257](../0257-merge-admission-requires-the-exact-head-review-receipt/README.md), [ADR 0229](../0229-required-contexts-need-success-and-finished-runs/README.md), [ADR 0226](../0226-superseded-cancelled-check-runs-do-not-block-admission/README.md), [ADR 0202](../0202-bind-autonomous-merge-to-independent-review-receipt/README.md)
- **Related:** [ADR 0091](../0091-ruleset-requires-authorization-arm/README.md), [ADR 0190](../0190-pin-the-producer-app-of-every-required-status-check/README.md), [#1964](https://github.com/verJSON/.github/issues/1964)
- **Category:** merge-gate admission, required checks (sensitive class)

## Context

ADR 0257 added Gate R to `scripts/assert-mergeable-head.sh`: on a promotion-enabled
repository, a head needs a valid ADR 0202 independent-review receipt bound to exactly that
head. It holds every merge that runs the assertion, but not the merge button or native
auto-merge, which are held only by rulesets, and GitHub reads a SKIPPED required check as
passing. ADR 0257 Decision 3 left the native path to #1990 and required its ADR to record
whether the new check may veto that path. #2031 asks that a promotion-enabled repository
cannot merge a head without that receipt on any path the canonical contract owns.

On 2026-11-06 the architecture review board decided #1990 (ARB-G1-2026-11-06, item C2):
ADOPTED_WITH_CONSTRAINTS by majority, 2-1, not validator-certified. Claude and Gemini
accepted; Grok 4.7 (Cursor) voted REVISE, and an advisory Codex vote also said REVISE.
Nobody voted REJECT. The two dissents are binding preconditions.

## Decision

The adopted action items, distilled:

1. **Admission.** On enrolled repositories a native merge-button merge or native auto-merge
   is admitted only when the canonical `assert-mergeable-head.sh` exits 0 printing `true`
   for the exact head, through all of Gates A, W, B, C, and R, and not under GitHub's
   SKIPPED-passes semantics. This answers ADR 0257 Decision 3: on an enrolled repository
   the check does veto the native path for every principal that cannot bypass the
   ruleset.
2. **Ruleset.** A new organization ruleset, scoped to `verjson-core-checks=enforced`
   repositories that hold the AI review App key, requires the context `merge-admission`
   bound to the AI review App's integration id. The conformance policy and the
   org-ruleset-conformance audit are amended to allow exactly this per-context binding
   (`merge-admission` to the App id) and to fail when that context is bound to 15368 or
   unbound; every other required context stays bound to 15368 (ADR 0190).
3. **Producer and self-exclusion.** The producer runs the canonical script from the
   organization repository at a pinned SHA. It either runs entirely outside the target
   head's workflow-run and check inventories, or the script excludes exactly its own run
   from ADR 0229 rule 2 and its own job check runs from Gate C, each authenticated by run
   id and the pinned producer path, and nothing else. Gates A and C exclude
   `merge-admission` only when its producer is the bound App; a same-named check from any
   other producer stays a Gate C failure.
4. **States.** Every evaluation first creates a new `merge-admission` check run in progress
   on the head, superseding any earlier result, and completes it SUCCESS only when the
   assertion answers `true`. On a refusal or an error it leaves the run in progress
   (pending, which blocks natively) and never posts NEUTRAL, SKIPPED, or FAILURE. That
   the newest same-named check run governs the native required status is platform
   behavior that the negative canaries of item 7 must prove.
5. **Triggers.** Re-evaluate on pull-request events (opened, synchronize, reopened,
   ready_for_review, edited including a base change, labeled, unlabeled), on completion of
   each workflow that reports a required context (excluding the producer's own path), on
   reviews submitted, edited, or dismissed, after any ruleset edit, and by a periodic
   sweep of open enrolled pull requests at most 15 minutes apart. The superseding
   in-progress run is the first action of every evaluation; the canaries measure the
   residual window between a same-head change and that run, and this ADR states it.
6. **Stated limits.** Ruleset bypass actors are not held: the Renovate App (renovate-config
   `default.json`) and administrators with bypass. Native merges gain no human-review
   requirement (GH-03, out of scope). The App key sits in each consumer's `ai-review-app`
   environment, so the trust boundary is the repository's main branch plus that
   environment's protection, the same as the authorization arm.
7. **Rollout.** This ADR first. Ship the producer posting the context while it is not
   required, and observe at least 20 merges across at least 3 enrolled repositories with
   a pre-merge SUCCESS on the exact head, recording any false refusal. Then enable the
   ruleset on one canary repository only, with an audit receipt, and run matched positive
   and negative canaries as a non-bypass actor, through both the merge button and an
   already-enabled auto-merge: a draft-skipped required context, an unfinished run on the
   head, a same-head base change and a review dismissal after a SUCCESS (measuring the time
   until the superseding run exists), assert true then false on the same head, and a
   forged same-named check from GitHub Actions. Each negative case must block. Then the
   enrolled set.
8. **Rollback.** Disable the new ruleset: one edit, no data effect. The policy amendment
   and the producer may remain.

Binding preconditions from the dissents:

- **(a) Cursor.** When the producer refuses or errors, its own workflow and job checks
  conclude success. The only blocking signal is the `merge-admission` check left in
  progress. The self-exclusion covers only the current in-progress run and never hides an
  earlier failed producer check.
- **(b) Codex.** This decision is admission with a remaining **stale-SUCCESS window**: a
  review dismissed on the same head, a newly started advisory workflow, or a non-Actions
  check that fails later can follow a SUCCESS until the superseding run exists. The owner
  must accept that window explicitly. Canaries try to merge, through the merge button and
  through already-enabled auto-merge, throughout the interval before the superseding run
  exists, and report any merge that gets through as a limitation. If full merge-time
  parity is required, rollout stays blocked.
- **Serialization and completion triggers.** Evaluations are serialized per head (a
  producer concurrency group without cancel-in-progress), or an out-of-order-completion
  negative canary is added. A completion trigger covers checks from non-Actions Apps, or
  that window is documented. The canaries confirm that a check left in progress for a long
  time still blocks natively.

The ARB record does not itself authorize a ruleset edit, and neither does this ADR.

### Outcome for #2031

Gate R (ADR 0257) is part of the assertion that the producer runs, so once the item 2
ruleset is enabled on a repository, a native merge of a head without a valid exact-head
ADR 0202 receipt on a promotion-enabled repository is held: the producer leaves
`merge-admission` in progress on Gate R's exit 6, exactly as on any other refusal. That
is the outcome #2031 requested, for every non-bypass principal. It takes effect only with
the rollout steps below; this change performs none of them.

## Implementation in this change

**Placement (item 3).** The producer is a new reusable workflow,
`.github/workflows/merge-admission.yml`, called from the consumer's default branch, and
not a job inside the org-required arm (`gate-rearm.yml`). The arm is a required workflow,
which runs only on `pull_request_target`, so it cannot take `workflow_run`, review, or
`schedule` triggers, and its runs belong to the head's inventory, which would
need the run-id self-exclusion of item 3's second branch. The producer takes the first
branch instead: callers start it only from `workflow_run`, `schedule`, and
`workflow_dispatch`, whose runs and job check runs belong to the default-branch commit,
never to a pull-request head; the producer's first job evaluates nothing when any other
event starts it. Pull-request and review events reach it through a keyless
trigger workflow (`merge-admission-trigger.yml`) whose run completes before the producer
starts, so ADR 0229 rule 2 never sees an unfinished trigger run as the producer's own. The
assertion therefore needs no run-id exclusion and gains none, and it never hides any
Actions check; precondition (a)'s "never hide an earlier failed producer check" holds by
construction, because no producer job check runs on the head. A job inside the arm was
rejected for long-term health as well as fit: it would put a second secret-bearing control
plane in the required workflow, and couple every producer change to the arm's ruleset
rotation (ADR 0091).

**Self-exclusion.** `assert-mergeable-head.sh` binds `MERGE_ADMISSION_APP_ID=4528902`
(the AI review App, `ai-review-authorization`, read from the organization variable and
`apps/ai-review-authorization` on 2026-11-12). Gates A and C set aside every
`merge-admission` check run produced by that App that is still in progress, concluded
success, or concluded stale, including earlier in-progress ones, which every refusal
leaves behind and which would otherwise block every later evaluation of the same head.
Stale is the conclusion GitHub alone sets on a check run left incomplete for 14 days, so
on an older head it is the same refusal residue; reading it as a failure would refuse
that head forever, even after a later success. The producer never concludes its check
any other way, so any other completed conclusion from that App is not its output and
stays a Gate C failure. Gate A skips the required context only where the ruleset binds
it to that App. A same-named check run from any other App and a same-named commit status
stay in the inventory. Gate B keeps reading the raw inventory. The assertion also takes
an optional third argument, the expected head, and exits 1 when the pull request's head
is another SHA, so an evaluation never answers for a head other than the one its check
run belongs to.

**Evaluation is per head, not per pull request.** A check run attaches to a commit, so a
`merge-admission` success on a head stands for every open pull request at that head,
whatever its base or branch. Evaluating pull requests one at a time would let the last
evaluation of a shared head govern all of them: a pull request without a receipt could
merge on another's success. `scripts/ci-gate/merge-admission.sh` therefore takes one
head and every open pull request at it. It creates the new check run in progress with
the App token, continues only when GitHub reports the bound App as its producer and a
numeric id, runs the assertion beside it with the job's read-only token (the App token
is removed from the assertion's environment) for each of those pull requests, re-lists
the open pull requests at the head immediately before completing, and completes the
check run as success only when every assertion exited 0 with the exact output `true` and
that set is unchanged. Every other outcome, including one refusal among several pull
requests or a set that changed or could not be read again, leaves it in progress and
exits 0. The evaluate job checks out `Verjson/.github` at `job.workflow_sha`, the
executing producer's own SHA, which the consumer pins in its `uses:` reference. The App
token is minted with `permission-checks: write` only and the mint step tolerates
failure. `scripts/ci-gate/merge-admission-plan.sh` groups the open pull requests by
head: it plans each head that has at least one pull request that is not a draft (a draft
cannot merge, and marking it ready is a trigger), lists every open pull request at that
head including drafts (the success would stand for a draft the moment it is marked
ready), drops a head at which any pull request is unevaluable, and with an event head
plans only that head.

**Serialization.** The evaluate job's concurrency group is per repository and head, with
`cancel-in-progress: false`. GitHub keeps one pending run per group, so a pending
evaluation can be replaced only by a newer one of the same head, which reads later state
and creates a newer check run. The sweep's matrix legs, one per head, share the same groups
as the event-driven evaluations.

**Triggers (item 5).** This repository's caller (`merge-admission-caller.yml`) lists
every workflow that reports a check on a pull-request head here, plus the trigger, under
`workflow_run`. The workflow contract test derives that list from every workflow a
pull-request or review event starts (excluding the producer's own three files and
close-only workflows), adds the dispatched `AI review + auto-merge` gate, which patches
its authorization check on the head, and requires an exact match, so a new pull-request
workflow cannot be left out. Only a completed run that a pull-request or review event
started (`pull_request`, `pull_request_target`, `pull_request_review`) narrows the plan
to its `head_sha`. Every other completed run plans a full sweep, like the schedule and a
manual dispatch: the AI review gate is dispatched with `--ref` set to the default branch
(`gate-rearm.yml`), so its `head_sha` is a default-branch commit no pull request is at,
and narrowing to it would evaluate nothing. Each completion of the gate therefore costs
a sweep.

**The sweep is best-effort.** It is scheduled every 15 minutes, and that is a target, not
a guarantee: GitHub delays scheduled runs under load and can drop them, and it disables a
scheduled workflow on a public repository after 60 days without repository activity. Item
5's "at most 15 minutes apart", and every bound below that names the next sweep, holds
only as well as the schedule does; the canaries measure the actual gaps between sweeps and
report them.

**The completion trigger for checks from Apps other than GitHub Actions is deferred, and
that window is documented here** (the serialization bullet's alternative): `check_suite:
completed` is the event that reports them, but the ADR 0235 runner-isolation checker
classifies `check_suite` as undetermined (it can name a fork head), and classifying it
is an ADR 0235 change of its own. Until then, a non-Actions check that completes on a
head is seen by the next sweep (targeted 15 minutes later) or by any earlier trigger on
that head. When `check_suite` is classified, the caller adds it and the producer must
skip the AI review App's own suite, which every admission completes. GitHub delivers no
workflow event for a ruleset edit, so that trigger is the sweep (targeted 15 minutes) or
a manual `workflow_dispatch` of the caller as part of the ruleset-edit procedure.
`app-key-environment.yml` now reads a scheduled run's default branch from its ref,
because a schedule event carries no repository record and always runs on the default
branch.

**Precondition (a) scope.** Refusals, evaluation errors, an unreadable pull-request
listing, and an App token that cannot be minted all end with the producer's jobs
succeeding. Two configuration faults still fail visibly, because they precede any
evaluation and are the controls that protect the key: the main-only environment
admission (`app-key-environment.yml`) and the generated App client-id guard. A runner or
checkout failure before the evaluation starts fails visibly too, and so does an evaluate
job that exceeds its 15-minute timeout. The evaluate job's concurrency group also
cancels pending legs: GitHub keeps one pending job per group and cancels an older
pending one when a newer one queues, so a burst of triggers on one head concludes the
displaced legs as cancelled. Precondition (a) is therefore narrowed, not met as written:
every one of these runs concludes red or cancelled on the default-branch commit, never
on a pull-request head, so none enters a head's inventory or blocks a merge, but each
adds noise to the default branch's CI signal. An evaluation they stop leaves the head's
earlier `merge-admission` state as it was, which is the stale-SUCCESS window of
precondition (b). One canonical environment admission is kept rather than a third
behaviorally matched copy.

## Not performed by this change (rollout gates)

- The owner's explicit acceptance of the stale-SUCCESS window (precondition (b)), which
  gates enabling the ruleset (item 7, second step), not shipping this unrequired producer.
- ARB or owner acknowledgment of the narrowed precondition (a): configuration faults,
  timeouts, and concurrency cancellations of pending evaluate legs conclude red or
  cancelled on the default-branch commit.
- An alert, before enrollment, on every producer run that does not conclude success and on
  every evaluation that annotates `was not evaluated` (which precondition (a) lets
  succeed), so the unbounded outage window below is seen rather than silent.
- A REST rate-limit budget, before enrollment. Each assertion makes roughly 10-20 API
  calls on the job's `GITHUB_TOKEN`, whose budget (about 1,000 requests per hour per
  repository on the Team plan) is shared with the AI gate and `actions-ci`. The sweep
  alone costs about 4 x open pull requests x calls per hour, plus bursts from pushes and
  completions. The rollout measures it on the canary and, where it does not fit, adopts
  one of: reading with a separately minted read-only App token, whose budget is per
  installation; or skipping, in the sweep, a head whose newest bound-App
  `merge-admission` check postdates the head's last event.
- The item 2 ruleset, its conformance-policy amendment, and the org-ruleset-conformance
  audit change. No ruleset is created, edited, or enabled, and no property or variable
  changes.
- Generated consumer callers (the producer caller and the trigger) for enrolled
  repositories, the 20-merge observation across 3 repositories, the canary ruleset with
  its audit receipt, and every positive and negative canary of item 7, including the
  measurement of the trigger-to-superseding-run window and the long-in-progress block.

## Consequences

- This repository posts `merge-admission` on its pull-request heads as soon as this lands,
  while no ruleset requires it. A refused head keeps an in-progress `merge-admission` check,
  which the assertion sets aside and nothing else requires.
- Any caller of the assertion may pass the expected head; existing two-argument callers are
  unchanged.
- **Residual windows**, beyond precondition (b)'s: a change on the same head waits for
  its trigger run to complete and the producer to start before the superseding check
  exists; a GitHub Actions check is seen at completion only for workflows the caller
  lists, and a check from any other App only by the next sweep;
  a ruleset edit is seen by the next sweep or a manual dispatch; a permission downgrade
  emits no event and is seen by the next sweep.
- A pull request whose head is the default branch's current commit shares that commit
  with the producer's own runs, which read as unfinished; it is refused and left in
  progress, never admitted.
- Both producer jobs run on the ephemeral lane (`CI_LANE_UNTRUSTED`, GitHub-hosted by
  default) because the ADR 0235 checker finds that they read pull-request data. The
  sweep therefore costs, every 15 minutes, one planning job, the canonical environment
  admission's two jobs (one of them fixed on a GitHub-hosted runner), and one evaluation
  job and environment deployment record per distinct head of the open non-draft pull
  requests. On a private
  repository without an ephemeral self-hosted lane those are billed hosted minutes; the
  rollout must budget them before enrolling private repositories.
- **Unbounded stale-SUCCESS window during an outage.** A missing App client id, an
  environment admission failure, an App token that cannot be minted, or a check run that
  cannot be created stops the evaluation before the superseding in-progress check exists
  (`scripts/ci-gate/merge-admission.sh`, the token and creation guards; the evaluate job's
  client-id and admission steps). An earlier SUCCESS on the head then stands for as long
  as the outage lasts, with no upper bound, even if the head's state changes. The alert
  listed under the rollout gates is what bounds it in practice.
- Rollback before enablement is deleting the caller and trigger; after enablement it is
  item 8.

## Verification

- `scripts/assert-mergeable-head.merge-admission.test.sh` (merge-gate): Gate C sets
  aside the bound App's in-progress and stale checks but keeps its completed failure;
  Gate A sets aside the context the ruleset binds to that App; a same-named GitHub
  Actions check stays a Gate C failure; a context bound to GitHub Actions stays
  required; a mismatched expected head exits 1, a matching one is evaluated, and a
  malformed one is a usage error.
- `scripts/ci-gate/merge-admission.test.sh` (merge-gate): the in-progress check is created
  first with the App token and before the assertion; exit 0 with `true` completes that
  check run as success; exit 0 with any other output, exit 6, exit 5, and exit 1 leave it
  in progress while the producer exits 0; a refusal for any one pull request at the head,
  or a pull-request set at the head that changed before completion, leaves it in progress;
  the assertion runs without the App token; a check from another App, a non-numeric id, a
  failed creation, or no App token stops before the assertion; a failed completion is
  named; malformed arguments are refused before any call; the producer and the
  assertion bind the same App id.
- `scripts/ci-gate/merge-admission-plan.test.sh` (merge-gate) and
  `scripts/ci-gate/merge-admission-workflow.test.py` (merge-gate) pin the plan and the
  workflow contract: the caller's completion triggers equal the derived set of
  pull-request workflows, one plan entry per head with every open pull request at it
  (drafts included, a head with an unevaluable pull request dropped),
  default-branch-only triggers, a sweep scheduled at most 15 minutes apart, the
  trigger's events and its lack of credentials, per-head serialization without
  cancellation, the pinned canonical checkout, the App token's single permission, the
  read-only assertion token, the environment admission and its scheduled-run default
  branch, and the guard that a pull-request event evaluates nothing.

Each guard that held when written was proven by a mutant that fails it for its asserted
reason, recorded in the pull request.
