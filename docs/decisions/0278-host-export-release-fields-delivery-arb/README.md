# 0278 — Delivery ARB archive for .github#2232 host-export release fields

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-2232-host-export-release-fields-2026-11-18`; slate digest
  `94069856ce36053cfadcd6458c4474bf6284196d7add39a04c957f8148764cf3`).
- **Related:**
  [verJSON/.github#2232](https://github.com/verJSON/.github/pull/2232),
  [verJSON/.github#2231](https://github.com/verJSON/.github/issues/2231),
  [dry-run 44232618346](https://github.com/verJSON/verjson-git-runners/actions/runs/44232618346)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0276](../0276-release-repo-historical-source-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

After the rename/casefold and release.repository historical-source split
(ADR 0276 / #2229), canary dry-run still failed opaque
`canonical host evidence transport failed`. The host-export request kept
`releaseVersion` from `_release()`, which transport `validate_request`
rejects as `release fields differ` before attestation.
[.github#2232](https://github.com/verJSON/.github/pull/2232) emits the
transport-exact release key set and relays a single short TransportError line.
Gate R requires this archived chair record before autonomous merge of head
`60017fd6b984815d4af7d2b34ffbf359214268bf`.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0278-host-export-release-fields-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #2232 after Gate R; S-2 forbid `--admin`;
   S-3 post-merge adopter regen + dry-run with `action=deploy`, `sha256:4f5bb96e…`,
   `github-canary`, `dry-run=true`.
3. **This ADR does not** authorize live mutation outside dry-run or permit
   `--admin`.

## Consequences

Exact-head Gate R citations for .github#2232 may name
`verJSON/.github@<archive-sha>:docs/decisions/0278-host-export-release-fields-delivery-arb/arb-record.md`.

[arb-record]: ./arb-record.md
