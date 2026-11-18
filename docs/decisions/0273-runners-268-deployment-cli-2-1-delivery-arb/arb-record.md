# ARB chair record — runners#268 deployment cli-cloud 2.1.0 delivery

## Charter

- **Run id:** `arb-runners-268-2026-11-17`
- **PR:** [verJSON/verjson-git-runners#268](https://github.com/verJSON/verjson-git-runners/pull/268)
- **Issue:** [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197)
- **Head:** `abe35e27e5af748a9cdce723a86f149a7abb13a9`
- **Question:** Approve autonomous merge of the protected container-deployment
  adopter repin to `Verjson/.github@a52ea59e` (cli-cloud 2.1.0), retaining
  `merge-admission@dc300862`, under ADR 0271 Gate R?
- **Pins:** ADR 0271, ADR 0269, ADR 0257; hub contract `a52ea59e`; runners head
  `abe35e27`
- **Rule:** Unanimous ACCEPT among three returned seats (Grok 4.7, Grok 4.6,
  Composer 2.5).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `246b38cc7425a47e79190cebaad2acc354d1d59c2658aa892eae0055fe1634d5`
  over:

```canonical-slate-json
{"head":"abe35e27e5af748a9cdce723a86f149a7abb13a9","issues":["verJSON/verjson-git-runners#197","verJSON/verjson-git-runners#268"],"items":["S-1","S-2","S-3"],"pr":"verJSON/verjson-git-runners#268","run_id":"arb-runners-268-2026-11-17","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner ratification:** Owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-17 session).

## Adopted slate

- **S-1:** Approve autonomous merge of #268 after Gate R markers
  (`arb-consensus:v1` citation then ADR 0202 independent-review) on exact head
  `abe35e27e5af748a9cdce723a86f149a7abb13a9`.
- **S-2:** Do not use `--admin`; merge only via `assert-mergeable-head` when
  green.
- **S-3:** After merge, re-dispatch the protected runner deployment canary
  dry-run with `--preview` / `github-canary` under the new contract.

## Ballots

| Seat | Overall | S-1 | S-2 | S-3 | Model |
| --- | --- | --- | --- | --- | --- |
| A | ACCEPT | ACCEPT | ACCEPT | ACCEPT | grok-4.7-high |
| B | ACCEPT | ACCEPT | ACCEPT | ACCEPT | cursor-grok-4.6-high-fast |
| C | ACCEPT | ACCEPT | ACCEPT | ACCEPT | composer-2.5 |

## Integration constraints

1. **scope:** Gate R on runners#268. **vulnerability:** Stale ADR 0202 marker
   on prior head `cf29db41…` is not evidence for `abe35e27…`.
   **remediation_track:** Post preceding `arb-consensus:v1` citation then fresh
   exact-head independent-review marker before `assert-mergeable-head`.
2. **scope:** Autonomous merge principal. **vulnerability:** `--admin` would
   bypass ADR 0269. **remediation_track:** `assert-mergeable-head` only.
3. **scope:** Post-merge verification. **vulnerability:** CI does not exercise
   npm package acquisition on the canary fleet.
   **remediation_track:** Dry-run dispatch with `--preview` after merge.

## Dissent

None.
