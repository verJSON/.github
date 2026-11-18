# 0281 — Delivery ARB archive for runners#273 host-export Node adopter

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-runners-273-2026-11-18`; slate digest
  `647a579e4da60f04b5f293d7336ff2986159d4b8fe87b5f782b8d4dcaaac60b0`).
- **Related:**
  [verJSON/verjson-git-runners#273](https://github.com/verJSON/verjson-git-runners/pull/273),
  [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197),
  [verJSON/.github#2236](https://github.com/verJSON/.github/pull/2236)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0280](../0280-host-export-node-cli-stderr-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

After [.github#2236](https://github.com/verJSON/.github/pull/2236) landed the
Node 26.13.1 pin and sanitized host-export CLI stderr relay at `03da2f15…`,
[verjson-git-runners#273](https://github.com/verJSON/verjson-git-runners/pull/273)
regenerates the protected deployment adopter at that contract so canary dry-run
can surface DO/SSH/CLI diagnostics past GitHub binding. Gate R requires this
archived chair record before autonomous merge of head `3dd1f2cc60695d56f8bc372f3f18da76e294879f`.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0281-runners-273-host-export-node-adopter-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #273 after Gate R; S-2 forbid `--admin`;
   S-3 post-merge dry-run with `action=deploy`, `sha256:4f5bb96e…`,
   `github-canary`, `dry-run=true`.
3. **This ADR does not** change generator semantics beyond the adopter pin,
   authorize live mutation outside dry-run, or permit `--admin`.

## Consequences

Exact-head Gate R citations for runners#273 may name
`verJSON/.github@<archive-sha>:docs/decisions/0281-runners-273-host-export-node-adopter-arb/arb-record.md`.

[arb-record]: ./arb-record.md
