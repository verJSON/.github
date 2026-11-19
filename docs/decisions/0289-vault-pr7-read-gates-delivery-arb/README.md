# 0289 — Delivery ARB archive for verjson-vault#7 read gates

- **Date:** 2026-11-19
- **Status:** Accepted — chair record of the reconvened review board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-vault-pr7-read-gates-2026-11-19`; slate digest
  `79d8069aa3ad6de073f638e2c97f4d03669a274b9a4e2bd06344e5578b15264e`).
- **Related:**
  [verJSON/verjson-vault#7](https://github.com/verJSON/verjson-vault/pull/7)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md)
- **Category:** autonomous delivery approval archive
- **ARB:** [arb-record.md](./arb-record.md)

## Context

verjson-vault#7 fixes three defects the review board required after the vault
core had already merged: key names were listed with no grant, a revoked token
could still be read, and a hidden project admin was named as such. The board
reconvened on `3995f0e` and required no further fix.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0289-vault-pr7-read-gates-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 the three required outcomes hold at the fixed head;
   S-2 no further required fix was found; S-3 forbid `--admin`.
3. **This ADR does not** change vault codec exports or authorize a merge of
   any other head.

## Consequences

Gate R citations for verjson-vault#7 may name
`verJSON/.github@<archive-sha>:docs/decisions/0289-vault-pr7-read-gates-delivery-arb/arb-record.md`.
Any later change to #7's head invalidates this approval and needs a new run.

[arb-record]: ./arb-record.md
