# 0292 — Delivery ARB archive for .github#2261 release CI trust

- **Date:** 2026-11-20
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-2261-release-trust-exact-head-ci-2026-11-20-c`; slate digest
  `a353a85b799268f93c4b29f039b48a5a6fd7869947846830d59c654bed50d0c9`).
- **Related:**
  [verJSON/.github#2261](https://github.com/verJSON/.github/pull/2261),
  [verJSON/.github#2260](https://github.com/verJSON/.github/issues/2260),
  [verJSON/unrwly#321](https://github.com/verJSON/unrwly/issues/321)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0288](../0288-claim-bump-classifier-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive
- **ARB:** [arb-record.md](./arb-record.md)

## Context

#2261 implements ADR 0291 so an opt-in release verify job may trust named
required CI contexts on the exact dispatch SHA. The head stays
`c5350e7527ca2cedbf94d92f82517ddf4b2ad890`. Two earlier runs on this question
ended without approval; the owner authorized this run with those corrections
already in the opening slate.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0292-release-trust-exact-head-ci-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 edit the #2261 body and refresh evidence without
   moving the head; S-2 merge only with the pinned `gh pr merge` command after
   `assert-mergeable-head` prints true; S-3 land this archive separately, then
   cite, then receipt, and stop if the archive leaves #2261 unable to merge
   without a new commit.
3. **This ADR does not** change ADR 0291's permission ceiling or authorize
   `--admin`.

## Consequences

Gate R citations for .github#2261 may name
`verJSON/.github@<archive-sha>:docs/decisions/0292-release-trust-exact-head-ci-delivery-arb/arb-record.md`.
Any later change to #2261's head invalidates this approval and needs a new run.
An archive PR adds its fragment under its own `id:` identity, never issue 2260
or unrwly issue 321.

[arb-record]: ./arb-record.md
