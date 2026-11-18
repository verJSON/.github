# ARB chair record — runners#274 install-doctl adopter regen

## Charter

- **Run id:** `arb-runners-274-2026-11-18`
- **PR:** [verJSON/verjson-git-runners#274](https://github.com/verJSON/verjson-git-runners/pull/274)
- **Issue:** [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197)
- **Head:** `80477e97fe8454fbac905cc3f4ba216045518c3f`
- **Question:** Approve autonomous merge of the protected container-deployment
  adopter regenerated at `Verjson/.github@43c1a479f50b5cde83c1474b514d90e770770701`
  (merged .github#2241 install doctl), as S-3 follow-through of
  `arb-2241-install-doctl-host-export-2026-11-18` / ADR 0282?
- **Pins:** ADR 0271, ADR 0282, ADR 0257, ADR 0202; hub contract
  `43c1a479f50b5cde83c1474b514d90e770770701`; runners head `80477e97fe8454fbac905cc3f4ba216045518c3f`
- **Rule:** Unanimous ACCEPT among three returned seats (Grok 4.7, Grok 4.6,
  Composer 2.5).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `b09467d9fa0985ec94937f2347357730593cbe6b27e9f737ba578c1358eae81a`
  over:

```canonical-slate-json
{"head":"80477e97fe8454fbac905cc3f4ba216045518c3f","issues":["verJSON/verjson-git-runners#197","verJSON/verjson-git-runners#274"],"items":["S-1","S-2","S-3"],"pr":"verJSON/verjson-git-runners#274","run_id":"arb-runners-274-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner ratification:** Owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 carry-forward session).

## Adopted slate

- **S-1:** Approve autonomous merge of #274 after Gate R markers on exact head
  `80477e97fe8454fbac905cc3f4ba216045518c3f`.
- **S-2:** Do not use `--admin`; merge only via `assert-mergeable-head` when
  green.
- **S-3:** After merge, dispatch dry-run with `action=deploy`,
  `manifest-identity=sha256:4f5bb96e1fe07f7b56cfe124206ed85c4e59b9715b3b4e18b3054d890dd1ad32`,
  `fleet-selector=github-canary`, `dry-run=true`.

## Ballots

| Seat | Overall | S-1 | S-2 | S-3 | Model |
| --- | --- | --- | --- | --- | --- |
| A | ACCEPT | ACCEPT | ACCEPT | ACCEPT | grok-4.7-high |
| B | ACCEPT | ACCEPT | ACCEPT | ACCEPT | cursor-grok-4.6-high-fast |
| C | ACCEPT | ACCEPT | ACCEPT | ACCEPT | composer-2.5 |

## Integration constraints

1. **scope:** Gate R on runners#274. **vulnerability:** Stale markers on prior
   heads are not evidence for `80477e97fe8454fbac905cc3f4ba216045518c3f`. **remediation_track:** Post
   preceding `arb-consensus:v1` then exact-head independent-review before
   `assert-mergeable-head`.
2. **scope:** Autonomous merge. **vulnerability:** `--admin` bypasses ADR 0269.
   **remediation_track:** `assert-mergeable-head` only.
3. **scope:** Post-merge proof. **vulnerability:** Live dry-run still pending.
   **remediation_track:** Dispatch after merge.

## Dissent

None.
