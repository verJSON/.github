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
2. **Ruleset.** A new organization ruleset, `merge-admission-required`, requires the
   context `merge-admission` bound to the AI review App's integration id. *Scope amended
   2026-11-12 (ARB-2031-2026-11-12 C8, C3-CANARY; see the amendment below):* its first
   scope is this repository alone, by `repository_id` 1269388380. Wider enrollment is by an
   explicit enrollment property (for example `verjson-merge-admission=enforced`), set on a
   repository only after its generated caller and trigger have landed and a bound-App
   `merge-admission` SUCCESS has been observed there. The ruleset excludes
   `verjson-actions-state=paused` repositories (ADR 0252), its ref set matches ruleset
   20722935's or the record states why not, and the audit fails a ruleset whose scope
   includes a repository without the generated caller. This replaces the original scope,
   `verjson-core-checks=enforced` repositories that hold the AI review App key, which would
   enroll a repository before its caller exists. The conformance policy and the
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
   `default.json`) and administrators with bypass. *Amended 2026-11-12
   (ARB-2031-2026-11-12 C6):* the bypass list mirrors ruleset 20722935 (OrganizationAdmin,
   4693283, 4583107, and Renovate 2740, all `always`) and never includes the AI review App
   4528902. **Renovate pull requests therefore land on enrolled repositories without the
   exact-head ADR 0202 review receipt.** That is a named carve-out from #2031's outcome, not
   an oversight, and it is re-decided at the #1964 switch. Native merges gain no human-review
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
  parity is required, rollout stays blocked. *Accepted by the owner on 2026-11-12, scoped
  and time-boxed; see the C1 amendment below.*
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
is the outcome #2031 requested, for every non-bypass principal. Renovate is a bypass actor,
so its pull requests are outside it (Decision 6, amended 2026-11-12). It takes effect only with
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

**Drafts at a shared head cost liveness, by design.** Including drafts fails closed: an
unreviewed draft that shares a head with a ready pull request, such as a backport or a
duplicate opened from the same commit, is refused by its own assertion, so it holds the
shared `merge-admission` check in progress and blocks the ready pull request until the
draft is closed or reviewed. Excluding drafts would instead leave a SUCCESS on the head
that stands for the draft the moment it is marked ready, admitted without any evaluation
for the window until the `ready_for_review` trigger's superseding check exists, and
indefinitely during an outage of the producer. The blocked ready pull request is the
visible, operator-recoverable failure; the unevaluated draft merge is not.

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
displaced legs as cancelled. A sweep that plans more than 256 heads exceeds GitHub's
matrix limit, so the evaluate job fails to start and the run concludes red; no cap is
applied, because evaluating only some heads would leave the rest on their earlier result
silently, while the red run is visible. Precondition (a) is therefore narrowed, not met
as written: every one of these runs concludes red or cancelled on the default-branch
commit, never on a pull-request head, so none enters a head's inventory or blocks a
merge, but each adds noise to the default branch's CI signal. An evaluation they stop
leaves the head's earlier `merge-admission` state as it was, which is the stale-SUCCESS
window of precondition (b). One canonical environment admission is kept rather than a
third behaviorally matched copy.

## Not performed by this change (rollout gates)

- The owner's explicit acceptance of the stale-SUCCESS window (precondition (b)), which
  gates enabling the ruleset (item 7, second step), not shipping this unrequired producer.
- ARB or owner acknowledgment of the narrowed precondition (a): configuration faults,
  timeouts, concurrency cancellations of pending evaluate legs, and a sweep over more than
  256 heads conclude red or cancelled on the default-branch commit.
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

## 2026-11-12 amendment: the producer never evaluated live (key delivery)

After #2055 merged at `6ba5b189`, no `Merge admission` run evaluated a head. By
2026-11-12T13:31Z, 107 runs had failed. In each, the plan found work and
`admit / app-key-policy / validate-ai-review-key` stopped with
`AI_REVIEW_APP_PRIVATE_KEY is empty or unavailable` (for example
[run 43226371916](https://github.com/Verjson/.github/actions/runs/43226371916)), so
`evaluate` was skipped. The other 89 runs succeeded only because their plan was empty.
`evaluate` and the key validator were skipped in every one of them. No
`merge-admission` check was ever posted by this producer.

The cause was key transport, not policy. This repository holds the key only as an
`ai-review-app` environment secret. In a reusable workflow, an environment binding
alone resolves no secret: it needs caller inheritance
([ADR 0171](../0171-inherited-reusable-environment-context/README.md), live control
run 34433194313). The key validator also needs the narrow named grant on its policy edge
(ADR 0171, 2026-09-27 amendment). `merge-admission-caller.yml` called the producer
without `secrets: inherit`, and the producer's `app-key-policy` edge carried no grant.
Both the validator and `evaluate`'s token mint therefore saw an empty key. The
`ai-review-merge-dispatch.yml` → `ai-review-merge.yml` chain uses both transports and
resolves the same environment-only key; its run 43223952797 passed
`validate-ai-review-key` on the same day.

The fix adopts that transport. The caller's `admit` job inherits the caller's secrets.
That widens what the producer can read to every secret the caller can reach, including
the model provider keys, not only the App key. ADR 0171 accepts the widening on the
condition that each secret is referenced only where it is required: the producer
references `AI_REVIEW_APP_PRIVATE_KEY` alone, at the grant and in `evaluate`.
The producer's `app-key-policy` edge carries only
`AI_REVIEW_APP_PRIVATE_KEY: ${{ secrets.AI_REVIEW_APP_PRIVATE_KEY }}`. `evaluate` keeps
its `ai-review-app` environment binding, and the main-only policy and its
`needs`/`if` dependency are unchanged. Permissions and triggers are unchanged. The
producer does not declare the key under `on.workflow_call.secrets`: ADR 0171 records the
optional declaration as a transport that failed the live control, and the key-consumer
contract forbids declaring a role key.

`scripts/ci-gate/app-key-environment.test.py` (platform) now enrolls `merge-admission`
as an App-key consumer, with `evaluate` as its key-bearing job. It requires the named
grant on every `app-key-policy` edge whose role is `ai-review`, instead of on
`ai-review-merge` alone. It also derives every relative reusable edge into an App-key
consumer from the workflows and requires `secrets: inherit` on each, so a new caller
cannot omit it. Removing either transport line fails its test for that reason. The
pull request records the mutants. A live receipt is the first `Merge admission` run
after merge that reaches `evaluate`.

### Bounded producer fan-out

Delivering the key turns every run that plans a head into an `evaluate` matrix
(`max-parallel: 4`, up to 15 minutes per leg). The caller had no concurrency, so the run
count is the bill. Measured on 2026-11-12 from 08:17 to 14:17 UTC: 246 `Merge admission`
runs, 121 of them in the 12:00 hour (118 `workflow_run`, 3 `schedule`). The completions of
one head's workflows arrive 6-8 per head, and each completion that a pull-request or review
event did not start plans a full sweep.

**The bound.** The caller declares run-level concurrency with `cancel-in-progress: false`.
Its group is `merge-admission-caller-` followed by the plan job's own `EVENT_HEAD` choice,
with `sweep` in place of the empty head: a completed run that a `pull_request`,
`pull_request_target`, or `pull_request_review` event started keys on its `head_sha`, and
every other trigger (any other completed run, the schedule, a manual dispatch) keys on
`sweep`. The empty-head edge maps to a sweep in both, so the key and the planner cannot
disagree. The `-caller-` prefix keeps these groups apart from the evaluate job's
`merge-admission-<repository>-<head>` groups, so a run never waits on a group it holds.

**Why a cancelled run loses no evaluation.** GitHub keeps at most one running and one
pending run per concurrency group. When a run queues into a group that already holds a
pending run, the older pending run is cancelled and the newer one waits. With
`cancel-in-progress: false`, the running run is never cancelled. In a head group, the
replacing run plans the same head, and its planner reads the open pull requests at that
head when it starts, which is later than the cancelled run would have read. In the sweep
group, the replacing run is a sweep, which plans every open head. The groups are disjoint,
so a narrowed run is never replaced by a sweep, and it does not need to be. A running run
always finishes, so the pending slot always runs next.

**The trigger needs no bound of its own.** `merge-admission-trigger.yml` reaches the
producer only through this caller's `workflow_run`, so its completions are bounded here.
Its own runs hold no credential, read nothing, and finish in seconds. A review event runs
the head's copy of that file, so a concurrency declared there is not under the default
branch's control in any case.

**Who can influence the key.** A pull-request author chooses the head SHA, so an author can
open a pull request at another pull request's head and share its group. That cannot starve
the other pull request: every run in a head group plans every open pull request at that
head, so whichever run executes evaluates both. Events on one's own head cost at most one
running and one pending run for that head. The sweep group is reached by default-branch
pushes, dispatches, the schedule, and completions of the AI review gate, which
`gate-rearm.yml` dispatches on pull-request events. A burst there produces sweeps one
after another, never overlapping, and each covers every head.

**Expected rate.** Per head, at most one producer run executes and at most one waits. A
burst of completions that arrives within one producer run's duration therefore executes
at most 2 runs for that head; a longer burst executes at most one run per producer-run
duration, plus one. Sweeps never overlap. The schedule targets one every 15 minutes, and
gate dispatches and default-branch pushes can raise that to back-to-back sweeps, never
higher. Displaced pending runs still appear in the run list as `cancelled` on the
default-branch commit, so the count of created runs does not fall; executed runs and
`evaluate` jobs do. Those cancellations add the same default-branch noise as the
evaluate job's cancelled legs, which narrowed precondition (a) above already accepts.
The bound is per head and per sweep, not global: concurrent producer runs grow with
the number of open pull-request heads, so a contributor with K open heads holds K head
groups. The post-merge measurement on #2031 is therefore read per head, not as one
hourly total.

**Budget ownership.** `docs/ci-workflow-tiers.md` excludes `pull_request_target`
authorization and merge-gate workflows from the tier policy, so no document states a
runs-per-hour budget for this producer. The rate above is this ADR's own budget.

**Post-merge measurement.** For the first hour after this change merges, count
`Merge admission` runs by conclusion (success, failure, cancelled) and `evaluate` jobs, and
record both on #2031 beside the baseline above. An executed-run rate above the stated
bound reopens this design.

**Item 5 deviation, recorded.** Item 5 names completion of each workflow that reports a
*required* context, while the caller lists every workflow that reports any check on a
pull-request head. The caller keeps that list, for two reasons. First, no configuration in
this repository names this repository's required contexts. They come from live
organization rulesets selected by repository properties, which Gate A reads at evaluation
time. `config/org-ruleset-conformance-policy.json` binds required workflows for other
repositories, and `config/required-check-bindings/` holds snapshots of property-scoped
rulesets, not this repository's set. Deriving the list would need new configuration.
Second, the assertion's verdict depends on more than the required contexts. Gate C refuses
while any other check on the head is pending or non-passing. Narrowing the list would leave
a head refused, after an unrequired check completes, until the next sweep. The concurrency
bound limits the cost of the wider list instead.

`scripts/ci-gate/merge-admission-workflow.test.py` (merge-gate) pins the caller's group
and `cancel-in-progress: false`. It also evaluates the group and the plan job's
`EVENT_HEAD` over the same events, and requires the head for the three pull-request
events and `sweep` for every other completed run, the schedule, and a manual dispatch.
Dropping the concurrency, setting `cancel-in-progress: true`, keying on `github.sha`, or
keying on `head_sha` for every event fails it, and so does narrowing the planner's event
list alone.

## 2026-11-12 amendment: ARB-2031-2026-11-12 pre-enrollment contract

The architecture review board decided the item-2 rollout on 2026-11-12
([decision record](https://github.com/verJSON/.github/issues/2031#issuecomment-6831855094)):
12 of 13 candidates adopted 3-0, C10 escalated to the owner. The owner's acts followed
([owner acts](https://github.com/verJSON/.github/issues/2031#issuecomment-6831955600)). The
owner and agents share one account, so each owner act is recorded as **user-attested**.
This amendment records them and ships the policy and audit part of the C10 bundle. It
creates, edits, and enables no ruleset, and changes no organization property or variable.

**C1, precondition (b) accepted (user-attested).** The owner explicitly accepts the
stale-SUCCESS window, scoped to two same-head classes plus the outage window:

- *Trigger-bounded:* a review dismissed or edited, a base retarget, a later-failing
  GitHub Actions check from a listed workflow, and a pull request opened or synchronized to
  a commit that already carries a `merge-admission` SUCCESS, including a push or force-push
  back to an earlier admitted commit. The window lasts until the superseding in-progress run
  exists. **Ceiling: 5 minutes** from the change to that run.
- *Sweep-bounded:* a permission downgrade, a ruleset edit, and a later-failing check from a
  non-Actions App. The window lasts until the next sweep (15-minute target, best-effort, as
  stated above) plus run latency. **Ceiling: 20 minutes.**
- *Outage window:* unbounded in principle, bounded in practice by the C10 outage alert,
  which does not exist yet (below).

A push to a commit that carries no `merge-admission` check run is outside the window: the
required context is absent and blocks natively. **The ceilings are the owner's decision**
(user-attested, given in a Claude Code session on 2026-11-12): 5 minutes trigger-bounded and
20 minutes sweep-bounded, which leaves about 5 minutes of run latency beyond the 15-minute
sweep target. If a C3 canary measures a window above its class ceiling, or a canary merge
gets through inside a window, the acceptance lapses for wider enrollment (C8) and the owner
re-decides from the measurements; the canary ruleset may stay active meanwhile. The
acceptance lapses at the #1964 switch and must be re-decided then.

**C8, Decision 2's scope.** Amended inline above: the canary first (`repository_id`
1269388380), then an explicit enrollment property set only after a repository's caller,
trigger, and a bound-App SUCCESS exist, with paused repositories excluded. The audit check
that fails a scope including a repository without the generated caller is not implemented
yet (below).

**C6, Decision 6's Renovate carve-out.** Amended inline above. The bypass contract is in
`config/org-ruleset-conformance-policy.json` as the name-keyed (`ruleset_id: null`)
`merge-admission-required` entry, before the ruleset exists, and is to be pinned to the ruleset
id after creation. `scripts/org-ruleset-conformance.py` refuses a policy without that
contract or with the AI review App in it, and fails a live ruleset whose bypass list
differs from it.

**C10, as the owner adopted it (fable's two-phase correction, verbatim semantics).**

- *(i) policy and audit.* The policy's `merge_admission_binding` binds `merge-admission` to
  4528902 beside `required_check_producer_app_id` 15368. The audit fails a `merge-admission`
  context that is unbound or bound to any other App, 15368 included, keeps every other
  context on 15368, and refuses a policy that binds `merge-admission` to 15368.
- *(i)(a) App-id equality.* `scripts/assert-mergeable-head.merge-admission.test.sh` and
  `scripts/ci-gate/merge-admission.test.sh` (merge-gate) each fail when
  `MERGE_ADMISSION_APP_ID` in `scripts/assert-mergeable-head.sh` or
  `scripts/ci-gate/merge-admission.sh` differs from the policy's binding.
- *(i)(b) two-phase live audit.* It lives in `scripts/org-ruleset-conformance.py` rather than
  `scripts/ai-review-required-workflow-audit.py`: the conformance audit already owns the
  policy that holds the binding and the name-keyed contract, and reads every organization
  ruleset, while the arm audit's verdict is adjudicated against recorded findings
  (`config/audit-expected-findings.json`), which would let a receipt pass beside a quiet
  finding. Before the ruleset exists, the audit fails unless the organization variable
  `AI_REVIEW_APP_ID` equals the binding, and reports the ruleset as absent:
  `merge-admission-receipt=pre-creation gates=creation … ruleset_state=absent`. Once it
  exists, even disabled, it also fails unless the ruleset requires `merge-admission` bound to
  that id (unbound or 15368 fails), and prints
  `merge-admission-receipt=full gates=activation … ruleset_id=<id> enforcement=<state>`.
  Two live rulesets with that name print no receipt, and in either phase neither does a
  ruleset under any other name, a case variant included, that requires `merge-admission`:
  the binding check accepts the context in any ruleset, so the receipt could otherwise
  report the named ruleset absent while another enforces it. A receipt prints only on
  exit 0, so any other conformance finding withholds it. **The pre-creation receipt gates
  creation; the full receipt gates activation only.** It checks no enrollment scope: not the
  C3 canary's `repository_id` 1269388380, not the paused-repository exclusion, and not C8's
  caller presence. An enrollment edit therefore also needs the C8 scope check, which is not
  done (below); until it lands, no receipt gates an enrollment edit. The scheduled
  `org-ruleset-conformance.yml` run repeats the check daily with its unchanged no-argument
  command. Verified read-only against the live organization on 2026-11-12: exit 0 with the
  pre-creation receipt.
- *Numbering.* Item (v), the runbook, moves before item (vi), the hosted-minute budget.

**C11, ordering.** Nothing removes the OrganizationAdmin bypass from the item-2 ruleset or
the core rulesets before #1964's C4 criterion is met. The native edge #1945 blocked-by #1964
now exists (read through the issue-dependencies API on 2026-11-12). #1956 is a draft pull
request and carries no edge; its bypass-retiring work is tracked under #1945. Replacing the
`--admin` terminal merge (#1945) is not blocked by #1964 if the same change keeps the owner
account's explicit-bypass path (the assertion, then `--admin`), a canary exercises it, and
the runbook states that a producer outage stalls autonomous merges until the owner merges by
bypass.

**Not yet done.** Each of these gates the canary ruleset (C3) and none is claimed here:

1. The C10 (ii) outage alert: failed runs, cancelled runs with at least one started job,
   `was not evaluated` annotations, no producer run reaching `evaluate` and concluding
   success for 45 minutes while open non-draft heads exist, and a disabled producer
   schedule. A fixture proves it does not fire on displaced pending cancellations. It names
   its delivery channel and owner, and either detects independently of Actions in the
   monitored repository or names the Actions-wide or billing outage as the accepted C1 gap.
2. The C10 (iii) REST budget, measured on this repository, adopting one of the two
   mitigations above if it does not fit.
3. The C10 (iv) consumer caller-and-trigger generator.
4. The C10 (v) runbook: ruleset-edit and arm-rotation caller dispatches, deactivating the
   ruleset, closing a foreign same-head pull request, and C7 recovery.
5. The C10 (vi) hosted-minute budget, before a generated caller lands on any private
   repository (verjson-agents and verjson-ai are private).
6. The C9 non-bypass canary principal.
7. The C8 enrollment-scope audit check above (the C3 canary's `repository_id` 1269388380,
   the paused-repository exclusion, and generated-caller presence), which every enrollment
   edit needs beside the full receipt, and C3-OBS (at least 20 merges across at least 3
   repositories).
