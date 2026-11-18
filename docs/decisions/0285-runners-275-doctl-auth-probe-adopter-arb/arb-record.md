# ARB chair record — runners#275 doctl auth probe adopter regen

## Charter

- **Run id:** `arb-runners-275-2026-11-18`
- **PR:** [verJSON/verjson-git-runners#275](https://github.com/verJSON/verjson-git-runners/pull/275)
- **Issue:** [verJSON/verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197)
- **Head:** `656e592699e1e08cecbd8b5bca9afeb725201caa`
- **Question:** Approve autonomous merge of the protected container-deployment
  adopter regenerated at `Verjson/.github@76e61575c362a6a58102cbde080c54ecc8d836cd`
  (merged .github#2245 doctl auth probe), as S-3 follow-through of
  `arb-2245-doctl-auth-probe-2026-11-18` / ADR 0284?
- **Pins:** ADR 0271, ADR 0284, ADR 0257, ADR 0202; hub contract
  `76e61575c362a6a58102cbde080c54ecc8d836cd`; runners head `656e592699e1e08cecbd8b5bca9afeb725201caa`
- **Rule:** Unanimous ACCEPT among three returned seats (Grok 4.7, Grok 4.6,
  Composer 2.5).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `81a9e20b55bc464172aa15d12edbde59687c3df826af856648e2e6a036eb4081`
  over:

```canonical-slate-json
{"head":"656e592699e1e08cecbd8b5bca9afeb725201caa","issues":["verJSON/verjson-git-runners#197","verJSON/verjson-git-runners#275"],"items":["S-1","S-2","S-3"],"pr":"verJSON/verjson-git-runners#275","run_id":"arb-runners-275-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner ratification:** Owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 carry-forward session).

## Adopted slate

- **S-1:** Approve autonomous merge of #275 after Gate R markers on exact head
  `656e592699e1e08cecbd8b5bca9afeb725201caa`.
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

1. **scope:** Gate R on runners#275. **vulnerability:** Stale markers are not
   evidence for `656e592699e1e08cecbd8b5bca9afeb725201caa`. **remediation_track:** Post preceding
   `arb-consensus:v1` then exact-head independent-review before
   `assert-mergeable-head`.
2. **scope:** Autonomous merge. **vulnerability:** `--admin` bypasses ADR 0269.
   **remediation_track:** `assert-mergeable-head` only.
3. **scope:** Post-merge proof / credentials. **vulnerability:** Dry-run may
   still fail closed if `RUNNER_HOST_EVIDENCE_DOCTL_CONFIG` lacks a usable
   `access-token:` under `doContext: default`. **remediation_track:** Dispatch
   dry-run; operator re-provision if probe relays that fault.

## Dissent

None.
