# 0076 — Bound `actions-ci` shell tests into three parallel groups

- **Date:** 2026-08-07
- **Issue:** [#233](https://github.com/Verjson/.github/issues/233)
- **Extends:** [ADR 0058](../0058-github-waits-for-checks-not-the-gate/README.md)
- **Category:** CI policy
- **Status:** Accepted

## Context

`actions-ci` placed every repository shell contract after one checkout in one
`shell-tests` job. That minimized runner setup but made independent failures
invisible after the first failure and put the full suite on one critical path.

Five consecutive successful `main` runs supplied representative job and step
timestamps:

| Run | Queue | Job | Merge gate | Changelog/release | Platform/fleet |
| --- | ---: | ---: | ---: | ---: | ---: |
| 31200344417 | 92s | 210s | 102s | 83s | 19s |
| 31200067588 | 10s | 285s | 141s | 115s | 24s |
| 31199501925 | 3s | 259s | 126s | 105s | 25s |
| 31198811513 | 43s | 287s | 142s | 113s | 26s |
| 31198521533 | 4s | 257s | 126s | 102s | 25s |

The sequential median was 259 runner-seconds plus 10 seconds queued. The
measured groups are cohesive: merge-gate tests exercise one workflow trust
boundary, changelog/release tests share the canonical release contract, and
platform/fleet tests cover reusable actions and operational policy.

Three parallel legs project a 126-second median critical path. Allowing roughly
five seconds for repeated checkout/setup and a later aggregate job yields about
150 seconds at the observed median queue delay: approximately 44% lower wall
clock. Total runner time rises only by repeated setup, roughly 4–8%. Queue
pressure rises from one runnable job to at most three, bounded explicitly rather
than fanning out once per test.

The `core-checks-actions` ruleset requires the exact `shell-tests` context (ADR
0058). A matrix named `shell-tests` would rename every context and wedge the
ruleset.

## Decision

Run a three-value `shell-test-groups` matrix:

- `platform`
- `merge-gate`
- `changelog-release`

Set `fail-fast: false`, `max-parallel: 3`, and a 12-minute timeout. Each leg has
its own bounded checkout and invokes `scripts/actions-ci-group.sh` against the
checked-in `scripts/actions-ci-groups.tsv` manifest.

The runner launches every manifest command in a fresh Bash process, records
failures, and exits nonzero only after all commands in its group ran. This
preserves each former Actions step's shell isolation while exposing independent
failures within and across groups. Tests may share repository source but may not
depend on mutations from an earlier command; each matrix leg has an independent
checkout.

Keep an unmatrixed `shell-tests` job. It runs after the matrix with `always()`
and reports failure unless the complete matrix result is `success`. This keeps
the ruleset's required context unchanged; no ruleset mutation is part of this
decision.

## Consequences

- Independent failures are visible in one run instead of serial rediscovery.
- Median wall-clock should fall by about 44%; production telemetry must be used
  to validate the projection after merge.
- Runner setup and total runner-minutes rise slightly.
- Up to three fast-lane workers may queue concurrently, but fan-out cannot grow
  with the test count.
- Wiring assertions now inspect the canonical manifest rather than confusing
  physical YAML steps with execution reachability.

## Rollback

Restore the manifest commands as sequential steps under the unmatrixed
`shell-tests` job and remove `shell-test-groups`. The required context remains
`shell-tests` in either shape, so rollback requires no ruleset change.

## Amendment — 2026-10-09: four changelog-release cells ([#1733](https://github.com/Verjson/.github/issues/1733))

[ADR 0224](../0224-shard-changelog-release-actions-ci/README.md) replaces the single `changelog-release` matrix value with four cells. `platform` and `merge-gate` stay as decided here, and `shell-tests` remains the required context.
