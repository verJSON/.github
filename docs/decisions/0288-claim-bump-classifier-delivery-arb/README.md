# 0288 — Delivery ARB archive for .github#2255 Renovate bump classifier

- **Date:** 2026-11-19
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-2255-claim-bump-classifier-2026-11-19`; slate digest
  `966cbf1931b0ba9e960b51e57cf753ad8160edaf2c9e7faa5547a0c8cd08c655`).
- **Related:**
  [verJSON/.github#2255](https://github.com/verJSON/.github/pull/2255),
  [verJSON/.github#2210](https://github.com/verJSON/.github/issues/2210),
  [verJSON/.github#2222](https://github.com/verJSON/.github/pull/2222)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0287](../0287-next-fragment-consolidation-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive
- **ARB:** [arb-record.md](./arb-record.md)

## Context

The agent-delivery claim worker fails closed on major and breaking Renovate
bumps (#2209). Its title heuristic flagged any target whose major was 2 or
higher, so #2222 (`@verjson/cli-cloud` 2.1.0 → 2.2.0) failed its claim check
as a major and the #2210 canary never ran. #2255 narrows the heuristic to
Renovate's major forms.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0288-claim-bump-classifier-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #2255 at its fixed head after Gate R markers;
   S-2 forbid `--admin`; S-3 re-trigger #2222 through a Renovate rebase after
   merge.
3. **This ADR does not** change the fail-closed policy for majors or authorize
   live mutation.

## Consequences

Gate R citations for .github#2255 may name
`verJSON/.github@<archive-sha>:docs/decisions/0288-claim-bump-classifier-delivery-arb/arb-record.md`.
Any later change to #2255's head invalidates this approval and needs a new run.

[arb-record]: ./arb-record.md
