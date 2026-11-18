# 0274 — Delivery ARB archive for host observation repository case identity

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-host-obs-case-2026-11-18`; slate digest
  `008823d561df3f9f21fd9a976c2c757ce244959aaefc435d187b1c252e749af9`).
- **Related:**
  [verJSON/.github#2226](https://github.com/verJSON/.github/pull/2226),
  [verJSON/.github#2225](https://github.com/verJSON/.github/issues/2225),
  [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0213](../0213-package-type-surface-release-authorization/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

Protected runner dry-run
[44205371156](https://github.com/verJSON/verjson-git-runners/actions/runs/44205371156)
failed with `host observation repository identity is unavailable` after the
organization rename: Actions and the GitHub API report `verJSON/...` while
reviewed deployment config keeps `Verjson/...`. PR #2226 applies ADR 0213 ASCII
case-insensitive identity compares in the deployment controller and
credential-separated transport. Gate R requires this archived chair record
before autonomous merge.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0274-host-observation-repo-case-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #2226 after Gate R markers; S-2 forbid
   `--admin`; S-3 regenerate runners adopter and re-dispatch canary dry-run.
3. **This ADR does not** change deployment semantics beyond the identity
   compare, authorize live mutation outside dry-run, or permit `--admin`.

## Consequences

Exact-head Gate R citations for #2226 may name
`verJSON/.github@<archive-sha>:docs/decisions/0274-host-observation-repo-case-delivery-arb/arb-record.md`.

[arb-record]: ./arb-record.md
