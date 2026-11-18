# ARB chair record — runners#270 host-observation casefold adopter regen

## Charter

- **Run id:** `arb-runners-270-2026-11-18`
- **PR:** [verJSON/verjson-git-runners#270](https://github.com/verJSON/verjson-git-runners/pull/270)
- **Issue:** [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197)
- **Head:** `ca7ba683fed72bcd2433ea6a6f09329e02f4dbca`
- **Question:** Approve autonomous merge of the protected container-deployment
  adopter regenerated at `Verjson/.github@a3a7fe75` (merged .github#2226
  host-observation / transport casefold), as S-3 follow-through of
  `arb-host-obs-case-2026-11-18` / ADR 0274?
- **Pins:** ADR 0271, ADR 0274, ADR 0269, ADR 0257, ADR 0202; hub contract
  `a3a7fe75`; runners head `ca7ba68`
- **Rule:** Unanimous ACCEPT among three returned seats after chair REVISE
  (duplicate NEXT + pin family) and Seat A S-3 REVISE (`action=deploy` on
  dispatch) were remediated (Grok 4.7, Grok 4.6, Composer 2.5).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `6daf850442bc5f1106e2d1872aad706af090737fe5eb9bd1f937ea02c6e6ec52`
  over:

```canonical-slate-json
{"head":"ca7ba683fed72bcd2433ea6a6f09329e02f4dbca","issues":["verJSON/verjson-git-runners#197","verJSON/verjson-git-runners#270"],"items":["S-1","S-2","S-3"],"pr":"verJSON/verjson-git-runners#270","run_id":"arb-runners-270-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner ratification:** Owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 carry-forward session).

## Adopted slate

- **S-1:** Approve autonomous merge of #270 after Gate R markers
  (`arb-consensus:v1` citation then ADR 0202 independent-review) on exact head
  `ca7ba683fed72bcd2433ea6a6f09329e02f4dbca`.
- **S-2:** Do not use `--admin`; merge only via `assert-mergeable-head` when
  green.
- **S-3:** After merge, dispatch the protected container-deployment dry-run with
  `action=deploy`,
  `manifest-identity=sha256:4f5bb96e1fe07f7b56cfe124206ed85c4e59b9715b3b4e18b3054d890dd1ad32`,
  `fleet-selector=github-canary`, `dry-run=true`.

## Ballots

| Seat | Overall | S-1 | S-2 | S-3 | Model |
| --- | --- | --- | --- | --- | --- |
| Chair (initial on de71393) | REVISE | REVISE | ACCEPT | ACCEPT | inherit |
| A (re-ballot ca7ba68) | REVISE | ACCEPT | ACCEPT | REVISE | grok-4.7-high |
| A (slate S-3 fix) | ACCEPT | ACCEPT | ACCEPT | ACCEPT | grok-4.7-high |
| B | ACCEPT | ACCEPT | ACCEPT | ACCEPT | cursor-grok-4.6-high-fast |
| C | ACCEPT | ACCEPT | ACCEPT | ACCEPT | composer-2.5 |

## Integration constraints

1. **scope:** Changelog NEXT identity. **vulnerability:** Duplicate `issue:197`
   blocked changelog-contract. **remediation_track:** Unique `id:` fragment on
   `ca7ba68`.
2. **scope:** Hub-caller pin family. **vulnerability:** AI-review producer pin
   `a3a7fe75` vs inventory `a52ea59e`. **remediation_track:**
   `tests/contract_pins.json` updated on `ca7ba68`.
3. **scope:** Post-merge dry-run dispatch. **vulnerability:** Required `action`
   input omitted from slate. **remediation_track:** S-3 includes `action=deploy`.
4. **scope:** Autonomous merge principal. **vulnerability:** `--admin` bypasses
   ADR 0269. **remediation_track:** `assert-mergeable-head` only.

## Dissent

None after final re-ballot.
