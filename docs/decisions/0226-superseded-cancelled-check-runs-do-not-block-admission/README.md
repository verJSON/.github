# 0226 — A cancelled check run superseded by a later successful run of its own workflow does not block admission

- **Date:** 2026-10-13
- **Status:** Accepted
- **Issue:** [verJSON/.github#1754](https://github.com/verJSON/.github/issues/1754)
- **Extends:** [ADR 0184](../0184-merge-gates-assert-execution-not-absence-of-red/README.md)
- **Related:** [ADR 0223](../0223-draft-pull-requests-defer-heavy-ci/README.md), [ADR 0024](../0024-absent-checks-fail-closed/README.md)
- **Superseded in part by:** [ADR 0229](../0229-required-contexts-need-success-and-finished-runs/README.md) — the workflow-run read is unconditional, so Actions (read) is required for every head
- **Category:** merge-gate admission (sensitive class)

## Context

ADR 0184's `scripts/assert-mergeable-head.sh` requires every required context on a
pull request's head to be completed and passing (Gate A) and every other check to be
passing (Gate C). It unions the conclusions of every check run sharing a context
name. ADR 0184 did not consider a head carrying more than one *run* of one workflow.

Since ADR 0223 (#1740) a pull request opened as a draft and then marked ready starts
a second `actions-ci` run on the same head, and the workflow's per-PR concurrency
group cancels the first. Each run is its own check suite, so the check-run endpoint's
default `filter=latest` — which collapses re-run attempts *within* a suite, verified
live on `d762771c` — keeps both suites, and the union reads `SUCCESS,CANCELLED`:

- #1750 head `d762771c`: run 38484712716 (#2831) cancelled, 38484728240 (#2832)
  succeeded; Gate A exit `3` on `shell-tests (SUCCESS,CANCELLED)`. The cancelled run
  also carried an unexpanded matrix check `shell-test-groups` that the passing run
  reports only as expanded legs (`shell-test-groups (merge-gate)`, …). Lifecycle
  re-arm runs 38484720322 (#73) and 38484725729 (#74) were cancelled and superseded
  by 38484728939 (#75); Gate C exit `5` on `rearm / event-policy=CANCELLED`.
- #1753 head `c10425e6`: run 38486174572 (#2833, the draft-time `opened` run)
  cancelled by #2834 (`ready_for_review`), which succeeded; Gate A exit `3`.

The workaround — `gh run rerun` of the cancelled run — costs a full CI run per PR
and teaches operators to re-run red checks until the gate goes quiet.

## Decision

**Gates A and C set aside exactly one shape: a check run that is `COMPLETED` with
conclusion `CANCELLED`, whose own workflow run (placed by `check_suite_id`)
completed with conclusion `cancelled`, when the NEWEST later run of the SAME
workflow (`workflow_id`), under the SAME event, on THIS head, completed with
conclusion `success` AND that run's own check suite reports the same check name
only as a completed `SUCCESS`, or not at all.** "Later" means a strictly greater
`run_number`. A same-named `SKIPPED`, `NEUTRAL`, `CANCELLED`, `FAILURE`,
conclusion-less, or unfinished check in the superseder keeps the cancellation
blocking. A required context set aside where the superseder omitted the check must
still carry a `SUCCESS` of its own after exclusion. Every dismissal is reported as
a `::notice::` naming the check run, the cancelled run, and the run that superseded
it.

Nothing else changes:

- The passing conclusion set (`SUCCESS`, `NEUTRAL`, `SKIPPED`) is not widened.
- A failure, a pending check, a cancellation nothing later superseded, a cancelled
  job inside a run that ended any other way, and every commit status still block.
- A required context whose only report was a superseded cancellation becomes
  *absent*, which Gate A refuses (ADR 0024): dismissal removes evidence and never
  manufactures it.
- Gate B reads the raw inventory. A `deferred-ci` that ran is execution evidence,
  and a rule written for the pass/fail gates does not reach it.
- Provenance between apps is unchanged; Gate A's `wrong producer` check still runs
  on what remains.

The workflow-run read (`repos/{repo}/actions/runs?head_sha=`) is made only when the
head carries a cancelled check run, so callers whose token lacks Actions read are
unaffected unless the head would otherwise block. The read is paginated and
reconciled against `total_count`, every record is type-checked (never
truthiness-checked), every record must name this head, and each check suite must
belong to exactly one run; any violation is exit `1`.

### Why `run_number`, and why per run rather than per check name

- `run_number` is GitHub's per-workflow event sequence: an integer assigned at
  creation and unique within a workflow, so distinct runs never tie, and it is the
  order concurrency supersedes by. Job `started_at`/`completed_at` are
  second-granular and a cancelled job reported `completed_at` *before* `started_at`
  on `d762771c` (check run 115595620111). Check-run ids are re-issued when an older
  event's run is re-run, so they would order an old event after a new one.
- A run's `success` is necessary but not sufficient. GitHub concludes a run
  `success` when its guarded jobs were all `SKIPPED`, so the superseder's own report
  of the same check is consulted: it must be `SUCCESS` or absent.

### Why the run conclusion and the event are not enough

`event` cannot tell a draft-time run from a ready-time one: `opened`,
`ready_for_review`, `converted_to_draft`, and `labeled` are all `pull_request`. A
first draft of this decision argued that the later run "carries the later event's
verification intent". That is wrong. Ready, then `converted_to_draft` (whose run
cancels the full run and succeeds with ADR 0223's Tier 2 skipped), then ready again
produces a later `success` run whose heavy checks are `SKIPPED`, as does an adopter
workflow that guards heavy jobs off `labeled`. The independent security review's
attack A1 (full jobs cancelled in run 10, same-named checks `SKIPPED` in a `success`
run 11) passed that draft and exits `3` on `main`. The rule therefore asks what the
superseder did with *that check*: only a `SUCCESS`, or no report at all, vouches.

Only the newest later successful run is consulted, so an older full run cannot be
cherry-picked over a newer run that skipped the work. A cancellation after an
earlier success blocks for the same reason any unsuperseded cancellation does:
nothing later did the work.

### Why a superseder that omits the check is accepted

#1750's cancelled run carried the matrix parent `shell-test-groups`, which the
passing run reports only as expanded legs (`shell-test-groups (merge-gate)`, …).
Requiring a same-named `SUCCESS` would leave that real case blocked. Omission
cannot hide a skip, because a job skipped by `if:` still reports under its
unexpanded name (`shell-test-groups` `SKIPPED` on `c10425e6`). To keep omission from
backing a required context with nothing, Gate A requires every required context
that had a cancellation set aside to carry a `SUCCESS` of its own after exclusion;
`SKIPPED` or `NEUTRAL` elsewhere is not enough.

### Why same workflow and same event

Every Actions workflow publishes through the one GitHub Actions app, so app id
alone would let a different workflow with a same-named job supersede a cancellation.
The same workflow under a different event (`push`, `workflow_dispatch`) verifies a
different ref or different `if:` paths.

## Alternatives rejected

- **Newest completed check run per (app, name).** App id cannot tell workflows
  apart, timestamps cannot be trusted to order events, and it cannot resolve the
  unexpanded matrix name. It would also let a later success hide an earlier failure.
- **Prevent the cancellable run upstream** (do not cancel draft-time runs, or start
  no run on draft heads). Covers only this repository's `actions-ci`. The lifecycle
  re-arm cancellations and every adopter's workflows produce the same shape, and the
  assertion is the organization's admission tool. Keeping cancellation cheap is also
  the point of the concurrency group.
- **Accept any cancelled conclusion.** That widens the conclusion set, which ADR
  0184 exists to prevent.

## Consequences

- A drafted, readied, green pull request is admissible without re-running
  superseded work when the ready-time run did the cancelled checks' work (#1750's
  shape). A cancellation that is the newest word on a workflow still blocks.
- **Known limit, accepted:** when the superseding run legitimately skips a cancelled
  check — #1753's documentation-only head, where ADR 0222 skipped
  `shell-test-groups` and `hosted-compatibility-tests` in the ready-time run — the
  cancellation still blocks under Gate C and still needs `gh run rerun`. That shape is
  indistinguishable from attack A1, and the strict reading is the safe one.
- **Known limit, accepted:** `actions/runs?head_sha=` returns every run on the
  commit, including runs for another pull request or a fork that share the head. The
  commit-level check-run inventory already mixes them the same way, so this adds no
  new class, and the per-check `SUCCESS`-or-absent rule removes the dangerous
  variant, where a foreign run's skip vouches for a cancelled check.
- Fine-grained callers need Actions (read) only when a cancelled check run is
  present; without it they fault with a message naming the grant, which is no worse
  than the exit `3`/`5` they received before. Callers documenting their grant set
  outside this repository (the agent guidance's admin-merge procedure) should add
  it; that is a follow-up in the owning repository.
- A cancelled run that is later re-run keeps its `run_number`, so a re-run of an
  older event never supersedes a newer one, and if the newer run is the one re-run,
  its latest attempt is what the check-run and workflow-run reads both report.

## Verification

`scripts/assert-mergeable-head.test.sh` (registered under `merge-gate` in
`scripts/actions-ci-groups.tsv`) adds stubbed `actions/runs` fixtures for: the #1750
superseded shape including the unexpanded matrix name (passes, with the dismissal
reported); the #1750 re-arm shape under Gate C (passes); and refusals for attack
A1 with `SKIPPED` and `NEUTRAL` under Gates A and C, a superseder reporting the
same check as cancelled, failed, conclusion-less, or unfinished, an older full run
cherry-picked over a newer skip, a run-number tie, a set-aside required context
backed only by a `SKIPPED`, a cancelled-only context, a cancellation after an
earlier success, a later run of the same workflow that ended without succeeding, a
success followed by a failure, a failure inside a superseded run, a newer run still pending, a
different workflow, a different event, a cancelled job inside a failed run, an
unfinished run on either side, an in-progress record carrying a cancelled
conclusion, an unplaceable check run, a context reported only by a superseded
cancellation, a superseded `deferred-ci` (Gate B), a foreign head SHA, a duplicate
check suite, eight malformed record shapes, an unattested or truncated walk, and a
failed read. Each new guard was mutated out and the suite failed, except two
equivalent mutants kept as defense in depth: the `type == "object"` record check
(jq already errors when indexing a non-object, which faults the same way) and the
exactly-one-run placement check (the suite-uniqueness validation rejects duplicates
first). The mutation table is recorded on the pull request.

## Amendment (2026-10-15) — a run displaced from a declared concurrency family

### Context

GitHub keeps at most one *pending* run per concurrency group: when a newer run joins
a group whose pending slot is taken, the older pending run concludes `cancelled`
without a runner or a step. `gate-rearm.yml` declares the workflow-level group
`ai-review-arm-${{ github.event.pull_request.number }}` with
`cancel-in-progress: false`, and every caller of it — the ruleset's required
`AI review authorization arm` run, `gate re-arm`, `AI review label re-arm`, and
`AI review lifecycle re-arm` — joins that one group. So a run can be displaced by a
run of a *different* workflow, which the decision above (same `workflow_id`, same
event) never sets aside.

Observed live on Verjson/verjson-agents#432, head `ae5f8326`, on 2026-10-15: run
38787495945 (`gate re-arm`, created 18:39:57) was cancelled with zero steps and
`runner_id` 0 while `AI review authorization arm` run 38787495335 held the group;
38787540131 (`AI review lifecycle re-arm`, 18:40:15) took its pending slot and was
itself replaced by 38787543084 (18:40:16, `success`, every job `success`).
`scripts/assert-mergeable-head.sh Verjson/verjson-agents 432` exited `5` on
`rearm / event-policy=CANCELLED` until a manual `gh run rerun`.

### Decision

Gates A and C also set aside a `COMPLETED`/`CANCELLED` check run whose own workflow
run concluded `cancelled` when all of the following hold. Anything else keeps the
cancellation blocking.

1. **Declared family.** The run belongs to a family in the checked-in
   `CONCURRENCY_FAMILIES` map in `scripts/assert-mergeable-head.sh` — never by name
   resemblance. A member is either a listed caller (exact workflow `name` and `path`)
   whose `referenced_workflows` names the declaring workflow (owner and repository
   compared case-insensitively, the path exactly), or the declaring workflow itself:
   its exact name and path, run directly in the declaring repository or as a
   required workflow whose `workflow_url` names that run's own
   `required_workflows/<workflow_id>`. Its `event` must be the family's event.
   `scripts/ci-gate/concurrency-family-map.test.py` rebuilds the map from every
   reusable workflow declaring a group keyed only on the pull-request number with
   `cancel-in-progress: false`, and from every generator and workflow calling it.
   Each caller must be a pure single-job call on the family event without a
   different group. The test fails when the map disagrees in either direction.
   **Both runs must also be bound to this pull request**: their `head_branch` must
   equal its head branch and their `head_repository.full_name` its head repository
   (case-insensitively). An unknown pull-request head identity binds nothing.
2. **Displacement-shaped.** The jobs of the attempt the run record reports
   (`actions/runs/{id}/attempts/{run_attempt}/jobs`, paginated, reconciled against
   `total_count`, every record type-checked and bound to this run and head) are read.
   A job may report an earlier `run_attempt` than the one listed: a re-run of failed
   jobs can carry over jobs that passed. Such a job is well-formed but is not part of
   this attempt's shape. A later `run_attempt` faults. The jobs of this attempt are
   non-empty. Every job is `completed`/`cancelled`, ran no step, and never
   acquired a runner (`runner_id` 0 or null), and the cancelled check run is one of
   them (a job id is its check run id). A run whose job started is interrupted, not
   displaced.
3. **A later-created successful family run did the work.** The newest family run on
   this head whose `created_at` is strictly later than the cancelled attempt's
   `run_started_at` concluded `success`. Its own check suite is non-empty and every
   check in it is a completed `SUCCESS`. A different workflow names its checks
   differently, so the same-name rule above has nothing to consult. A run concludes
   `success` with guarded jobs `SKIPPED`, so the whole suite is required. Only the
   newest such run is consulted. When any other family run on this head shares that
   run's creation second, which run is newest cannot be told, and nothing displaces.

`created_at`, not `run_number`, orders here because `run_number` is per workflow and
does not compare across a family. It is second-granular, so a same-second tie is
not later and keeps the cancellation blocking. Using the attempt's
`run_started_at` keeps a family run created before a re-run attempt from displacing
that attempt. Dismissals are reported in the same `::notice::`, naming the declared
family, the cancelled run, and its displacer. The notice does not name a resolved
group, because no API reports one; it states that the runs were bound to the pull
request by head branch and head repository. Gate B, the passing conclusion set, and
every commit status are unchanged, and a required context reported only by a
displaced cancellation is absent, which Gate A refuses.

The job listing is read only for cancelled family runs, under the Actions (read)
grant the workflow-run read already requires; a refused read exits `1` naming it.

### Consequences

- The #432 shape is admitted without a re-run. The displacement path is proven by
  fixtures that reproduce its shape, plus a hand check of the live attempt-1 job
  (116603178508: `runner_id` 0, zero steps, conclusion `cancelled`; displacer run
  38787543084). It has not been proven by a live end-to-end replay of an unmodified
  head: the live head was re-run before this rule existed. The replay recorded on the
  pull request restores that attempt from those live payloads.
- **Known limit, accepted:** the map follows the current tree. A run pinned to an
  older declaring SHA is admitted on the current declaration. `ai-review-arm-<PR>`
  has not changed since the group was introduced, and the displacement shape already
  requires the cancelled run to have done nothing.
- **Known limit, accepted: the group itself is not observed.** The group key is the
  pull-request number. A workflow run reports no resolved concurrency group, and its
  `pull_requests` array is empty for these `pull_request_target` runs. This was
  verified on verjson-agents#432's runs 38787495945, 38787540131, and 38787543084.
  Binding by head branch and head repository excludes runs of other branches, other
  repositories, and forks. It cannot tell apart two open pull requests from the same
  head branch into different bases, which resolve to different groups. Both runs must
  still match this head, and the displacer must still have run the family's whole
  suite to `SUCCESS` on it.
- A family whose displacer skipped work still needs `gh run rerun`, as #1753's shape
  does.

### Verification

`scripts/assert-mergeable-head.concurrency-family.test.sh` and
`scripts/ci-gate/concurrency-family-map.test.py`, both registered under
`merge-gate` in `scripts/actions-ci-groups.tsv`. Each guard above was mutated out
and the suites failed. The mutation table and its equivalent mutants are recorded on
the pull request.

## Amendment (2026-10-15) — a non-CANCELLED check of a superseded cancelled run

### Context

An `if: always()` aggregator job keeps running after concurrency cancels its run. It
reads its cancelled inputs and concludes `FAILURE`, not `CANCELLED`. The decision
above sets aside only `CANCELLED` check runs, so a head whose newest later run of the
same workflow re-did that aggregator to `SUCCESS` was still refused. Its Verification
section pinned that refusal ("a failure inside a superseded run").

Observed live on Verjson/.github#1824, head `33bb4d6b`, on 2026-10-15:

- `actions-ci` run 38803591718 (#2960, created 20:38:30) concluded `cancelled` on
  attempt 1. Its other jobs were `cancelled` with zero steps.
- Its `shell-tests` aggregator ran 3 steps, saw `SCOPE_RESULT=cancelled`, and
  concluded `FAILURE` ("changed-path classification failed").
- Run 38803594552 (#2961, created 20:38:32) succeeded. Gate A refused with
  `shell-tests not passing (SKIPPED,FAILURE)`.

### Decision

A completed check run with **any** non-passing conclusion is now eligible for the
supersession above, when its own workflow run concluded `cancelled`. All of the
decision's conditions still apply: the newest later (`run_number`) successful run of
the same workflow, under the same event, on this head. One rule is stricter for a
conclusion other than `CANCELLED`: the superseder must report the **same check name**,
and only as a completed `SUCCESS`. Omission does not vouch for it. The
matrix-parent rationale above covers only a check that never ran.

A same-named `SUCCESS` is necessary but not sufficient. A re-run attempt replays the
same event payload, so its passing check repeats the failed work. A new run is a
different event: a `labeled` or `ready_for_review` run whose job gates its steps on
the event (`gate-rearm.yml`'s event-policy job is one) can report `SUCCESS` having
done nothing. So a non-`CANCELLED` check is set aside only when it **completed
strictly after the cancel landed**, which makes it a failure the cancel caused. A
genuine failure that finished before the cancel stays red.

The cancel's time is the **latest** `completed_at` of a `CANCELLED` check run in the
same suite. A run-level cancel cancels every in-flight job at once, so it is the last
cancellation in the suite. An earlier cancellation, such as a fail-fast matrix leg or
a job timeout, has nothing to do with it and anchors nothing.

**With no `CANCELLED` check in the suite, a non-`CANCELLED` check stays red.** Nothing
observable says when that run's cancel landed. A first revision of this amendment
used the superseding run's `created_at` as a stand-in. The re-review at `1f9d79e6`
rejected it: a run can outlive its successor's creation. That happens when a
manual or watcher cancel lands later, or when a workflow without cancel-in-progress
leaves its successor pending. In that case a genuine failure completing after the
successor's creation would have been set aside.

`completed_at` is now part of the normalized check-run inventory. Timestamps are
second-granular, so a failure completing in the same second as the cancel stays red.
An absent or unreadable time on either side also stays red.

The first review proposed the mere presence of a `CANCELLED` sibling as enough. That
would admit a genuine failure that finished before the cancel whenever any sibling
was later cancelled, so the sibling supplies the cancel's time instead.

**Residual risk, accepted.** The latest `CANCELLED` sibling stands in for "the cancel
caused this failure". It is evidence of when *a* cancellation landed, not proof of
causation. A check that completed after it is set aside, and two cases remain:

- **An unrelated cancellation is the latest one in the suite.** For example, a
  fail-fast leg was cancelled and then the run itself was cancelled while only an
  `if: always()` job was still running.
- **A job really fails after the run-level cancel.** This is a job guarded to run
  anyway, such as `if: always()` or `if: cancelled()`, that fails for its own
  reasons.

Both are accepted for three reasons:

1. A job that is still running when its run is cancelled is itself cancelled unless
   it is guarded to run anyway. So a non-`CANCELLED` conclusion that completed after
   the latest cancellation comes from exactly such a guarded job, which is the
   aggregator shape this amendment exists for.
2. The failing check must still be re-done to a completed `SUCCESS` under the same
   name by the newest later successful run of the same workflow, event, and head. A
   fail-fast trigger itself finishes before the legs it cancels, so it stays red.
3. Gate A still requires every required context to carry a `SUCCESS` of its own.

I found no observable signal tighter than this. A run reports no cancellation time or
cause. Job `started_at`/`completed_at` are second-granular, and a cancelled job can
report `completed_at` before `started_at` (check run 115595620111). The check-run
`output` text varies by workflow.

#1824 meets this rule. All five siblings were cancelled at 20:38:32, so both the
earliest and the latest are 20:38:32, and `shell-tests` completed at 20:38:38. A
replay of its live payloads, with the successor's skipped checks set to `SUCCESS`,
exits `0` through this path. The real successor skipped them, so the live head stays
refused for that reason. Evaluation now starts whenever the head carries any
non-passing check run, not only a `CANCELLED` one. The concurrency-family
displacement amendment above stays limited to `CANCELLED` checks; its job-shape test
excludes a job that ran anyway.

### Why a same-name `SKIPPED` does not vouch

The request that prompted this amendment also proposed accepting a same-name
`SKIPPED` "where the newer run legitimately skips it". That reintroduces this ADR's
attack A1 for real failures. A full run whose tests genuinely failed, then cancelled
by a later `converted_to_draft` run (ADR 0223 Tier 2 skipped), would hide the failure
behind the skip. For a required context, Gate A would still refuse, because ADR 0229
requires a `SUCCESS` of its own. For an advisory check, nothing would. So a skip
re-does nothing and keeps the failure blocking.

#1824 itself is that shape. The later run #2961 was a draft-tier run that skipped
`shell-tests`, `shell-test-groups`, and `hosted-compatibility-tests`, so the heavy
tier never ran on the head. The head stays refused, correctly, until a run that does
the work succeeds. This is the known limit recorded above for #1753.

### Verification

`scripts/assert-mergeable-head.test.sh`: the earlier refusal is inverted into an
admission when the superseder re-did the check to `SUCCESS`. New refusals cover a
superseder that reports the check `SKIPPED` or `NEUTRAL` (the #1824 shape) or omits
it, a failure in a run that ended `failure`, `success`, or `timed_out`, a different
workflow, a different event, an earlier run number, and a later run that did not
succeed, plus the Gate C equivalents. Timing has its own cases:

- A failure completed after the latest cancelled sibling is admitted.
- A failure completed before the cancel, in the same second, between an earlier and
  the latest cancellation, or at an unknown time stays red.
- A real failure completed after the superseding run's creation, in a run with no
  `CANCELLED` check, stays red.
- Any cancelled sibling without a time, alone or among timed ones, leaves the cancel
  time unknown.

Each condition was mutated out and the suite failed.

The suite's runtime margin is recorded too. `assert-mergeable-head.test.sh` has
declared `@budget=120` since this amendment, because the PR row default is 60s. It
took 85s locally on the re-reviewed head under host load average 15, and 89-90s on
the final head under load average 9-10, which leaves about 30s of margin. CI ran its predecessor in 51s on `main` run 38799238936. The mutation table is on the pull
request.
