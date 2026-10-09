# 0224 — Shard the changelog-release actions-ci group

- **Date:** 2026-10-09
- **Status:** Accepted
- **Issue:** [verJSON/.github#1733](https://github.com/verJSON/.github/issues/1733)
- **Extends:** [ADR 0076](../0076-bounded-actions-ci-shell-test-groups/README.md)
- **Category:** CI policy

## Context

ADR 0076 split `actions-ci` into `platform`, `merge-gate`, and `changelog-release`. The changelog-release cell grew back into the critical path. On run [37874848459](https://github.com/verJSON/.github/actions/runs/37874848459) it occupied one general runner for 24m 46s, and `bash scripts/ci-gate/changelog-caller-contract.test.sh` accounted for 1208s of that. Sibling cells on the same run finished in 13m 20s (`platform`) and 9m 49s (`merge-gate`). The job timeout is already 30 minutes; raising it would only hide the cost.

The caller contract builds an adopter and runs the generated suite once per mutation, in order. Splitting that suite by generated section is not a safe cut. On [run 37970631888](https://github.com/verJSON/.github/actions/runs/37970631888) every changelog-release cell failed the same way: a mutation mapped to `generated-set` was accepted because the assertion that rejects it lives in a later section. One wrong map entry therefore looks like four independent test failures.

## Decision

1. Replace the `changelog-release` matrix value with `changelog-release-1` through `changelog-release-4`. Keep `fail-fast: false`. Set `max-parallel` to 6 so those four cells can overlap `platform` and `merge-gate`. Leave `timeout-minutes` at 30.
2. Partition `changelog-caller-contract.test.sh` by case with `CHANGELOG_CALLER_CONTRACT_SHARD`. Shard `all` remains the local full run. An unknown shard fails before any case. A case runs in exactly one cell, and that run executes the whole generated contract.
3. The generated suite has no section switch. A later change may not skip `generated-set`, `workflow-callers`, `release-workflows`, `renderer`, or `fixtures` for a single mutation.
4. `shell-tests` stays the unmatrixed required context. No ruleset change is part of this decision.

## Consequences

One caller-contract case runs in exactly one cell and still executes every generated assertion. Wall-clock comparison against the 24-minute cell belongs to the next `actions-ci` run of this change. Rollback is the single `changelog-release` group and an unsharded caller contract; `shell-tests` does not move.
