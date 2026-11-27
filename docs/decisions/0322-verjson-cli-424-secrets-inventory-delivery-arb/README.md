# 0322 — Delivery ARB archive for verjson-cli#424 Infisical inventory

- **Date:** 2026-11-27
- **Status:** Accepted. The Architecture Review Board's outcome is `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-cli-pr424-1e755b214d-20261127-01`; slate digest `ec3baf6e98914bbcdf09065c6af4ef9126d71cc302ab255937d9a3d8c2ecd0a5`.
- **Related:** verJSON/verjson-cli#424, verJSON/verjson-cli#417, verJSON/verjson-cli#425, verJSON/verjson-cli#423
- **Admission:** archive-only lane ([ADR 0309](../0309-standing-archive-only-admission-lane/README.md), [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md))
- **Category:** autonomous delivery approval archive (sensitive class: secrets handling)
- **ARB:** [arb-record.md](./arb-record.md)

## Archived record

`arb-record.md` is the run-33 chair artifact, copied byte for byte. Its whole-file SHA-256 is `daeaf9b97d50df0bf0a1009fc56cd5582fa8df3f9beb0ae9c7530a3df2bdd5d3`.

That value is a consistency anchor written by the publisher, not provenance (ADR 0309). The provenance channel that predates this pull request is the chair's board-outcome comment on the source PR, verjson-cli#424 comment 7222964334. The comment states the same hash, run id and fence digest.

This README restates the board's outcome and records no new decision (ADR 0271 Decision 1).
- **Decision:** the board approved merging verjson-cli#424 at exactly `1e755b214d34f13ba1f957fa63d0f81bae22f5c3`, with `--match-head-commit` and never `--admin`.
- **History:** a single board run of three rounds; claude, cursor-agent and agy accepted every candidate in every round.
- **Constraints:** a live run against the deployed Infisical before verjson-docs ADR 0020 open decision 3 is decided from the counts (verjson-cli#425).
- **Follow-ups:** kind (b) items are tracked in verjson-cli#425.
- **Dissent:** codex's final-round dissent, ruled kind (b) by the required member, is retained in the record for the owner.

[arb-record]: ./arb-record.md
