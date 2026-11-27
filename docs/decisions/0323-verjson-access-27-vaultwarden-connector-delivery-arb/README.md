# 0323 — Delivery ARB archive for verjson-access#27 Vaultwarden connector

- **Date:** 2026-11-27
- **Status:** Accepted. The Architecture Review Board's outcome is `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-access-pr27-07d80411c9-20261127-01`; slate digest `afec17ef3ab65ee5262404af07819d1d98499074a22a9103e5193059a9eed8cd`.
- **Related:** verJSON/verjson-access#27, verJSON/verjson-access#3, verJSON/verjson-access#26, verJSON/verjson-access#29, verJSON/verjson-access#30
- **Admission:** archive-only lane ([ADR 0309](../0309-standing-archive-only-admission-lane/README.md), [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md))
- **Category:** autonomous delivery approval archive (sensitive class: access control and credentials)
- **ARB:** [arb-record.md](./arb-record.md)

## Archived record

`arb-record.md` is the run-34 chair artifact, copied byte for byte. Its whole-file SHA-256 is `e7eff976b10dfb820f4f0ad7bf4f965f7e9734d779a4e8505630a915d9b4da95`.

That value is a consistency anchor written by the publisher, not provenance (ADR 0309). The provenance channel that predates this pull request is the chair's board-outcome comment on the source PR, verjson-access#27 comment 7222993523. The source repository is private. The comment states the same hash, run id and fence digest.

This README restates the board's outcome and records no new decision (ADR 0271 Decision 1).
- **Decision:** the board approved merging verjson-access#27 at exactly `07d80411c9b2d3b31edfb355acb594d2b56aa955`, with `--match-head-commit` and never `--admin`.
- **History:** run 34 continued board run 32 on the same PR. Run 32 ended NOT APPROVED at an earlier head on one documentation-versus-code defect, which was fixed.
- **Constraints:** the deployment checks in verjson-access#30 before production use.
- **Follow-ups:** kind (b) items are tracked in verjson-access#29 and #26.
- **Dissent:** run 34's final round was unanimous. Run 32 retains codex's dissent, ruled kind (b) by the required member; the record restates it for the owner.

[arb-record]: ./arb-record.md
