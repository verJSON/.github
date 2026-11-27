# 0318 — Delivery ARB archive for verjson-access#23 OpenBao connector

- **Date:** 2026-11-27
- **Status:** Accepted. The Architecture Review Board's outcome is `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-access-pr23-f35f0de1de-20261127-01`; slate digest `e4d268892b04d6fc388f827f1e79b326d4743668ca4d6b4db31a4dfe4aaefe03`.
- **Related:** verJSON/verjson-access#23, verJSON/verjson-access#4, verJSON/verjson-access#22, verJSON/verjson-access#24, verJSON/verjson-access#25
- **Admission:** archive-only lane ([ADR 0309](../0309-standing-archive-only-admission-lane/README.md), [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md))
- **Category:** autonomous delivery approval archive (sensitive class: access control and secrets)
- **ARB:** [arb-record.md](./arb-record.md)

## Archived record

`arb-record.md` is the run-31 chair artifact, copied byte for byte. Its whole-file SHA-256 is `f68089486f8f8c820ea1199cfa0b7047331051eca32f5a5c0d4a9e087fa30787`.

That value is a consistency anchor written by the publisher, not provenance (ADR 0309). The provenance channel that predates this pull request is the chair's board-outcome comment on the source PR, verjson-access#23 comment 7219700203. The source repository is private. The comment states the same hash, run id and fence digest.

This README restates the board's outcome and records no new decision (ADR 0271 Decision 1).
- **Decision:** the board approved merging verjson-access#23 at exactly `f35f0de1ded0430d39f2d49a02690316b92bfbb5`, with `--match-head-commit` and never `--admin`.
- **History:** run 31 continued board run 30 on the same PR. Run 30 ended NOT APPROVED at an earlier head on one gap the required member found, which was fixed.
- **Constraints:** the deployment checks in verjson-access#25 before the connector is relied on for a production leave.
- **Follow-ups:** kind (b) items are tracked in verjson-access#24.
- **Dissent:** run 31's final round was unanimous. Run 30 retains one advisory dissent, which the required member adjudicated as kind (b). The record restates it for the owner.

[arb-record]: ./arb-record.md
