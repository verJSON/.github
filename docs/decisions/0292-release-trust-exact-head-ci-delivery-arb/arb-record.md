# ARB chair record — .github#2261 release verification trusts exact-head CI

## Charter

- **Run id:** `arb-2261-release-trust-exact-head-ci-2026-11-20-c`
- **PR:** [verJSON/.github#2261](https://github.com/verJSON/.github/pull/2261)
- **Issues:** [verJSON/.github#2260](https://github.com/verJSON/.github/issues/2260),
  [verJSON/unrwly#321](https://github.com/verJSON/unrwly/issues/321)
- **Head:** `c5350e7527ca2cedbf94d92f82517ddf4b2ad890`
- **Question:** Approve autonomous merge of #2261 at that fixed head, which
  implements ADR 0291 so an opt-in release verify job may trust named required
  CI contexts on the exact dispatch SHA?
- **Pins:** ADR 0271, ADR 0202, ADR 0291 (this head); baseline
  `main@b89082f8717929f293a6bbb59444f5f5d6c03745` at charter.
- **Prior runs, closed and not continued:**
  `arb-2261-release-trust-exact-head-ci-2026-11-20` (two rounds; round 2 Seat B
  REVISE on S-1 and S-2) and
  `arb-2261-release-trust-exact-head-ci-2026-11-20-b` (one round; Seat A REVISE
  on S-2, Seat C REVISE on S-3). The owner authorized this run. Its opening
  slate already contains those corrections. One round.
- **Rule:** unanimous; unverified members may block, never approve alone;
  three members; one round.
- **Status:** `APPROVED` (round 1 unanimous ACCEPT; no dissent). This record
  does not claim a `validate_ledger.py` run.
- **Slate digest (SHA-256)** for Gate R:
  `a353a85b799268f93c4b29f039b48a5a6fd7869947846830d59c654bed50d0c9`
  over:

```canonical-slate-json
{"head":"c5350e7527ca2cedbf94d92f82517ddf4b2ad890","issues":["verJSON/.github#2260","verJSON/.github#2261","verJSON/unrwly#321"],"items":["S-1","S-2","S-3"],"pr":"verJSON/.github#2261","revision":1,"run_id":"arb-2261-release-trust-exact-head-ci-2026-11-20-c","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt, not
  personal CODEOWNERS.
- **Owner direction:** the owner authorized this run and the opening slate
  (2026-11-20). `governance.ratification_status` is not claimed as a separate
  human ratification beyond that direction.

## Adopted slate (revision 1)

- **S-1:** Edit the #2261 body so it states this head includes
  `--require-ci-context` wiring and contains no closing keyword for
  verJSON/unrwly#321. The body edit must not move the head. Immediately before
  merge, refresh the review decision, required checks, and applicable rulesets,
  and stop if that evidence is missing, stale, or unreadable. Also verify the
  edited body and the commits that would be squashed contain no closing keyword
  for unrwly#321.
- **S-2:** Merge only with
  `gh pr merge 2261 --repo Verjson/.github --squash --match-head-commit c5350e7527ca2cedbf94d92f82517ddf4b2ad890`
  and no `--admin`, only after
  `bash scripts/assert-mergeable-head.sh Verjson/.github 2261 c5350e7527ca2cedbf94d92f82517ddf4b2ad890`
  prints true from a clean `origin/main` checkout. The squash message must not
  close unrwly#321.
- **S-3:** Land this archive on main through a separate PR that does not move
  #2261's head. Post the arb-consensus citation as a COMMENTED review by an
  admin or maintain user, then a new `independent-review:v1` receipt on the
  same head, with a lower citation review id. After that receipt, run the S-1
  refresh again. If landing this archive leaves #2261 behind main in a way
  that would require moving the head, stop and report. Only then run
  `assert-mergeable-head` and execute S-2.

After S-2, comment on unrwly#321 and .github#2260, close #2260, and leave
#321 open. That follow-through is owner direction, not a fourth candidate.

## Ballots (round 1)

| Seat | S-1 | S-2 | S-3 | CLI | Model identity |
| --- | --- | --- | --- | --- | --- |
| A | ACCEPT | ACCEPT | ACCEPT | `claude` | claude-opus-5-5 (chair-verified from `modelUsage` in the claude CLI output, retained with SHA-256 `72bfe01d7cbc6dafc3ab751fd7ebb9302ab0fadac3af74e9687ead49513ad2c6`) |
| B | ACCEPT | ACCEPT | ACCEPT | `codex` | unverified — requested gpt-6.2-sol; the ballot file names no served model |
| C | ACCEPT | ACCEPT | ACCEPT | `cursor-agent` | unverified — requested composer-2.5; JSON names no served model |

`approval_includes_unverified_votes`: seats B and C on S-1, S-2, and S-3 (each
candidate also carries seat A's verified ACCEPT).

Round 1 raw response digests:

- seat-a: `72bfe01d7cbc6dafc3ab751fd7ebb9302ab0fadac3af74e9687ead49513ad2c6`
- seat-b: `52da724fa0ffcd708a4f492cf0b9aad8e4375a1fb23db367be424c31d63e0621`
- seat-c: `7cc4c4ae9898d2f80f59a0636a9f085041bfb53298a2fa9e0805fa345df824b5`

## Dissent

None. Seat A noted, without a revision, that a conflict in the generated
`docs/decisions/README.md` index is a stop: do not rebase or regenerate on
#2261's head. Seat A also noted the assertion must exit 0 and print `true`.

## Integration constraints

1. **scope:** Gate R on #2261. **vulnerability:** markers on another head are
   not evidence for `c5350e7527ca2cedbf94d92f82517ddf4b2ad890`. **remediation_track:** post the citation, then
   a fresh exact-head `independent-review:v1` marker after it, then
   `assert-mergeable-head`.
2. **scope:** index overlap with #2261. **vulnerability:** this archive and
   #2261 both insert a row at the top of `docs/decisions/README.md`. **remediation_track:** if the merge cannot complete without a new commit on the head, stop and report.
