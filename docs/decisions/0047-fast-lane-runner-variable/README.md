# 0047 — A fast lane for short CI jobs, selected by variable

- **Date:** 2026-08-03
- **Issue:** [Verjson/.github#332](https://github.com/Verjson/.github/issues/332)
- **Amends:** ADR 0034 for `.github` repository validation routing
- **Corrects:** ADR 0033's premise that GitHub-hosted minutes are unfunded

## Context

The organization runs six self-hosted runners, all carrying the same labels
(`self-hosted, Linux, X64, gce, gate, GCP, general`). The `gate`, `general`, and
`isolated` "pools" named by `VERJSON_RUNNER_DEFAULT`, `VERJSON_RUNNER_UNTRUSTED`
and `VERJSON_RUNNER_ISOLATED` all resolve to those same six machines, so the
routing taxonomy names one queue three times.

That queue is shared by two workloads with opposite shapes. `gate` and
`privileged_merge` hold a runner for 10–40 minutes while **polling** — sleeping
in 30-second increments waiting for checks they cannot influence. `shell-tests`,
`actionlint` and consumer `build-test` are minutes of actual CPU. On one queue
the sleepers evict the workers.

Observed 2026-08-03: six gate jobs each holding a runner while waiting for CI
that could not start, because the CI needed the runners the gates were holding —
a resource deadlock, not a shortage. Throughput went to zero until the gates were
cancelled. Separately, `.github`'s own `shell-tests` queued ~20 minutes behind
consumer repositories, so the repository that ships fleet-wide fixes was the one
waiting on the fleet. This is a priority inversion; adding runners raises the
threshold at which it bites but does not remove it.

ADR 0033 asserted that GitHub-hosted minutes are unfunded and that `ubuntu-24.04`
is "a guaranteed failure, not a fallback." Billing data measured 2026-08-01
contradicts this: public repositories run hosted at `netAmount: 0`, and private
ones were capped by a $20 spending limit reached 2026-07-17 — a budget knob, not
an architectural impossibility.

## Decision

Short, non-polling jobs run in a **fast lane** that the long pollers cannot
occupy, and the lane is chosen by a **variable**, never a hardcoded label.

`VERJSON_RUNNER_FASTLANE` (org-level, `visibility: all`) currently holds
`["ubuntu-24.04"]`. `.github` is public, so those minutes are free. The routing
expression is a fallback chain:

```yaml
runs-on: ${{ fromJSON(vars.VERJSON_RUNNER_FASTLANE || vars.VERJSON_RUNNER_DEFAULT || '["self-hosted","general"]') }}
```

Three properties follow, and `scripts/ci-gate/runner-routing-policy.test.sh`
asserts them:

1. **Repointable from org settings alone.** Standing up a dedicated self-hosted
   fast pool later is a variable edit, not a workflow edit across N repositories.
   This is the whole reason it is not `runs-on: ubuntu-24.04`.
2. **Degrades to the general pool, not to nothing.** An unset or emptied variable
   falls back to ADR 0034's `general`, so losing the variable slows CI rather
   than breaking it.
3. **The hardcoded form is a test failure.** Reverting to
   `runs-on: [self-hosted, general]` fails the policy test, so the exception
   ADR 0034 carved out cannot silently return.

This ADR deliberately does **not** move `gate` or `privileged_merge`. They are
the jobs whose runner occupancy is the actual problem, and hosted runners would
not fix them — a poll loop wastes a hosted minute exactly as it wastes a
self-hosted one. Their fix is to stop holding a runner while waiting
(`workflow_run: completed` rather than a 30-second poll), which is a larger
change and is tracked separately.

## Consequences

- `.github`'s validation suite no longer queues behind consumer repositories, so
  a fleet-wide fix can be shipped while the fleet is saturated.
- Self-hosted capacity is freed for the work that genuinely needs it: jobs
  touching private repositories or requiring the org's network position.
- A new dependency on GitHub-hosted availability for this one lane. Bounded: the
  fallback chain means an outage or a policy change degrades to `general`.
- **Private repositories must not adopt this lane without checking the spending
  limit first.** The variable is org-visible, so the constraint is documented
  here rather than enforced by the variable's scope. Extending the fast lane to
  private repositories needs its own decision.

## Rollback

Delete `VERJSON_RUNNER_FASTLANE`, or set it to `["self-hosted","general"]`. No
workflow change is required — that is the point of routing through a variable.

## 2026-10-15 amendment — `actions-ci` leaves the fast lane

[ADR 0235](../0235-public-pull-request-code-uses-ephemeral-runners/README.md)
moves every `actions-ci.yml` job from `CI_RUNNER_FASTLANE || CI_LANE_TRUSTED`
to `CI_LANE_UNTRUSTED` with a hosted tail. Those jobs run pull-request code in a
public repository, and the fast lane resolves to the persistent general pool
today. The rollback above (set the variable to `["self-hosted","general"]`) is
therefore no longer a safe choice for any workflow that public pull-request code
can reach.
