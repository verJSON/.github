# ARB chair record — .github#2241 install doctl for host-export

## Charter

- **Run id:** `arb-2241-install-doctl-host-export-2026-11-18`
- **PR:** [verJSON/.github#2241](https://github.com/verJSON/.github/pull/2241)
- **Issue:** [verJSON/.github#2240](https://github.com/verJSON/.github/issues/2240)
- **Head:** `71e2881d0386f69f981b7966fc66c557604f93a7`
- **Question:** Approve autonomous merge of installing pinned
  `digitalocean/action-doctl` (doctl `2.6.0`, `no_auth: true`) on
  container-deployment `dry-run` and `deploy` so `runner-host-evidence`
  can find `doctl` after dry-run 44245632322 failed with ENOENT on
  `doctl projects list`?
- **Pins:** ADR 0271, ADR 0280, ADR 0257, ADR 0202; head `71e2881d0386f69f981b7966fc66c557604f93a7`
- **Rule:** Unanimous ACCEPT among three returned seats (Grok 4.7, Grok 4.6,
  Composer 2.5).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `b3a5829c51b1d9644c605f9f8ac79a5c399931692ccb6d328825a93ef0940bf4`
  over:

```canonical-slate-json
{"head":"71e2881d0386f69f981b7966fc66c557604f93a7","issues":["verJSON/.github#2240","verJSON/.github#2241"],"items":["S-1","S-2","S-3"],"pr":"verJSON/.github#2241","run_id":"arb-2241-install-doctl-host-export-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner ratification:** Owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 carry-forward session).

## Adopted slate

- **S-1:** Approve autonomous merge of #2241 after Gate R markers on exact head
  `71e2881d0386f69f981b7966fc66c557604f93a7`.
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

1. **scope:** Gate R on .github#2241. **vulnerability:** Stale markers on prior
   heads (`562fd8cd`) are not evidence for `71e2881d0386f69f981b7966fc66c557604f93a7`. **remediation_track:** Post
   preceding `arb-consensus:v1` then exact-head independent-review before
   `assert-mergeable-head`.
2. **scope:** Autonomous merge. **vulnerability:** `--admin` bypasses ADR 0269.
   **remediation_track:** `assert-mergeable-head` only.
3. **scope:** Post-merge proof. **vulnerability:** Adopter still vendors prior
   workflow bytes without doctl. **remediation_track:** Regen + dry-run after merge.

## Amendment

Head advanced from `562fd8cd` to `71e2881d0386f69f981b7966fc66c557604f93a7` after Seat B REVISE: fleet-mechanics
contract grep matched Install doctl. Remediation awk-skips only that pinned step
block; seats re-balloted ACCEPT.

## Dissent

None.
