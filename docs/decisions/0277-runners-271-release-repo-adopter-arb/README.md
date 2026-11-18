# 0277 — Delivery ARB archive for runners#271 release.repository adopter regen

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-runners-271-2026-11-18`; slate digest
  `0175d97774ec5b36f14541d5db8158c211fe755b82ae85d2d048bd81d47f7c2c`).
- **Related:**
  [verJSON/verjson-git-runners#271](https://github.com/verJSON/verjson-git-runners/pull/271),
  [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197),
  [verJSON/.github#2229](https://github.com/verJSON/.github/pull/2229)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0276](../0276-release-repo-historical-source-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

After [.github#2229](https://github.com/verJSON/.github/pull/2229) landed the
controller split of live API vs historical signed repository identity at
`8150f373…`, [verjson-git-runners#271](https://github.com/verJSON/verjson-git-runners/pull/271)
regenerates the protected deployment adopter at that contract so canary dry-run
can clear `fetch_manifest`. Gate R requires this archived chair record before
autonomous merge of head `d07cd2157d6cb54e555ca578bcbbef1c3c822dfb`.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0277-runners-271-release-repo-adopter-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #271 after Gate R; S-2 forbid `--admin`;
   S-3 post-merge dry-run with `action=deploy`, `sha256:4f5bb96e…`,
   `github-canary`, `dry-run=true`.
3. **This ADR does not** change generator semantics beyond the adopter pin,
   authorize live mutation outside dry-run, or permit `--admin`.

## Consequences

Exact-head Gate R citations for runners#271 may name
`verJSON/.github@<archive-sha>:docs/decisions/0277-runners-271-release-repo-adopter-arb/arb-record.md`.

[arb-record]: ./arb-record.md
