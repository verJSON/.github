# 0312 — Delivery ARB archive for verjson-cli#416 access dispatch

- **Date:** 2026-11-26
- **Status:** Accepted. The Architecture Review Board's outcome is `APPROVED_WITH_CONSTRAINTS` in the [ARB chair record][arb-record]; run `arb-verjson-cli-pr416-95fd0e11ae-20261126-01`; slate digest `50a0605e5033b4f800058c09bef4592db0cae3e995d0675c625180720a57b611`.
- **Related:** [verJSON/verjson-cli#416](https://github.com/verJSON/verjson-cli/pull/416), [verJSON/verjson-cli#413](https://github.com/verJSON/verjson-cli/issues/413), [verJSON/verjson-cli#422](https://github.com/verJSON/verjson-cli/issues/422)
- **Admission:** archive-only lane ([ADR 0309](../0309-standing-archive-only-admission-lane/README.md), [ADR 0310](../0310-verjson-dotgithub-2287-archive-lane-admission-arb/README.md))
- **Category:** autonomous delivery approval archive
- **ARB:** [arb-record.md](./arb-record.md)

## Archived record

`arb-record.md` is the run-26 chair artifact, copied byte for byte. Its whole-file SHA-256 is `b9de12d6d5708993a23f50398f65f45e666e46c4e7a21d9f18b0bd815035c450`.

That value is a consistency anchor written by the publisher, not provenance (ADR 0309). The provenance channel that predates this pull request is the chair's board-outcome comment on the source PR, [verjson-cli#416 comment 7201465510](https://github.com/verJSON/verjson-cli/pull/416#issuecomment-7201465510), which states the same hash, run id and fence digest.

This README restates the board's outcome and records no new decision (ADR 0271 Decision 1).
- **Decision:** the board approved merging verjson-cli#416 at exactly `95fd0e11aeea9cdfc1d0b747efaf7f4dc2c189e9`, with `--match-head-commit` and never `--admin`.
- **Follow-ups:** verjson-cli#422 carries the kind (b) items.
- **Retained dissent:** one advisory member, codex, kept its dissent. It concerns the "not installed" message shown for an installed package whose manifest lacks its bin. claude ruled it kind (b), because the behavior is the base's shared path and identical for `identity`.

[arb-record]: ./arb-record.md
