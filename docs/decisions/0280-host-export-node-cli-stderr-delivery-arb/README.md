# 0280 — Delivery ARB archive for .github#2236 host-export Node + CLI stderr

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-2236-host-export-node-cli-stderr-2026-11-18`; slate digest
  `9a69604367f22f34eb663db9c29f31070bd3b1871328858bacc924bf9773faa6`).
- **Related:**
  [verJSON/.github#2236](https://github.com/verJSON/.github/pull/2236),
  [verJSON/.github#2235](https://github.com/verJSON/.github/issues/2235),
  [dry-run 44236715751](https://github.com/verJSON/verjson-git-runners/actions/runs/44236715751)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0278](../0278-host-export-release-fields-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

After GitHub binding fixes (ADR 0278), canary dry-run reached
`verjson-cloud runner-host-evidence` and failed opaque while the reusable
workflow still pinned Node 26.12.1 against cli-cloud's `>=26.13.1` floor.
[.github#2236](https://github.com/verJSON/.github/pull/2236) pins 26.13.1 and
relays one sanitized CLI/adapter stderr line (with host-export secret scan).

## Decision

1. **Archive the chair record** at
   `docs/decisions/0280-host-export-node-cli-stderr-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #2236 after Gate R; S-2 forbid `--admin`;
   S-3 post-merge adopter regen + dry-run.
3. **This ADR does not** authorize live mutation outside dry-run or permit
   `--admin`.

## Consequences

Exact-head Gate R citations for .github#2236 may name
`verJSON/.github@<archive-sha>:docs/decisions/0280-host-export-node-cli-stderr-delivery-arb/arb-record.md`.

[arb-record]: ./arb-record.md
