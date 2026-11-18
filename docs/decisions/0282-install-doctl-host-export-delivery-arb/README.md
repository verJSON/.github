# 0282 — Delivery ARB archive for .github#2241 install doctl host-export

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-2241-install-doctl-host-export-2026-11-18`; slate digest
  `b3a5829c51b1d9644c605f9f8ac79a5c399931692ccb6d328825a93ef0940bf4`).
- **Related:**
  [verJSON/.github#2241](https://github.com/verJSON/.github/pull/2241),
  [verJSON/.github#2240](https://github.com/verJSON/.github/issues/2240),
  [dry-run 44245632322](https://github.com/verJSON/verjson-git-runners/actions/runs/44245632322)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0280](../0280-host-export-node-cli-stderr-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

Dry-run 44245632322 cleared GitHub binding and Node 26.13.1, then failed host
export with `doctl` ENOENT on `doctl projects list` because
`container-deployment.yml` never installed the binary on ubuntu-24.04.
[.github#2241](https://github.com/verJSON/.github/pull/2241) installs pinned
`digitalocean/action-doctl` with `no_auth: true` (config.yaml still from
`RUNNER_HOST_EVIDENCE_DOCTL_CONFIG`).

## Decision

1. **Archive the chair record** at
   `docs/decisions/0282-install-doctl-host-export-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #2241 after Gate R; S-2 forbid `--admin`;
   S-3 adopter regen + dry-run.
3. **This ADR does not** authorize live mutation outside dry-run or permit
   `--admin`.

## Consequences

Exact-head Gate R citations for .github#2241 may name
`verJSON/.github@<archive-sha>:docs/decisions/0282-install-doctl-host-export-delivery-arb/arb-record.md`.

[arb-record]: ./arb-record.md
