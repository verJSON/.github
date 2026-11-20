# ARB chair record — .github#2261 rebased head

## Charter

- **Run id:** `arb-2261-rebased-head-delivery-2026-11-20-c`
- **PR:** [verJSON/.github#2261](https://github.com/verJSON/.github/pull/2261)
- **Head:** `0df5657cffa21030c3655ed588f97f4c97f7d109`
- **Question:** Approve autonomous merge of #2261 at that fixed head?
- **Prior runs, closed:** the runs that approved
  `c5350e7527ca2cedbf94d92f82517ddf4b2ad890` do not approve this head. ADR 0292
  remains the archive of that earlier head.
- **Rule:** unanimous; unverified members may block, never approve alone;
  three members; one round.
- **Status:** `APPROVED` (round 1 unanimous ACCEPT; no dissent). This record
  does not claim a `validate_ledger.py` run.
- **Slate digest (SHA-256)** for Gate R:
  `51076f679a9002462befe114f4ecbfe9ac54fce120a22976650d9c4df0154c86`
  over:

```canonical-slate-json
{"head":"0df5657cffa21030c3655ed588f97f4c97f7d109","issues":["verJSON/.github#2260","verJSON/.github#2261","verJSON/unrwly#321"],"items":["S-1","S-2","S-3"],"pr":"verJSON/.github#2261","revision":1,"run_id":"arb-2261-rebased-head-delivery-2026-11-20-c","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus plus an ADR 0202 receipt.
  Personal CODEOWNERS are not that authority. A platform ruleset that requires
  a human, code-owner, or last-push approval is read immediately before merge
  and is not replaced by this receipt.
- **Shared identity:** the receipt author and the merge actor may be the same
  account. That is ADR 0257's time-boxed shared-identity exception until
  #1964, not a second GitHub user. The chair record states it here.

## Adopted slate (revision 1)

- **S-1:** Land this archive on main. The fence names head
  `0df5657cffa21030c3655ed588f97f4c97f7d109` and this run id. Do not move
  #2261's head.
- **S-2:** Post the arb-consensus citation, then a new
  `independent-review:v1` receipt, on that same head. The citation review id
  is lower.
- **S-3:** Immediately before merge: refresh live head SHA, reviewDecision,
  required checks, and applicable rulesets. Withhold the merge whenever
  GitHub reports reviewDecision REVIEW_REQUIRED, OR whenever a
  ruleset-required approval (human, code-owner, or last-push) is not shown on
  this head — including if the merge actor could bypass the ruleset. The ADR
  0202 receipt and arb-consensus citation are the autonomous quality gate
  under ADR 0271/0257; they are NOT a substitute for an approval the platform
  ruleset requires. Whether CODEOWNERS/human approval is required must be
  read from the refreshed ruleset, not assumed. Then assert-mergeable-head
  must print true; then
  `gh pr merge 2261 --repo Verjson/.github --squash --match-head-commit 0df5657cffa21030c3655ed588f97f4c97f7d109`
  (no `--admin`). Do not close unrwly#321.

## Ballots (round 1)

| Seat | S-1 | S-2 | S-3 | CLI | Model identity |
| --- | --- | --- | --- | --- | --- |
| A | ACCEPT | ACCEPT | ACCEPT | `claude` | claude-opus-5-5 (chair-verified from `modelUsage`; raw SHA-256 `14a31885b859d5594b96bdeac46c5a2a63ecf8a9c632744a791eb14f4ff7a86c`) |
| B | ACCEPT | ACCEPT | ACCEPT | `codex` | unverified — requested gpt-6.2-sol; the ballot file names no served model |
| C | ACCEPT | ACCEPT | ACCEPT | `cursor-agent` | unverified — requested composer-2.5; JSON names no served model |

`approval_includes_unverified_votes`: seats B and C on S-1, S-2, and S-3
(each candidate also carries seat A's verified ACCEPT).

Round 1 raw response digests:

- seat-a: `14a31885b859d5594b96bdeac46c5a2a63ecf8a9c632744a791eb14f4ff7a86c`
- seat-b: `0b388062fac3071dd7b8dd1a02155fcd66270737ef416a78596da1d95615b025`
- seat-c: `f4e00693c515f0fddc7fccf47d52959e349d59f8b43b8ae3c56fae7a52533a38`

## Dissent

None.

## Integration constraints

1. **scope:** Gate R on this head. **vulnerability:** the ADR 0292 fence names
   `c5350e7527ca2cedbf94d92f82517ddf4b2ad890`. **remediation_track:** cite this
   record, then a new receipt on `0df5657cffa21030c3655ed588f97f4c97f7d109`.
2. **scope:** platform review rules. **vulnerability:** a ruleset-required
   approval is not satisfied by the receipt. **remediation_track:** refresh
   `reviewDecision` and the ruleset immediately before merge and withhold when
   S-3 says to withhold.
