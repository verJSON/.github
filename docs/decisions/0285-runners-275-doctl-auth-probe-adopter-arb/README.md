# 0285 — Delivery ARB archive for runners#275 doctl auth probe adopter

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-runners-275-2026-11-18`; slate digest
  `81a9e20b55bc464172aa15d12edbde59687c3df826af856648e2e6a036eb4081`).
- **Related:**
  [verJSON/verjson-git-runners#275](https://github.com/verJSON/verjson-git-runners/pull/275),
  [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197),
  [verJSON/.github#2245](https://github.com/verJSON/.github/pull/2245)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0284](../0284-doctl-auth-probe-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

After [.github#2245](https://github.com/verJSON/.github/pull/2245) landed the
doctl auth probe at `76e61575…`,
[verjson-git-runners#275](https://github.com/verJSON/verjson-git-runners/pull/275)
regenerates the protected deployment adopter so canary dry-run vendors that
transport.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0285-runners-275-doctl-auth-probe-adopter-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #275 after Gate R; S-2 forbid `--admin`;
   S-3 post-merge dry-run.
3. **This ADR does not** re-provision secrets or authorize live mutation.

## Consequences

Gate R citations for runners#275 may name
`verJSON/.github@<archive-sha>:docs/decisions/0285-runners-275-doctl-auth-probe-adopter-arb/arb-record.md`.

[arb-record]: ./arb-record.md
