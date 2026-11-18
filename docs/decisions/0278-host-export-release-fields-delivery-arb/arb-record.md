# ARB chair record — .github#2232 host-export release fields

## Charter

- **Run id:** `arb-2232-host-export-release-fields-2026-11-18`
- **PR:** [verJSON/.github#2232](https://github.com/verJSON/.github/pull/2232)
- **Issue:** [verJSON/.github#2231](https://github.com/verJSON/.github/issues/2231)
- **Head:** `60017fd6b984815d4af7d2b34ffbf359214268bf`
- **Question:** Approve autonomous merge of the host-export transport release
  key-set fix (omit `releaseVersion`; relay single-line TransportError) that
  unblocked canary dry-run after ADR 0276's historical-source split?
- **Pins:** ADR 0271, ADR 0276, ADR 0257, ADR 0202; head
  `60017fd6b984815d4af7d2b34ffbf359214268bf`
- **Rule:** Unanimous ACCEPT among three returned seats (Grok 4.7, Grok 4.6,
  Composer 2.5).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `94069856ce36053cfadcd6458c4474bf6284196d7add39a04c957f8148764cf3`
  over:

```canonical-slate-json
{"head":"60017fd6b984815d4af7d2b34ffbf359214268bf","issues":["verJSON/.github#2231","verJSON/.github#2232"],"items":["S-1","S-2","S-3"],"pr":"verJSON/.github#2232","run_id":"arb-2232-host-export-release-fields-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner ratification:** Owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 carry-forward session).

## Adopted slate

- **S-1:** Approve autonomous merge of #2232 after Gate R markers on exact head
  `60017fd6b984815d4af7d2b34ffbf359214268bf`.
- **S-2:** Do not use `--admin`; merge only via `assert-mergeable-head` when
  green.
- **S-3:** After merge, regenerate verjson-git-runners adopter at the merge tip,
  Gate R merge that PR, then dispatch dry-run with `action=deploy`,
  `manifest-identity=sha256:4f5bb96e1fe07f7b56cfe124206ed85c4e59b9715b3b4e18b3054d890dd1ad32`,
  `fleet-selector=github-canary`, `dry-run=true`.

## Ballots

| Seat | Overall | S-1 | S-2 | S-3 | Model |
| --- | --- | --- | --- | --- | --- |
| A | ACCEPT | ACCEPT | ACCEPT | ACCEPT | grok-4.7-high |
| B | ACCEPT | ACCEPT | ACCEPT | ACCEPT | cursor-grok-4.6-high-fast |
| C | ACCEPT | ACCEPT | ACCEPT | ACCEPT | composer-2.5 |

## Integration constraints

1. **scope:** Gate R on .github#2232. **vulnerability:** Stale markers on prior
   heads are not evidence for `60017fd6b984815d4af7d2b34ffbf359214268bf`. **remediation_track:** Post
   preceding `arb-consensus:v1` then exact-head independent-review before
   `assert-mergeable-head`.
2. **scope:** Autonomous merge. **vulnerability:** `--admin` bypasses ADR 0269.
   **remediation_track:** `assert-mergeable-head` only.
3. **scope:** Post-merge proof. **vulnerability:** Adopter still vendors prior
   controller bytes. **remediation_track:** Regen + dry-run after merge.

## Dissent

None.
