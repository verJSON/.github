# 0048 — Merge-gate jobs take the fast lane when the target is public

- **Date:** 2026-08-03
- **Issue:** [Verjson/.github#334](https://github.com/Verjson/.github/issues/334)
- **Amends:** ADR 0047 (corrects its reasoning about polling jobs)
- **Supersedes:** ADR 0033's invariant that Verjson never routes to hosted

## Context

ADR 0047 gave short jobs a fast lane and explicitly kept `gate` out of it:

> hosted runners would not fix them — a poll loop wastes a hosted minute exactly
> as it wastes a self-hosted one

That reasoning was wrong, and the fast lane did not fix the jam because of it.
It conflates two different things:

- **Waste** is identical on either fleet. A job sleeping in a 30-second poll
  burns a minute whichever runner it holds.
- **Starvation** is a property of a **fixed-size** pool. Six runners and seven
  sleepers means the seventh job waits. Hosted capacity is elastic, so a sleeping
  gate on hosted blocks nobody.

Only the second one produced the 2026-08-03 outage, and only the second one is
fixed by changing lanes. Measured that night: six `gate` jobs held all six
runners while polling for CI that could not start *because it needed those
runners* — a resource deadlock, throughput zero until they were cancelled. After
ADR 0047 shipped, `.github`'s own `preflight` still queued 19+ minutes behind
four `privileged_merge` loops, because `preflight`/`gate`/`dispatch-merge`
were still self-hosted. Routing `shell-tests` alone moved the job that was not
the problem.

There is a second, independent argument. `gate` checks out untrusted pull
request head code and runs a model over it on a **persistent** runner. A
disposable hosted VM is stronger isolation than that, and is the isolated lane
ADR 0033 wanted the organization to build.

## Decision

`preflight`, `gate` and `dispatch-merge` route to `VERJSON_RUNNER_FASTLANE`
when the target repository is **public**, and stay on the self-hosted pool
otherwise.

The split is on visibility because that is where the cost sits. Public
repositories run hosted at $0 (billing measured 2026-08-01). Private ones meter
against a spending limit that was reached at exactly $20.00 on 2026-07-17, and
`gate` is both the longest job and one that runs on every pull request — so
routing private targets to hosted would be the most expensive possible change.

Two details are deliberate:

1. **`preflight` keys on `github.event.repository.private`; `gate` and
   `dispatch-merge` key on `needs.preflight.outputs.target_private`.**
   preflight cannot consult the resolved value because it is the job that
   resolves it. The later jobs must not use the event, because on the
   `workflow_dispatch` re-gate path `github.event.repository` is the
   dispatching repository, not the target.

2. **The test is `== 'false'`, never `!= 'true'`.** Visibility that fails to
   resolve is the empty string, and it must fall through to self-hosted. Written
   the other way, an unreadable repository would silently start spending hosted
   minutes — the failure mode being guarded against is a *bill*, so the default
   has to be the fleet that is already paid for.
   `runner-routing-policy.test.sh` pins both polarities; the `!= 'true'`
   mutant fails it.

## Consequences

- The deadlock class is removed for public targets: elastic capacity cannot be
  exhausted by sleepers.
- Self-hosted capacity is left to private repositories, which is where it is
  actually required.
- Untrusted public PR code now runs on a disposable VM rather than a persistent
  runner — an isolation improvement, not just a scheduling one.
- Private repositories keep the old behaviour, including the old deadlock risk.
  Fixing that needs either budget or the real fix below.
- ADR 0033's "Verjson never reaches hosted" invariant is retired. The routing
  test asserted it; that assertion has been replaced by the visibility split
  rather than deleted.

## What this still does not fix

The gate holds a runner *at all* while waiting for checks it cannot influence.
Hosted capacity makes that stop hurting other jobs; it does not make it
efficient, and for private targets it does not help at all. The real fix is to
stop waiting on a runner — re-enter on `workflow_run: completed` instead of a
30-second poll — which is a larger change tracked separately.

## 2026-10-16 amendment — a public target leaves the fast lane

[ADR 0235](../0235-public-pull-request-code-uses-ephemeral-runners/README.md)
decision 7 sends every `ai-review-merge.yml` job of a public target to
`CI_LANE_UNTRUSTED` with a hosted tail instead of `CI_RUNNER_FASTLANE`. The
`gate` job checks out and reviews the pull request head, and the fast lane is the
persistent general pool today. A private target keeps the lanes this record and
its successors chose; only a payload that says private, with a lookup that does
not say public, reaches them.
