# 0325 — Delivery ARB archive for verjson-access#28 server mode slice 1

- **Date:** 2026-11-27
- **Status:** Accepted. The Architecture Review Board's outcome is `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-access-pr28-39d93581bf-20261127-01`; slate digest `05eb8b5f50b9f76551902d7cceec5bf2677823d2fadac08a120969b19c26b8bd`.
- **Related:** verJSON/verjson-access#28, verJSON/verjson-access#6, verJSON/verjson-access#31, verJSON/verjson-access#32, verJSON/verjson-docs#51
- **Admission:** archive-only lane ([ADR 0309](../0309-standing-archive-only-admission-lane/README.md), [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md))
- **Category:** autonomous delivery approval archive (sensitive class: authentication, sessions and audit)
- **ARB:** [arb-record.md](./arb-record.md)

## Archived record

`arb-record.md` is the run-35 chair artifact, copied byte for byte. Its whole-file SHA-256 is `e1dc38b16557cc4d7387dfb1b0437ae223bc967a0ec24eade147dab760faf955`.

That value is a consistency anchor written by the publisher, not provenance (ADR 0309). The provenance channel that predates this pull request is the chair's board-outcome comment on the source PR, verjson-access#28 comment 7224520433. The source repository is private. The comment states the same hash, run id and fence digest.

This README restates the board's outcome and records no new decision (ADR 0271 Decision 1).
- **Decision:** the board approved merging verjson-access#28 at exactly `39d93581bf77908c741f88cf264b8188e464098e`, with `--match-head-commit` and never `--admin`.
- **History:** round 1 at an earlier head was NOT APPROVED. Its findings were fixed and verified in round 2, and the remaining advisory findings were fixed before round 3.
- **Constraints:** the source PR's decision record, verjson-access ADR 0006, is Proposed. The owner ratifies it, and the prerequisites in verjson-access#32 are settled, before any web action is built. A real-browser sign-in against the first deployment is an integration constraint.
- **Follow-ups:** kind (b) items are tracked in verjson-access#31.
- **Dissent:** codex's round-3 dissent on AUTHN, DOCS, TESTS and M1 is retained for the owner. The required member ruled the unbuilt parts kind (b).

[arb-record]: ./arb-record.md
