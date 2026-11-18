# 0275 — Delivery ARB archive for runners#270 host-observation adopter regen

- **Date:** 2026-11-18
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-runners-270-2026-11-18`; slate digest
  `6daf850442bc5f1106e2d1872aad706af090737fe5eb9bd1f937ea02c6e6ec52`).
- **Related:**
  [verJSON/verjson-git-runners#270](https://github.com/verJSON/verjson-git-runners/pull/270),
  [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197),
  [verJSON/.github#2226](https://github.com/verJSON/.github/pull/2226)
- **Extends:**
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md),
  [ADR 0274](../0274-host-observation-repo-case-delivery-arb/README.md)
- **Category:** autonomous delivery approval archive (sensitive class)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

After [.github#2226](https://github.com/verJSON/.github/pull/2226) landed ADR
0213 case-insensitive host-observation / transport repository identity at
`a3a7fe75…`, [verjson-git-runners#270](https://github.com/verJSON/verjson-git-runners/pull/270)
regenerates the protected deployment adopter at that contract so the failing
canary dry-run
[44205371156](https://github.com/verJSON/verjson-git-runners/actions/runs/44205371156)
can be re-dispatched. Gate R requires this archived chair record before an
exact-head `arb-consensus:v1` citation and ADR 0202 independent-review receipt
may authorize autonomous merge of head
`ca7ba683fed72bcd2433ea6a6f09329e02f4dbca`.

## Decision

1. **Archive the chair record** at
   `docs/decisions/0275-runners-270-host-obs-adopter-arb/arb-record.md`
   with run id `arb-runners-270-2026-11-18` and slate digest
   `6daf850442bc5f1106e2d1872aad706af090737fe5eb9bd1f937ea02c6e6ec52`.
2. **Adopted slate:** S-1 approve autonomous merge of #270 after Gate R
   markers on that exact head; S-2 forbid `--admin` / use only
   `assert-mergeable-head`; S-3 after merge dispatch dry-run with
   `action=deploy`, manifest `sha256:4f5bb96e…`, fleet `github-canary`,
   `dry-run=true`.
3. **This ADR does not** change container-deployment generator semantics
   beyond the adopter pin, authorize live mutation outside dry-run, or permit
   `--admin` merge.

## Consequences

Exact-head Gate R citations for runners#270 may name
`verJSON/.github@<archive-sha>:docs/decisions/0275-runners-270-host-obs-adopter-arb/arb-record.md`.
Merge remains blocked until the citation and ADR 0202 receipt are posted
and `assert-mergeable-head` prints `true`.

[arb-record]: ./arb-record.md
