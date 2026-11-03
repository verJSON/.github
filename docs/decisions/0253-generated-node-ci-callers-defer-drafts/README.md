# 0253 — Generated Node CI callers skip drafts and cancel only on new commits

- **Date:** 2026-11-03
- **Status:** Accepted
- **Issue:** [verJSON/.github#1984](https://github.com/verJSON/.github/issues/1984), part of [#1869](https://github.com/verJSON/.github/issues/1869)
- **Category:** required-check scope (sensitive class)
- **Related:** [ADR 0024](../0024-absent-checks-fail-closed/README.md),
  [ADR 0178](../0178-publish-deferred-ci-as-its-own-check/README.md),
  [ADR 0223](../0223-draft-pull-requests-defer-heavy-ci/README.md),
  [ADR 0226](../0226-superseded-cancelled-check-runs-do-not-block-admission/README.md),
  [ADR 0229](../0229-required-contexts-need-success-and-finished-runs/README.md),
  [ADR 0238](../0238-node-ci-cost-levers/README.md)

## Context

ADR 0223 defers heavy CI on drafts for this repository's own workflows. The
callers that `scripts/gen-node-ci-caller.py` generates for adopters had no
draft handling, so every draft push ran the full build and test plan. One
adopter spent 55,609 runner-minutes in 20 days (#1869). The owner directed on
2026-10-28 that draft pull requests run no CI tests, and that a draft skip must
never act as a passing gate for a ready pull request.

The caller's concurrency cancelled any running run on every `pull_request`
event. A `ready_for_review` run on the same head would therefore cancel a
running ready run and leave its required check cancelled (verjson-agents#457).

#1984 proposed guarding the jobs inside `node-ci.yml`. The merge gate
(`scripts/assert-mergeable-head.sh`) and the caller DAG rule that out:

- **A draft must skip, not fail.** Gate C ignores only failures from cancelled,
  superseded runs. A draft run that concluded `failure` would keep its failure
  on the head after the ready run passed, so a pull request whose last commit
  was pushed as a draft could not merge. Gate A requires a SUCCESS for every
  required context, so a skip never satisfies one, and SKIPPED counts as
  passing for Gate C.
- **A job skipped inside the reusable workflow still yields a successful call.**
  The `test-levels` aggregate would then read every level as `success` and
  report SUCCESS on a draft, which is the defect ADR 0178 exists to prevent.
  The artifact DAG would evaluate `fromJSON` over empty outputs and fail.
- **A default-on guard inside the callee would change every adopter that bumps
  its pin.** That includes hand-written callers without `ready_for_review`, which
  nothing would re-run once ready, and the Tier 1 `Authn required type surface`
  check, which ADR 0223 runs on drafts through `node-ci-protected.yml`. An
  opt-in input would need an edit to each caller, so it costs the same as a
  caller-level guard and splits one policy across two files.

Draft scheduling depends on the trigger, and the caller owns the trigger. That
is the same reason concurrency already belongs to the caller (ADR 0238).

## Decision

1. Every job that `gen-node-ci-caller.py` emits, the `test-levels` aggregate and
   the artifact DAG included, carries `!github.event.pull_request.draft`. A
   draft holds no runner. On a draft the single-job caller reports no
   `ci / build-test`: an absent required context fails closed, both natively and
   under ADR 0024. A skipped `test-levels` is never Gate A's SUCCESS.
2. The generated `pull_request` trigger requests `opened`, `synchronize`,
   `reopened`, and `ready_for_review`. Marking a pull request ready runs the
   whole plan on its head. `converted_to_draft` is not requested, because it
   could only start a run that skips everything.
3. `cancel-in-progress` is `github.event_name == 'pull_request' &&
   github.event.action == 'synchronize'`. Only a new commit cancels the run it
   supersedes. Same-head runs queue behind the running run. Push, dispatch, and
   other events keep their run-scoped groups and are never cancelled.
4. `node-ci.yml` and `node-ci-protected.yml` are unchanged. A hand-written
   caller that wants the same behavior adds the same job guard, trigger types,
   and concurrency predicate.

## Consequences

- Adopters get the deferral by regenerating their caller, which already moves
  in step with its `uses:` pin. Hand-written callers keep their current behavior.
- The aggregate still runs under `always()` on a ready or pushed run, so an
  expected level that skipped fails it.
- For a multi-level caller, GitHub's native required-check evaluation can see the
  draft's skipped `test-levels` as passing for the whole ready run. `test-levels`
  needs every level, and its new check run may not exist until they finish. Only
  the organization merge gate (ADR 0229), itself a required check, keeps that
  window closed: it treats the unfinished ready run as pending. A merge that
  bypasses the gate could land before any level ran. The single-job caller has no
  such window, because its required `ci / build-test` is never reported on a draft.
- This departs from ADR 0223 in two ways. Levels that ADR 0223 would run on drafts
  as Tier 1, such as `lint`, are deferred too, since the generated plan cannot tell
  light levels from heavy ones. `converted_to_draft` does not cancel a ready run in
  progress, which spends those minutes so a ready run is never cancelled into a red
  required check (verjson-agents#457).
- A pull request that goes ready, then draft, then ready again on one head runs
  the plan a second time. The second run waits for the first instead of
  cancelling it. This trades duplicate minutes in a rare sequence for never
  cancelling a ready run into a red required check.
- Re-running a draft-time run replays its draft payload and still skips. The
  `ready_for_review` run is the verification run.
