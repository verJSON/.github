# 0224 — Shard the changelog-release actions-ci group

- **Date:** 2026-10-09
- **Status:** Accepted
- **Issue:** [verJSON/.github#1733](https://github.com/verJSON/.github/issues/1733)
- **Extends:** [ADR 0076](../0076-bounded-actions-ci-shell-test-groups/README.md)
- **Category:** CI policy

## Context

ADR 0076 split `actions-ci` into `platform`, `merge-gate`, and `changelog-release`. The changelog-release cell grew back into the critical path. On run [37874848459](https://github.com/verJSON/.github/actions/runs/37874848459) it occupied one general runner for 24m 46s, and `bash scripts/ci-gate/changelog-caller-contract.test.sh` accounted for 1208s of that. Sibling cells on the same run finished in 13m 20s (`platform`) and 9m 49s (`merge-gate`). The job timeout is already 30 minutes; raising it would only hide the cost.

The caller contract builds an adopter and runs the generated suite once per mutation, in order. Most mutations can affect only some of the five generated sections (`generated-set`, `workflow-callers`, `release-workflows`, `renderer`, `fixtures`).

## Decision

1. Replace the `changelog-release` matrix value with `changelog-release-1` through `changelog-release-4`. Keep `fail-fast: false`. Set `max-parallel` to 6 so those four cells can overlap `platform` and `merge-gate`. Leave `timeout-minutes` at 30.
2. Partition `changelog-caller-contract.test.sh` across those four cells with `CHANGELOG_CALLER_CONTRACT_SHARD`. Shard `all` remains the local full run. An unknown shard fails before any case.
3. Each mutation names the generated sections it can affect. An unmapped mutation still runs every section, and the caller contract then fails, so a new case cannot drop assertions by omission.
4. `shell-tests` stays the unmatrixed required context. No ruleset change is part of this decision.

## Consequences

One caller-contract case runs in exactly one cell, and that case executes only its named sections. Wall-clock comparison against the 24-minute cell belongs to the next `actions-ci` run of this change. Rollback is the single `changelog-release` group and an unsharded caller contract; `shell-tests` does not move.
