# 0226 — A cancelled check run superseded by a later successful run of its own workflow does not block admission

- **Date:** 2026-10-13
- **Status:** Accepted
- **Issue:** [verJSON/.github#1754](https://github.com/verJSON/.github/issues/1754)
- **Extends:** [ADR 0184](../0184-merge-gates-assert-execution-not-absence-of-red/README.md)
- **Related:** [ADR 0223](../0223-draft-pull-requests-defer-heavy-ci/README.md), [ADR 0024](../0024-absent-checks-fail-closed/README.md)
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
