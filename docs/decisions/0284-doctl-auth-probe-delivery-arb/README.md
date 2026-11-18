# 0284 — Delivery ARB archive for .github#2245 doctl auth probe

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-2245-doctl-auth-probe-2026-11-18`; slate digest
  `28130f1337c7c8331dfcaf5cebbdde265124ababd501764ed542ffbf2701f4bb`).
- **Related:**
  [verJSON/.github#2245](https://github.com/verJSON/.github/pull/2245),
  [verJSON/.github#2244](https://github.com/verJSON/.github/issues/2244),
  [dry-run 44248683810](https://github.com/verJSON/verjson-git-runners/actions/runs/44248683810)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0282](../0282-install-doctl-host-export-delivery-arb/README.md),
  [ADR 0198](../0198-confine-runner-host-evidence-credentials/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

After doctl install (#2241), dry-run 44248683810 failed `doctl projects list
--context default` with exit 1 while execa's shortMessage hid DigitalOcean
stderr. #2245 probes doctl under the transport isolated HOME before the CLI.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0284-doctl-auth-probe-delivery-arb/arb-record.md`.
2. **Adopted slate:** S-1 merge #2245 after Gate R; S-2 forbid `--admin`;
   S-3 adopter regen + dry-run.
3. **This ADR does not** re-provision secrets or authorize live mutation.

## Consequences

Gate R citations for .github#2245 may name
`verJSON/.github@<archive-sha>:docs/decisions/0284-doctl-auth-probe-delivery-arb/arb-record.md`.

[arb-record]: ./arb-record.md
