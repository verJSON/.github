# 0273 — Delivery ARB archive for runners#268 deployment cli-cloud 2.1.0 repin

- **Date:** 2026-11-17
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-runners-268-2026-11-17`; slate digest
  `246b38cc7425a47e79190cebaad2acc354d1d59c2658aa892eae0055fe1634d5`).
- **Related:**
  [verJSON/verjson-git-runners#268](https://github.com/verJSON/verjson-git-runners/pull/268),
  [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md)
  (Gate R archive locator surface)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

Protected runner deployment dry-run failed acquiring unpublished
`@verjson/cli-cloud@1.3.0`. PR
[verJSON/verjson-git-runners#268](https://github.com/verJSON/verjson-git-runners/pull/268)
repins the generated container-deployment adopter to
`Verjson/.github@a52ea59e1300693081af62128556d4063d2faf8a` (cli-cloud 2.1.0)
while retaining `merge-admission@dc300862…` after #267. Gate R (ADR 0271 /
#2184) requires an archived chair record before an exact-head
`arb-consensus:v1` citation and ADR 0202 independent-review receipt may
authorize autonomous merge of head
`abe35e27e5af748a9cdce723a86f149a7abb13a9`.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0273-runners-268-deployment-cli-2-1-delivery-arb/arb-record.md`
   with run id `arb-runners-268-2026-11-17` and slate digest
   `246b38cc7425a47e79190cebaad2acc354d1d59c2658aa892eae0055fe1634d5`.
2. **Adopted slate:** S-1 approve autonomous merge of #268 after Gate R
   markers on that exact head; S-2 forbid `--admin` / use only
   `assert-mergeable-head`; S-3 after merge re-dispatch the protected
   deployment canary dry-run with `--preview`.
3. **This ADR does not** change container-deployment generator semantics,
   merge-admission policy, or authorize `--admin` merge.

## Consequences

Exact-head Gate R citations for runners#268 may name
`verJSON/.github@<archive-sha>:docs/decisions/0273-runners-268-deployment-cli-2-1-delivery-arb/arb-record.md`.
Merge remains blocked until the citation and ADR 0202 receipt are posted
and `assert-mergeable-head` prints `true`.

[arb-record]: ./arb-record.md
