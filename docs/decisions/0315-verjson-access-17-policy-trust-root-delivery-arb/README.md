# 0315 — Delivery ARB archive for verjson-access#17 policy trust root

- **Date:** 2026-11-26
- **Status:** Accepted. The Architecture Review Board's outcome is `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-access-pr17-e112845a7a-20261126-01`; slate digest `242077d65d7f60b585b1923f829fd3d206dc4a38f5bbd4556720ddde4b201bf5`.
- **Related:** verJSON/verjson-access#17, verJSON/verjson-access#7, verJSON/verjson-access#16, verJSON/verjson-access#13, verJSON/verjson-docs#49
- **Admission:** archive-only lane ([ADR 0309](../0309-standing-archive-only-admission-lane/README.md), [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md))
- **Category:** autonomous delivery approval archive (sensitive class: access control and IAM)
- **ARB:** [arb-record.md](./arb-record.md)

## Archived record

`arb-record.md` is the run-27 chair artifact, copied byte for byte. Its whole-file SHA-256 is `8948fb6e30fb776421147bae951f1c9a234c122292aed4caa9e69dab0aa801b6`.

That value is a consistency anchor written by the publisher, not provenance (ADR 0309). The provenance channel that predates this pull request is the chair's board-outcome comment on the source PR, verjson-access#17 comment 7203310967. The source repository is private. The comment states the same hash, run id and fence digest.

This README restates the board's outcome and records no new decision (ADR 0271 Decision 1).
- **Decision:** the board approved merging verjson-access#17 at exactly `e112845a7af184dd9faeb9b59b5af3d3adc0234f`, with `--match-head-commit` and never `--admin`.
- **Findings and follow-ups:** codex's findings were fixed in later heads where cheap. The record routes the remaining kind (b) items to verjson-access#16, #13, verjson-docs#49, new follow-up issues, or the source PR, and requires each to be recorded before #17's `hold` label is removed. They were recorded in verjson-access#18 and #16 (comment 7203394897); the two ADR 0003 wording items the record prefers fixed in #17 were noted on #16 instead, because the approval is bound to #17's exact head.
- **Retained dissent:** codex's final-round REVISE on CHAIN, USE, DOCS, TESTS, X1, X2 and M1 is retained for the owner, with no later ballot. In round 3, claude ruled codex's round-2 findings kind (b).

[arb-record]: ./arb-record.md
