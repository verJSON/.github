# ARB chair record — verjson-vault#7 read gates

## Charter

- **Run id:** `arb-vault-pr7-read-gates-2026-11-19`
- **PR:** [verJSON/verjson-vault#7](https://github.com/verJSON/verjson-vault/pull/7)
- **Head:** `3995f0e0f8101ffea1af60355cd86ab6a894402f`
- **Question:** Do the three required fixes hold on this head, and does the
  reconvened board require any further fix before autonomous merge?
- **Pins:** the pull request diff of `3995f0e0f8101ffea1af60355cd86ab6a894402f`
  against `origin/main`; ADR 0271; ADR 0202; ADR 0257.
- **Rule:** a required fix is a concrete defect. The round closes only when
  every seated member reports none.
- **Status:** `APPROVED`
- **Slate digest (SHA-256)** for Gate R:
  `79d8069aa3ad6de073f638e2c97f4d03669a274b9a4e2bd06344e5578b15264e`
  over:

```canonical-slate-json
{"head":"3995f0e0f8101ffea1af60355cd86ab6a894402f","items":["S-1","S-2","S-3"],"pr":"verJSON/verjson-vault#7","run_id":"arb-vault-pr7-read-gates-2026-11-19","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus published through the ADR 0202
  receipt, not personal CODEOWNERS.
- **Standards grounding:** organization catalog hydration was not completed.
  This record makes no published-standards compliance claim.
- **Owner direction:** the owner told the authoring session to publish the
  Gate R citation for this head. This record claims no separate human
  ratification.

## Adopted slate

- **S-1:** At `3995f0e0f8101ffea1af60355cd86ab6a894402f`, `currentKeyVersions`
  returns secret names and referenced Google secret ids only to a sync reader
  this service issued; `valuesForUse` returns no plaintext and writes no read
  audit when that token is revoked or expired; `members.set` gives a hidden or
  inactive person the ordinary membership refusal and stores no member row.
- **S-2:** The reconvened board required no further fix on this head. Approval
  is for this head only.
- **S-3:** Do not use `--admin`. Merge only when `assert-mergeable-head.sh`
  exits 0 for this head, with `--match-head-commit`.

## Evidence given to every seat

The full diff of `3995f0e0f8101ffea1af60355cd86ab6a894402f` against
`origin/main`, read with the surrounding functions. Local `npm test` on that
commit: 208 passed, 68 skipped without a Postgres URL, lint clean. CI on the
head: 12 checks passed, none failed, at the time this record was written.

## Ballots

| Seat | Result | Identity |
| --- | --- | --- |
| Codex | `NO_BLOCKING_DEFECTS` for reviewed SHA `3995f0e0f8101ffea1af60355cd86ab6a894402f`; blocking list empty. CI and release Node `26.13.1`; no Node 24 job. | gpt-6-luna, read-only |
| Access and audit | `NO_REQUIRED_FIXES` | Grok |
| Push and crypto | `NO_REQUIRED_FIXES` | Grok |
| agy | `NO_REQUIRED_FIXES` | plan mode; advisory, not a source-verified dissent |

## Dissent

None. The earlier round's three proposals that were not adopted — a sealed-box
test key, a required GitHub `authorise` callback, and a host refusal when
`NODE_ENV` is unset — were not re-filed.

## Integration constraints

1. **scope:** Gate R on verjson-vault#7. **vulnerability:** a citation or
   receipt on another head is not evidence for
   `3995f0e0f8101ffea1af60355cd86ab6a894402f`. **remediation_track:** post the
   citation, then the exact-head `independent-review:v1` marker, then
   `assert-mergeable-head`.
2. **scope:** this archive. **vulnerability:** it records the reconvened
   review; it is not a `validate_ledger.py` run. **remediation_track:** a new
   head needs a new run.
