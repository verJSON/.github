# ARB chair record — .github#2245 doctl auth probe before host-export

## Charter

- **Run id:** `arb-2245-doctl-auth-probe-2026-11-18`
- **PR:** [verJSON/.github#2245](https://github.com/verJSON/.github/pull/2245)
- **Issue:** [verJSON/.github#2244](https://github.com/verJSON/.github/issues/2244)
- **Head:** `c80685148a17ad015f903e26eaa2077a8be281ac`
- **Question:** Approve autonomous merge of probing `doctl projects list` under
  the transport's isolated HOME/config before `runner-host-evidence`, so
  dry-runs relay DigitalOcean auth/context stderr after doctl install cleared
  ENOENT (dry-run 44248683810)?
- **Pins:** ADR 0271, ADR 0282, ADR 0198, ADR 0257, ADR 0202; head `c80685148a17ad015f903e26eaa2077a8be281ac`
- **Rule:** Unanimous ACCEPT among three returned seats (Grok 4.7, Grok 4.6,
  Composer 2.5).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `28130f1337c7c8331dfcaf5cebbdde265124ababd501764ed542ffbf2701f4bb`
  over:

```canonical-slate-json
{"head":"c80685148a17ad015f903e26eaa2077a8be281ac","issues":["verJSON/.github#2244","verJSON/.github#2245"],"items":["S-1","S-2","S-3"],"pr":"verJSON/.github#2245","run_id":"arb-2245-doctl-auth-probe-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner ratification:** Owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 carry-forward session).

## Adopted slate

- **S-1:** Approve autonomous merge of #2245 after Gate R markers on exact head
  `c80685148a17ad015f903e26eaa2077a8be281ac`.
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

1. **scope:** Gate R on .github#2245. **vulnerability:** Stale markers are not
   evidence for `c80685148a17ad015f903e26eaa2077a8be281ac`. **remediation_track:** Post preceding
   `arb-consensus:v1` then exact-head independent-review before
   `assert-mergeable-head`.
2. **scope:** Autonomous merge. **vulnerability:** `--admin` bypasses ADR 0269.
   **remediation_track:** `assert-mergeable-head` only.
3. **scope:** Post-merge proof / credentials. **vulnerability:** Probe may
   report access-token required if `RUNNER_HOST_EVIDENCE_DOCTL_CONFIG` is not
   a usable `access-token:` config under `doContext: default` (ADR 0015).
   **remediation_track:** Adopter regen + dry-run; operator re-provision if
   relayed.

## Dissent

None.
