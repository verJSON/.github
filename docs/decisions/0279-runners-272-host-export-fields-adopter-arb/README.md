# 0279 — Delivery ARB archive for runners#272 host-export release-fields adopter

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-runners-272-2026-11-18`; slate digest
  `633a8d0f3a89070a6f83d0bece616897986279699d128ecaf9b1edd6ecbb6e61`).
- **Related:**
  [verJSON/verjson-git-runners#272](https://github.com/verJSON/verjson-git-runners/pull/272),
  [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197),
  [verJSON/.github#2232](https://github.com/verJSON/.github/pull/2232)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0278](../0278-host-export-release-fields-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

After [.github#2232](https://github.com/verJSON/.github/pull/2232) landed the
host-export release key-set fix at `eeafd201…`,
[verjson-git-runners#272](https://github.com/verJSON/verjson-git-runners/pull/272)
regenerates the protected deployment adopter at that contract so canary dry-run
can clear transport `validate_request`. Gate R requires this archived chair
record before autonomous merge of head `51aa60bf2172d2190826884dd010d368eda99f28`.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0279-runners-272-host-export-fields-adopter-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #272 after Gate R; S-2 forbid `--admin`;
   S-3 post-merge dry-run with `action=deploy`, `sha256:4f5bb96e…`,
   `github-canary`, `dry-run=true`.
3. **This ADR does not** change generator semantics beyond the adopter pin,
   authorize live mutation outside dry-run, or permit `--admin`.

## Consequences

Exact-head Gate R citations for runners#272 may name
`verJSON/.github@<archive-sha>:docs/decisions/0279-runners-272-host-export-fields-adopter-arb/arb-record.md`.

[arb-record]: ./arb-record.md
