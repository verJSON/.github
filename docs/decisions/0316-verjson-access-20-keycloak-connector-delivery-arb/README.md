# 0316 — Delivery ARB archive for verjson-access#20 Keycloak connector

- **Date:** 2026-11-26
- **Status:** Accepted. The Architecture Review Board's outcome is `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-access-pr20-221b7c2b85-20261126-01`; slate digest `0e24dba406fa752a3f958fef3f20d06e4d6a7067a48e8744b53b996f50233c1a`.
- **Related:** verJSON/verjson-access#20, verJSON/verjson-access#2, verJSON/verjson-access#12, verJSON/verjson-access#14, verJSON/verjson-access#19
- **Admission:** archive-only lane ([ADR 0309](../0309-standing-archive-only-admission-lane/README.md), [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md))
- **Category:** autonomous delivery approval archive (sensitive class: access control, credentials and IAM)
- **ARB:** [arb-record.md](./arb-record.md)

## Archived record

`arb-record.md` is the run-29 chair artifact, copied byte for byte. Its whole-file SHA-256 is `e8aee2e3ccac7590e0593d8d6c7a2bddd82426d7e72b401c27312babf253b0ab`.

That value is a consistency anchor written by the publisher, not provenance (ADR 0309). The provenance channel that predates this pull request is the chair's board-outcome comment on the source PR, verjson-access#20 comment 7204952217. The source repository is private. The comment states the same hash, run id and fence digest.

This README restates the board's outcome and records no new decision (ADR 0271 Decision 1).
- **Decision:** the board approved merging verjson-access#20 at exactly `221b7c2b859762c4a6c0e5eeb26e0517e07a1bda`, with `--match-head-commit` and never `--admin`.
- **History:** run 29 continued board run 28 on the same PR. Run 28 ended NOT APPROVED at an earlier head on one documentation claim, which was fixed.
- **Constraints:** the Keycloak 26 live-realm checks listed in the connector's README.
- **Follow-ups:** kind (b) items are tracked in verjson-access#14 and #19.
- **Dissent:** the final round was unanimous; no dissent is retained.

[arb-record]: ./arb-record.md
