# ARB chair record — runners#273 host-export Node pin + stderr relay adopter regen

## Charter

- **Run id:** `arb-runners-273-2026-11-18`
- **PR:** [verJSON/verjson-git-runners#273](https://github.com/verJSON/verjson-git-runners/pull/273)
- **Issue:** [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197)
- **Head:** `3dd1f2cc60695d56f8bc372f3f18da76e294879f`
- **Question:** Approve autonomous merge of the protected container-deployment
  adopter regenerated at `Verjson/.github@03da2f159499203c4593a362ace629cfd3942445`
  (merged .github#2236 host-export Node 26.13.1 pin + sanitized CLI stderr
  relay), as S-3 follow-through of
  `arb-2236-host-export-node-cli-stderr-2026-11-18` / ADR 0280?
- **Pins:** ADR 0271, ADR 0280, ADR 0257, ADR 0202; hub contract
  `03da2f159499203c4593a362ace629cfd3942445`; runners head `3dd1f2cc60695d56f8bc372f3f18da76e294879f`
- **Rule:** Unanimous ACCEPT among three returned seats (Grok 4.7, Grok 4.6,
  Composer 2.5).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `647a579e4da60f04b5f293d7336ff2986159d4b8fe87b5f782b8d4dcaaac60b0`
  over:

```canonical-slate-json
{"head":"3dd1f2cc60695d56f8bc372f3f18da76e294879f","issues":["verJSON/verjson-git-runners#197","verJSON/verjson-git-runners#273"],"items":["S-1","S-2","S-3"],"pr":"verJSON/verjson-git-runners#273","run_id":"arb-runners-273-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner ratification:** Owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 carry-forward session).

## Adopted slate

- **S-1:** Approve autonomous merge of #273 after Gate R markers on exact head
  `3dd1f2cc60695d56f8bc372f3f18da76e294879f`.
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

1. **scope:** Gate R on runners#273. **vulnerability:** Stale markers on prior
   heads are not evidence for `3dd1f2cc60695d56f8bc372f3f18da76e294879f`. **remediation_track:** Post
   preceding `arb-consensus:v1` then exact-head independent-review before
   `assert-mergeable-head`.
2. **scope:** Autonomous merge. **vulnerability:** `--admin` bypasses ADR 0269.
   **remediation_track:** `assert-mergeable-head` only.
3. **scope:** Post-merge proof. **vulnerability:** Live dry-run still pending.
   **remediation_track:** Dispatch after merge.

## Dissent

None.
