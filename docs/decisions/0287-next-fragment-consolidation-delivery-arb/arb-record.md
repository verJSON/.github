# ARB chair record — .github#2253 NEXT fragment consolidation

## Charter

- **Run id:** `arb-2253-next-fragment-consolidation-2026-11-18`
- **PR:** [verJSON/.github#2253](https://github.com/verJSON/.github/pull/2253)
- **Issue:** [verJSON/.github#2250](https://github.com/verJSON/.github/issues/2250)
- **Head:** `85865a2d4a2671c22750e9857364fe58de71a5de`
- **Question:** Approve autonomous merge of #2253, which restores a green
  `main` by consolidating two `NEXT/` fragments that both declared identity
  `issue:2250` (main `actions-ci` run 44420721126 at `bf9d207c` failed
  `docs-contracts`, `shell-test-groups (platform)` and `shell-tests` with
  `changelog: duplicate identity issue:2250`)?
- **Pins:** ADR 0271, ADR 0257, ADR 0202, ADR 0286; baseline
  `main@bf9d207c83211685bab2cd3e87eb6f2420d3808b`; organization context
  `verJSON/verjson-docs` config `57932dc678cb089a99c19e6eb3aba78329933eab`, manifest
  `b1b7c897f5208ccc31dbd8b562e0ed0d8169788158281b107c58661bd4b425fd`.
- **Rule:** unanimous; ledger schema v2 (unverified members may block, never
  approve alone); three required members; at most two rounds.
- **Status:** `APPROVED` (`validate_ledger.py --state`: `run_state_verified:
  true`, scope `full_slate`, delivery conditions clear).
- **Ledger slate digest:** `f5f0b0dd3718997896a73e985f71794d562525b6234dbc45c2f53bef8881b095` (revision 2)
- **Slate digest (SHA-256)** for Gate R:
  `740eccad3a34ba8b87a6b69b66a0cafcf295fef6df2353f6fb9771b063ee561f`
  over:

```canonical-slate-json
{"head":"85865a2d4a2671c22750e9857364fe58de71a5de","issues":["verJSON/.github#2250","verJSON/.github#2253"],"items":["S-1","S-2"],"pr":"verJSON/.github#2253","run_id":"arb-2253-next-fragment-consolidation-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner direction:** the owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 session). Every ledger's
  `governance.ratification_status` remains `pending`; this record claims no
  separate human ratification.

## Adopted slate (revision 2)

- **S-1:** Merge #2253 at `85865a2d4a2671c22750e9857364fe58de71a5de`: delete
  `NEXT/2026-11-18-issue-2250-adr-0286-relay-allowlist-arb.md` and fold its
  ADR 0286 pointer into the surviving #2250 fragment, as `NEXT/README.md`
  directs. The head stays fixed; this archive lands in a separate PR.
  Acceptance: before merge, every check on the head completed with no failure
  and `assert-mergeable-head` prints `true` once the receipt is posted; after
  merge, `main` `actions-ci` passes.
- **S-2:** No `--admin`; run `assert-mergeable-head` from a clean `origin/main`
  checkout immediately before merging and pass `--match-head-commit`.

## Ballots (round 1 / round 2)

| Seat | S-1 | S-2 | CLI | Model identity |
| --- | --- | --- | --- | --- |
| A | ACCEPT / ACCEPT | ACCEPT / ACCEPT | `claude` | claude-opus-5-5 (chair-verified from `modelUsage` in the claude CLI output, retained with SHA-256 `263573fa32b6fb14b1a7b3e1920a0033741f9caeb9ccf76f6d04e4741ee64c25`) |
| B | REVISE / ACCEPT | REVISE / ACCEPT | `codex` | unverified — requested gpt-6-luna; codex exec reports no served model |
| C | ACCEPT / ACCEPT | ACCEPT / ACCEPT | `cursor-agent` | unverified — requested composer-2.5; JSON names no served model |

`approval_includes_unverified_votes`: seats B and C on S-1 and S-2 (each
candidate also carries seat A's verified ACCEPT).

Round 2 raw response digests:

- seat-a: `a06376c681386e68507f5b97a885bb9a1dc15ba7255da2746a4b33409f1ea070`
- seat-b: `6eeb05fb8e2fbb4e2cd02c8d87817a2802aa2def64b62320edfc0b62c2008436`
- seat-c: `1db2031077a5960bb220393d350d41466813ecfbe4d5ea9bf78c974a1f3cef59`

## Dissent

Round 1: seat B voted REVISE on S-1 and S-2, asking for fresh exact-head
evidence (checks, review decision, a guard run) before authorizing. The chair
adopted it in revision 2 (adopted by
`ARB chair (.github PM session, claude-opus-5-5)`): acceptance became pre-merge and
exact-head, and the round-2 prompt carried the unfiltered check runs and the
guard's output (Gate R only, exit 6). Round 2 had no dissent.

## Integration constraints

1. **scope:** Gate R on #2253. **vulnerability:** markers on another head are
   not evidence for `85865a2d4a2671c22750e9857364fe58de71a5de`. **remediation_track:** post the citation, then
   the exact-head `independent-review:v1` marker, then `assert-mergeable-head`.
2. **scope:** `main` recovery. **vulnerability:** the heavy shell-test tier is
   skipped for a `NEXT/`-only change, so `hub-changelog-validate.sh` ran only
   locally before merge. **remediation_track:** confirm `main` `actions-ci`
   passes on the merge commit.
