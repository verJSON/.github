# ARB chair record — .github#2236 host-export Node pin + CLI stderr relay

## Charter

- **Run id:** `arb-2236-host-export-node-cli-stderr-2026-11-18`
- **PR:** [verJSON/.github#2236](https://github.com/verJSON/.github/pull/2236)
- **Issue:** [verJSON/.github#2235](https://github.com/verJSON/.github/issues/2235)
- **Head:** `5ce5173b1188407d63ed748eddb038b4c2401d5b`
- **Question:** Approve autonomous merge of Node 26.13.1 pin for cli-cloud
  host export plus sanitized CLI/adapter stderr relay (after GitHub binding
  fixes landed and dry-run reached `runner-host-evidence`)?
- **Pins:** ADR 0271, ADR 0278, ADR 0257, ADR 0202; head `5ce5173b1188407d63ed748eddb038b4c2401d5b`
- **Rule:** Unanimous ACCEPT among three returned seats (Grok 4.7, Grok 4.6,
  Composer 2.5).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `9a69604367f22f34eb663db9c29f31070bd3b1871328858bacc924bf9773faa6`
  over:

```canonical-slate-json
{"head":"5ce5173b1188407d63ed748eddb038b4c2401d5b","issues":["verJSON/.github#2235","verJSON/.github#2236"],"items":["S-1","S-2","S-3"],"pr":"verJSON/.github#2236","run_id":"arb-2236-host-export-node-cli-stderr-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner ratification:** Owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 carry-forward session).

## Adopted slate

- **S-1:** Approve autonomous merge of #2236 after Gate R markers on exact head
  `5ce5173b1188407d63ed748eddb038b4c2401d5b`.
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

1. **scope:** Gate R on .github#2236. **vulnerability:** Stale markers on prior
   heads are not evidence for `5ce5173b1188407d63ed748eddb038b4c2401d5b`. **remediation_track:** Post
   preceding `arb-consensus:v1` then exact-head independent-review before
   `assert-mergeable-head`.
2. **scope:** Autonomous merge. **vulnerability:** `--admin` bypasses ADR 0269.
   **remediation_track:** `assert-mergeable-head` only.
3. **scope:** Post-merge proof. **vulnerability:** Adopter still vendors prior
   controller/workflow bytes. **remediation_track:** Regen + dry-run after merge.

## Dissent

None.

## Amendment

Head advanced to  after CI required  to assert Node  (mechanical; slate intent unchanged). Seats re-balloted ACCEPT. Seat A REVISE twice (suppression test; sensitive_values scan before relay);
both remediated on `5ce5173b1188407d63ed748eddb038b4c2401d5b`.
