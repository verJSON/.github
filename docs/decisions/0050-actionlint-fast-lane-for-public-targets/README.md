# 0050 — actionlint takes the fast lane on public targets

- **Date:** 2026-08-03
- **Issue:** [Verjson/.github#346](https://github.com/Verjson/.github/issues/346)
- **Amends:** ADR 0047 (extends the fast lane to the jobs the gate waits on)

## Context

ADRs 0047 and 0048 moved `shell-tests`, `preflight`, `gate` and `dispatch-merge`
onto elastic hosted capacity. They did not move **what the gate waits on**, so
the gate got cheaper at waiting while the jobs it waits for got no relief. The
wall-clock outcome was unchanged.

Measured on this repository's own PR #339 at 13:57Z, twelve minutes in:

```
actionlint       QUEUED       since 13:45:37Z
npm-cache-seed   QUEUED       since 13:45:36Z
gate             IN_PROGRESS  since 13:45:55Z   ← polling for both
general runners: 6/6 busy
```

Across the organization at that moment: 6/6 runners busy and roughly fifteen
runs queued, most of them merge-gate poll loops. `verjson-payments`'
`ci / build-test` had been queued **45 minutes** without ever being assigned.

ADR 0048 already recorded this pattern once — routing `shell-tests` alone "moved
a job that was not the problem". It recurred one layer down.

## Decision

`actionlint` routes to `VERJSON_RUNNER_FASTLANE` when the target repository is
**public**. It is a short, secretless, pure-CPU job — exactly ADR 0047's
fast-lane profile — and under ADR 0048 a public target's hosted minutes are
free. Running untrusted public PR content on a disposable VM rather than a
persistent runner is the same isolation improvement ADR 0048 claimed for the
gate.

Private targets keep `VERJSON_RUNNER_DEFAULT`. Nothing about their funding has
changed.

### The predicate is `visibility == 'public'`, not `private == false`

This is the load-bearing detail. `github.event.repository.private` is a boolean
that Actions coerces to `0` when the payload does not carry it, and `0 == false`
is **true**. Keying the public branch on `private == false` therefore sends an
*unreadable* repository to the fast lane — fail-open, and precisely the failure
ADR 0048's `== 'false'` polarity rule exists to prevent.

`visibility` is a string. An unresolved payload gives `''`, and `'' == 'public'`
is false, so the unresolved case falls through to `VERJSON_RUNNER_UNTRUSTED` —
self-hosted, which is capacity already paid for.

The fail-open form was written first here. It passed all three positive cases
and failed only the unresolved one, which is how it would otherwise have
shipped.

## What this does not fix

`npm-cache-seed` was the other job starving PR #339, and it must **not** move.
`node-cache-integration.yml` exists to validate the *persistent* runner's local
npm download cache; a disposable hosted VM would delete the thing under test.
Its `runs-on: [self-hosted, general]` is a deliberate exemption.

The problem there is coupling, not routing: a cache-integration probe should not
be a per-PR check that an unrelated PR's merge gate blocks on. #346 tracks that
separately. (PR #339 triggered it by editing a comment in `node-ci.yml`, which
is a legitimate match for its existing path filter.)

This also does not address the gate holding a runner while polling at all, which
is #341, or the watchdog's inability to preempt these cases, which is #343 —
`actionlint` and `npm-cache-seed` are not poll workflows, so preempting a gate
here frees a hosted slot and leaves the real blockage untouched.

## 2026-08-07 completion: decouple the persistent-cache probe

The persistent-cache probe remains on `VERJSON_LANE_TRUSTED`. Its two jobs
exercise state that exists only on persistent runners, so routing them to a
disposable public fast-lane runner would make the test meaningless.

The coupling is removed at the trigger boundary instead. The probe no longer
runs for pull requests. It runs after relevant changes merge to `main`, on a
weekly schedule, and by explicit dispatch. This preserves pre-merge enforcement
where it actually exists: the current organization ruleset requires only
`shell-tests`, not either `npm-cache-*` job. A docs-only or workflow-comment PR
therefore cannot allocate persistent-runner capacity for this probe or make the
merge gate wait for it.

The semantic routing matrix added by #357 now covers actionlint's public,
private, unresolved, and external cases. The same policy suite separately pins
both cache-probe jobs to the trusted lane so a future fast-lane sweep cannot
erase the persistent-runner exemption by accident.

## Consequences

- A public repository's linter stops competing with merge-gate poll loops for a
  fixed pool.
- Self-hosted capacity is left to private repositories, which is the population
  that has no alternative.
- `actionlint.yml` is now **evaluated** by `runner-routing-policy.test.sh`
  rather than only grepped. It was excluded before by an accident of syntax:
  `inputs.github-hosted-runner` is a legal Actions reference and an illegal JS
  one, parsing as `inputs.github - hosted - runner`, so the shared expression
  evaluator threw on it. The evaluator now rewrites that reference, which is why
  the fail-open predicate above was caught here rather than in production.
- Four polarities are pinned — public, private, unresolved, and unset fast lane —
  plus a mutation check asserting the `private == false` form is absent, so the
  regression cannot return through a rewrite that keeps the positive cases green.
- `actionlint-reusable.test.sh` pins the whole `runs-on` line literally, so this
  expression is now asserted in two suites. That is deliberate duplication: one
  checks meaning, the other checks the exact bounded mapping.

## Amendment (2026-11-15, #1831) — public targets leave the persistent fast lane

`CI_RUNNER_FASTLANE` now names the persistent general pool (ADR 0235). A public
target of `actionlint`, including a `workflow_call` whose caller event is not a
public pull request, selected that variable and could run on the pool.

Public visibility now selects
`fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]')`. That branch does not
read `CI_RUNNER_FASTLANE` or `CI_LANE_FALLBACK`. Private targets stay on
`CI_LANE_TRUSTED`. Unresolved visibility still prefers the untrusted lane and
may use `CI_LANE_FALLBACK` only when that lane is unset. The predicate remains
`visibility == 'public'`; `private == false` stays absent. The 2026-08-03 title
records the original fast-lane decision; this amendment is the public route.
