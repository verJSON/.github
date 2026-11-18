# 0276 — Delivery ARB archive for release.repository historical source split

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-2229-release-repo-split-2026-11-18`; slate digest
  `2c0a28fe8d83f9944a303f51f84be675a71db494f822fc995e0a5a0d5eb8d208`).
- **Related:**
  [verJSON/.github#2229](https://github.com/verJSON/.github/pull/2229),
  [verJSON/.github#2228](https://github.com/verJSON/.github/issues/2228),
  [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0274](../0274-host-observation-repo-case-delivery-arb/README.md),
  [ADR 0275](../0275-runners-270-host-obs-adopter-arb/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

After [.github#2226](https://github.com/verJSON/.github/pull/2226) /
[runners#270](https://github.com/verJSON/verjson-git-runners/pull/270) cleared
host-observation casefold, canary dry-run failed in transport because the
controller still set both live API and signed-source repository fields from
`expectedRelease.sourceRepository`. Transport already required the rename
split. Gate R requires this archived chair record before autonomous merge of
#2229.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0276-release-repo-historical-source-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #2229 after Gate R; S-2 forbid `--admin`;
   S-3 regen runners adopter and re-dispatch canary dry-run.
3. **This ADR does not** change transport semantics beyond aligning the
   controller to the existing rename contract, authorize live mutation outside
   dry-run, or permit `--admin`.

## Consequences

Exact-head Gate R citations for #2229 may name
`verJSON/.github@<archive-sha>:docs/decisions/0276-release-repo-historical-source-delivery-arb/arb-record.md`.

[arb-record]: ./arb-record.md
