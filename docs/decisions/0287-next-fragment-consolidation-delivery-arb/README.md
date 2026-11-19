# 0287 — Delivery ARB archive for .github#2253 NEXT fragment consolidation

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-2253-next-fragment-consolidation-2026-11-18`; slate digest
  `740eccad3a34ba8b87a6b69b66a0cafcf295fef6df2353f6fb9771b063ee561f`).
- **Related:**
  [verJSON/.github#2253](https://github.com/verJSON/.github/pull/2253),
  [verJSON/.github#2250](https://github.com/verJSON/.github/issues/2250),
  [actions-ci run 44420721126](https://github.com/verJSON/.github/actions/runs/44420721126)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0286](../0286-host-export-relay-allowlist-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive
- **ARB:** [arb-record.md](./arb-record.md)

## Context

#2251 and its ADR 0286 archive (#2252) each added a `NEXT/` fragment with
identity `issue:2250`. Each validated alone; together on `main` they failed
the changelog contract. #2253 consolidates them as `NEXT/README.md` directs.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0287-next-fragment-consolidation-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #2253 at its fixed head after Gate R markers;
   S-2 forbid `--admin`.
3. **This ADR does not** change the changelog contract or authorize live
   mutation.

## Consequences

Gate R citations for .github#2253 may name
`verJSON/.github@<archive-sha>:docs/decisions/0287-next-fragment-consolidation-delivery-arb/arb-record.md`.
Any later change to #2253's head invalidates this approval and needs a new run.
An archive PR adds its fragment under its own `id:` identity, never the
delivered change's issue number.

[arb-record]: ./arb-record.md
