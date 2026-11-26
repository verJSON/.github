# 0313 — Delivery ARB archive for verjson-access#9 workforce access core

- **Date:** 2026-11-26
- **Status:** Accepted. The Architecture Review Board's outcome is `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-access-pr9-cd0e0a58ef-20261126-01`; slate digest `f1bf11c1e2eba3fdb8d46d75916a7b56823d97405a4271411de214c4d4abf0ec`.
- **Related:** verJSON/verjson-access#9, verJSON/verjson-access#1, verJSON/verjson-access#7, verJSON/verjson-access#13, verJSON/verjson-access#14, verJSON/verjson-access#15
- **Admission:** archive-only lane ([ADR 0309](../0309-standing-archive-only-admission-lane/README.md), [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md))
- **Category:** autonomous delivery approval archive (sensitive class: access control and IAM)
- **ARB:** [arb-record.md](./arb-record.md)

## Archived record

`arb-record.md` is the run-25 chair artifact, copied byte for byte. Its whole-file SHA-256 is `174b39975d1b6b640ec32c7ef89d003c94f8e57610b92549e7459c4c311bca75`.

That value is a consistency anchor written by the publisher, not provenance (ADR 0309). The provenance channel that predates this pull request is the chair's board-outcome comment on the source PR, verjson-access#9 comment 7202090406. The source repository is private. The comment states the same hash, run id and fence digest.

This README restates the board's outcome and records no new decision (ADR 0271 Decision 1).
- **Decision:** the board approved merging verjson-access#9 at exactly `cd0e0a58ef02316cbd6e5dceb6f9b2253ddcb1d9`, with `--match-head-commit` and never `--admin`.
- **Kind (a) defects:** rounds 1 and 2 found kind (a) defects, which were fixed in later heads and re-verified.
- **Follow-ups:** verjson-access#13, #14 and #15 carry the kind (b) items. Production use stays blocked on verjson-access#7.
- **Retained dissent:** one advisory member, codex, kept its final-round dissent. claude ruled each of its items kind (b).

[arb-record]: ./arb-record.md
