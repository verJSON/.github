# 0293 — Delivery ARB archive for the rebased .github#2261 head

- **Date:** 2026-11-20
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-2261-rebased-head-delivery-2026-11-20-c`; slate digest
  `51076f679a9002462befe114f4ecbfe9ac54fce120a22976650d9c4df0154c86`).
- **Related:**
  [verJSON/.github#2261](https://github.com/verJSON/.github/pull/2261),
  [verJSON/.github#2260](https://github.com/verJSON/.github/issues/2260),
  [verJSON/unrwly#321](https://github.com/verJSON/unrwly/issues/321)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0292](../0292-release-trust-exact-head-ci-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive
- **ARB:** [arb-record.md](./arb-record.md)

## Context

#2261's head moved once, to
`0df5657cffa21030c3655ed588f97f4c97f7d109`, so the index could list both
ADR 0291 and ADR 0292. ADR 0292's fence names the previous head. This record
names the rebased head.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0293-release-ci-trust-rebased-head-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 land this archive without moving #2261 again; S-2
   cite it and post a new exact-head receipt, using the ADR 0257
   shared-identity exception if the receipt author is the merge actor; S-3
   withhold the merge on `REVIEW_REQUIRED` or any ruleset-required approval
   that is not shown, then merge only with the pinned command.
3. **This ADR does not** authorize `--admin` or closing unrwly#321 from the
   squash message.

## Consequences

Gate R citations for .github#2261 at
`0df5657cffa21030c3655ed588f97f4c97f7d109` may name
`verJSON/.github@<archive-sha>:docs/decisions/0293-release-ci-trust-rebased-head-delivery-arb/arb-record.md`.
Any later change to that head invalidates this approval and needs a new run.

[arb-record]: ./arb-record.md
