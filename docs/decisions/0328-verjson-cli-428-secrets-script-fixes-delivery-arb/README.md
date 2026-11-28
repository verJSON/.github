# 0328 — Delivery ARB archive for verjson-cli#428 secrets-host script pre-release fixes

- **Date:** 2026-11-27
- **Status:** Accepted. The Architecture Review Board's outcome is `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-cli-pr428-31422c5219-20261127-01`; slate digest `cfe5cb39f8eb3350f10a8d6a4971b9f62e940318a9a2607cb439ff68d6c293b8`.
- **Related:** verJSON/verjson-cli#428, verJSON/verjson-cli#427, verJSON/verjson-cli#412, [ADR 0327](../0327-verjson-cli-426-secrets-host-scripts-delivery-arb/README.md)
- **Admission:** archive-only lane ([ADR 0309](../0309-standing-archive-only-admission-lane/README.md), [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md))
- **Category:** autonomous delivery approval archive (sensitive class: secrets handling, destructive recovery, locks, signals and ingress)
- **ARB:** [arb-record.md](./arb-record.md)

## Archived record

`arb-record.md` is the run-37 chair artifact, copied byte for byte. Its whole-file SHA-256 is `02a58050680f7abbad17ab0b736d3752d2a2982bb59dbfaa2456524d4a7cbae4`.

That value is a consistency anchor written by the publisher, not provenance (ADR 0309). The provenance channel that predates this pull request is the chair's board-outcome comment on the source PR, verjson-cli#428 comment 7232332202, which states the same hash, run id and fence digest.

This README restates the board's outcome and records no new decision (ADR 0271 Decision 1).
- **Decision:** the board approved merging verjson-cli#428 at exactly `31422c5219886388610e3d019f6428eca7d973a3`, with `--match-head-commit` and never `--admin`.
- **History:** round 1 at an earlier head was NOT APPROVED. Its findings were fixed and verified in round 2 at the approved head. Round 3 ran at the same head, with no commit in between, and adjudicated the remaining advisory findings as kind (b) or no defect. Every call was recorded on the first attempt.
- **Constraints:** every kind (b) item, codex's final-round dissent, and the first apply, rollback, recovery and break-glass on an isolated host supervised by the owner are recorded in verjson-cli#427, which gates any release that ships these scripts.
- **Follow-ups:** kind (b) items are tracked in verjson-cli#427.
- **Dissent:** codex's round-3 dissent on REVIEW, REDIS, DOCS, TESTS and M1 is retained for the owner.

[arb-record]: ./arb-record.md
