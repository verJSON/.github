# 0283 — Delivery ARB archive for runners#274 install-doctl adopter

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-runners-274-2026-11-18`; slate digest
  `b09467d9fa0985ec94937f2347357730593cbe6b27e9f737ba578c1358eae81a`).
- **Related:**
  [verJSON/verjson-git-runners#274](https://github.com/verJSON/verjson-git-runners/pull/274),
  [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197),
  [verJSON/.github#2241](https://github.com/verJSON/.github/pull/2241)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0282](../0282-install-doctl-host-export-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

After [.github#2241](https://github.com/verJSON/.github/pull/2241) landed the
doctl install for host-export at `43c1a479…`,
[verjson-git-runners#274](https://github.com/verJSON/verjson-git-runners/pull/274)
regenerates the protected deployment adopter at that contract so canary dry-run
can execute `doctl projects list` (ENOENT on run 44245632322).

## Decision

1. **Archive the chair record** at
   `docs/decisions/0283-runners-274-install-doctl-adopter-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #274 after Gate R; S-2 forbid `--admin`;
   S-3 post-merge dry-run.
3. **This ADR does not** authorize live mutation outside dry-run or permit
   `--admin`.

## Consequences

Exact-head Gate R citations for runners#274 may name
`verJSON/.github@<archive-sha>:docs/decisions/0283-runners-274-install-doctl-adopter-arb/arb-record.md`.

[arb-record]: ./arb-record.md
