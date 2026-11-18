# 0286 — Delivery ARB archive for .github#2251 host-export diagnostic relay allowlist

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-2251-host-export-relay-allowlist-2026-11-18`; slate digest
  `86736a84ec9ee6b90e06c080c1302556b668fdf6085bd8e0e73e86b91a5aec0e`).
- **Related:**
  [verJSON/.github#2251](https://github.com/verJSON/.github/pull/2251),
  [verJSON/.github#2250](https://github.com/verJSON/.github/issues/2250),
  [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197),
  [dry-run 44253156883](https://github.com/verJSON/verjson-git-runners/actions/runs/44253156883)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0198](../0198-confine-runner-host-evidence-credentials/README.md),
  [ADR 0280](../0280-host-export-node-cli-stderr-delivery-arb/README.md),
  [ADR 0284](../0284-doctl-auth-probe-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive (sensitive class: secrets
  handling in public logs)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

Dry-run 44253156883 reported only `canonical host evidence transport failed`
after the doctl auth probe (#2245). #2251 replaces the one-line,
denylist-filtered relay with an allowlist of diagnostic forms, recorded as the
2026-11-18 amendment to ADR 0198. The decision took six ARB runs, and three
escalated to the owner (runs 2, 3 and 5), who chose the allowlist design and
authorized fresh runs. Each dissent was fixed on the branch, rejected by the
chair with a stated reason, or kept as a stated trade-off; the
[run table](./arb-record.md#run-history-and-dissent) gives each outcome.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0286-host-export-relay-allowlist-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #2251 at
   `b2251048c4dc9d16f88b4295e80cd155dcf6336f` after Gate R markers, with the
   two accepted limits stated there; S-2 forbid `--admin`; S-3 adopter
   regeneration and dry-run handed to the verjson-git-runners owner.
3. **This ADR does not** re-provision secrets, change the relay policy beyond
   ADR 0198's amendment, or authorize live mutation.

## Consequences

Gate R citations for .github#2251 may name
`verJSON/.github@<archive-sha>:docs/decisions/0286-host-export-relay-allowlist-delivery-arb/arb-record.md`.
Any later change to #2251's head invalidates this approval and needs a new run.

[arb-record]: ./arb-record.md
