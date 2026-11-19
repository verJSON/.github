# ARB chair record — .github#2255 Renovate bump classifier

## Charter

- **Run id:** `arb-2255-claim-bump-classifier-2026-11-19`
- **PR:** [verJSON/.github#2255](https://github.com/verJSON/.github/pull/2255)
- **Issue:** [verJSON/.github#2210](https://github.com/verJSON/.github/issues/2210)
- **Head:** `460ba0775c7b266031bd2af818045ad66c9fc95f`
- **Question:** Approve autonomous merge of #2255, which fixes
  `classify_renovate_bump` (`scripts/ci-gate/agent_delivery_claim.py`) so
  unlabeled Renovate minor/patch titles of v2+ packages are no longer failed
  closed as majors? The trigger was #2222 (`@verjson/cli-cloud` 2.1.0 → 2.2.0),
  whose claim check (run 44204693663) logged `worker failed closed: major bump`.
  Bare majors and `N.0.0` targets stay major, `to v1` now fails closed, short
  commit digests are not majors, and labels still win.
- **Pins:** ADR 0271, ADR 0257, ADR 0202, ADR 0287; baseline
  `main@07c8b6d1019de935ea872d086f920d9db584d964`; organization context
  `verJSON/verjson-docs` config `57932dc678cb089a99c19e6eb3aba78329933eab`, manifest
  `5ae0c3cf480c9285bdae773f3d6f1c47a70c74b698f839f574d65e6a08031783` (grounded).
- **Rule:** unanimous; ledger schema v2 (unverified members may block, never
  approve alone); three required members; at most two rounds.
- **Status:** `APPROVED` (`validate_ledger.py --state`: `run_state_verified:
  true`, scope `full_slate`, delivery conditions clear).
- **Ledger slate digest:** `519bd23a5c0e54da7b3faee5adab60ed5429f9582541148a91b6f57d37c0471e` (revision 1)
- **Slate digest (SHA-256)** for Gate R:
  `966cbf1931b0ba9e960b51e57cf753ad8160edaf2c9e7faa5547a0c8cd08c655`
  over:

```canonical-slate-json
{"head":"460ba0775c7b266031bd2af818045ad66c9fc95f","issues":["verJSON/.github#2210","verJSON/.github#2255"],"items":["S-1","S-2","S-3"],"pr":"verJSON/.github#2255","run_id":"arb-2255-claim-bump-classifier-2026-11-19","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt, not
  personal CODEOWNERS.
- **Owner direction:** the owner delegated merge authority contingent on ARB
  consensus for this delivery class. Every ledger's
  `governance.ratification_status` remains `pending`; this record claims no
  separate human ratification.

## Adopted slate (revision 1)

- **S-1:** Merge #2255 at `460ba0775c7b266031bd2af818045ad66c9fc95f`: a
  Renovate title target is a major only when it is a bare major (`to v2`,
  `to 24`) or `N.0.0`; a trailing `(?!\w)` boundary stops a hex SHA's leading
  digits parsing as a version; an unprefixed, dotless run of seven or more
  digits is a short commit digest, not a major; label precedence is
  unchanged. The head stays fixed; this archive lands in a separate PR.
  Acceptance: before merge, every check on the head completed with no failure
  and `assert-mergeable-head` prints `true` once the receipt is posted; after
  merge, `main` `actions-ci` passes.
- **S-2:** No `--admin`; run `assert-mergeable-head` from a clean `origin/main`
  checkout immediately before any manual merge and pass `--match-head-commit`.
- **S-3:** After merge, trigger a Renovate rebase of #2222 so its claim job
  checks out the fixed classifier at a new `base.sha` (a rerun reuses the old
  one) and exercises the #2210 canary path; record the outcome on #2210.

## Evidence given to every seat

Exact-head check runs on `460ba077` (24 success, 6 skipped, `merge-admission`
in progress), and `assert-mergeable-head.sh verJSON/.github 2255` output (exit
6, Gate R only). The PR diff. Verification: the three claim suites pass; a
mutation table kills the trailing-boundary, digest-exemption, zero-tail,
`major >= 2`, and title-path mutants, with one accepted survivor (a
`to v1234567` target, a form Renovate never renders); two independent `code-reviewer` rounds
(round 1 found a word-keyed `digest` fail-open and a pin-digest false major,
both fixed at this head; round 2 found no new fail-open case).

## Ballots (round 1)

| Seat | S-1 | S-2 | S-3 | CLI | Model identity |
| --- | --- | --- | --- | --- | --- |
| A | ACCEPT | ACCEPT | ACCEPT | `claude` | claude-opus-5-5 (chair-verified from `modelUsage` in the claude CLI output, retained with SHA-256 `e654162addc2bbda811f23427babff51e6b9aa4b8f5cbd2f86c5803cf58e3190`) |
| B | ACCEPT | ACCEPT | ACCEPT | `codex` | unverified — requested gpt-6-luna; codex exec reports no served model |
| C | ACCEPT | ACCEPT | ACCEPT | `cursor-agent` | unverified — requested composer-2.5; JSON names no served model |

`approval_includes_unverified_votes`: seats B and C on S-1, S-2 and S-3 (each
candidate also carries seat A's verified ACCEPT).

Round 1 raw response digests:

- seat-a: `4959195a58eca8a8223f65395141ab41a629da85838775ad35eaffe4077439ef`
- seat-b: `7c3f3fbb4cd28fdd1074d19c6222cf158f33857f3c7e879f6406b24a8a68efa3`
- seat-c: `c97658df8d2f942352264e8b13ff4400982e24dba89e049bbbfec4d426317f3c`

## Dissent

None. Seat A independently exercised the regex against additional edge
titles (`to 24-alpine`, `to v3.0.0-beta.1`, `to v2.x`) and found the major
forms preserved, and `to 2025.1.0` correctly eligible.

## Integration constraints

1. **scope:** Gate R on #2255. **vulnerability:** markers on another head are
   not evidence for `460ba0775c7b266031bd2af818045ad66c9fc95f`. **remediation_track:** post the citation, then
   a fresh exact-head `independent-review:v1` marker after it, then
   `assert-mergeable-head`.
2. **scope:** title heuristic. **vulnerability:** it assumes Renovate's
   default title rendering; a repository that overrides `commitMessageExtra`
   so majors render with the full version would classify them `eligible`.
   Pre-existing gaps (grouped titles with no `to`, range targets, `!`
   breaking markers, 0.x minors) are unchanged. **remediation_track:** the
   #2210 canary on #2222 (S-3) is the first live evidence; fleet adopters'
   Renovate overrides are unverified.
