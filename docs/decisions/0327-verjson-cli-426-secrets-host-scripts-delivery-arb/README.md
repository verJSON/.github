# 0327 — Delivery ARB archive for verjson-cli#426 secrets-host change scripts

- **Date:** 2026-11-27
- **Status:** Accepted. The Architecture Review Board's outcome is `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-cli-pr426-2be3c2c6e1-20261127-01`; slate digest `38a79f7bc43ad625f015fa96418fe503f39bfa74adf4900d7397f11cd4294aaf`.
- **Related:** verJSON/verjson-cli#426, verJSON/verjson-cli#412, verJSON/verjson-cli#427
- **Admission:** archive-only lane ([ADR 0309](../0309-standing-archive-only-admission-lane/README.md), [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md))
- **Category:** autonomous delivery approval archive (sensitive class: secrets handling, destructive recovery, access revocations and ingress)
- **ARB:** [arb-record.md](./arb-record.md)

## Archived record

`arb-record.md` is the run-36 chair artifact, copied byte for byte. Its whole-file SHA-256 is `297044f4072cb27bc6dd4d31eece2b820911256844f0998b3be113aedfff9a38`.

That value is a consistency anchor written by the publisher, not provenance (ADR 0309). The provenance channel that predates this pull request is the chair's board-outcome comment on the source PR, verjson-cli#426 comment 7228090716, which states the same hash, run id and fence digest.

This README restates the board's outcome and records no new decision (ADR 0271 Decision 1).
- **Decision:** the board approved merging verjson-cli#426 at exactly `2be3c2c6e1391371ab693ad134950b8ceed0ee71`, with `--match-head-commit` and never `--admin`.
- **History:** round 1 at an earlier head was NOT APPROVED. Its findings were fixed and verified in round 2; the remaining advisory findings were fixed before round 3, except one ruled kind (b). In round 3, one advisory member's first response was malformed; it was recorded as a failed call and re-requested once.
- **Constraints:** codex's final-round items, and the first apply, rollback, recovery and break-glass on an isolated host supervised by the owner, are recorded in verjson-cli#427. The record names that issue as holding fixes to land before any release ships these scripts.
- **Follow-ups:** kind (b) items are tracked in verjson-cli#427.
- **Dissent:** codex's round-3 dissent on all eight candidates is retained for the owner.

[arb-record]: ./arb-record.md
