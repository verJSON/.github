# ARB chair record — release.repository historical signed source split (#2229)

## Charter

- **Run id:** `arb-2229-release-repo-split-2026-11-18`
- **PR:** [verJSON/.github#2229](https://github.com/verJSON/.github/pull/2229)
- **Issue:** [verJSON/.github#2228](https://github.com/verJSON/.github/issues/2228)
- **Head:** `433059fb5f0285f28598df36761366768db634f0`
- **Question:** Approve autonomous merge of the deployment controller fix that
  binds host-export `release.repository` to the historical signed manifest
  source while `github.repository` keeps the post-rename live API identity,
  matching the existing transport rename contract?
- **Pins:** ADR 0271, ADR 0274, ADR 0275, ADR 0269, ADR 0257, ADR 0202; code head
  `433059fb5f0285f28598df36761366768db634f0`
- **Rule:** Unanimous ACCEPT among three returned seats (Grok 4.7, Grok 4.6,
  Composer 2.5).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `2c0a28fe8d83f9944a303f51f84be675a71db494f822fc995e0a5a0d5eb8d208`
  over:

```canonical-slate-json
{"head":"433059fb5f0285f28598df36761366768db634f0","issues":["verJSON/.github#2228","verJSON/.github#2229","verJSON/verjson-git-runners#197"],"items":["S-1","S-2","S-3"],"pr":"verJSON/.github#2229","run_id":"arb-2229-release-repo-split-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner ratification:** Owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 carry-forward session).

## Adopted slate

- **S-1:** Approve autonomous merge of #2229 after Gate R markers
  (`arb-consensus:v1` citation then ADR 0202 independent-review) on the tip
  that carries this archive.
- **S-2:** Do not use `--admin`; merge only via `assert-mergeable-head` when
  green.
- **S-3:** After merge, regenerate verjson-git-runners deployment adopter at the
  new contract SHA and re-dispatch dry-run with `action=deploy`,
  `manifest-identity=sha256:4f5bb96e1fe07f7b56cfe124206ed85c4e59b9715b3b4e18b3054d890dd1ad32`,
  `fleet-selector=github-canary`, `dry-run=true`.

## Ballots

| Seat | Overall | S-1 | S-2 | S-3 | Model |
| --- | --- | --- | --- | --- | --- |
| A | ACCEPT | ACCEPT | ACCEPT | ACCEPT | grok-4.7-high |
| B | ACCEPT | ACCEPT | ACCEPT | ACCEPT | cursor-grok-4.6-high-fast |
| C | ACCEPT | ACCEPT | ACCEPT | ACCEPT | composer-2.5 |

## Integration constraints

1. **scope:** Host-export release binding. **vulnerability:** Both
   `github.repository` and `release.repository` were taken from live
   `expectedRelease.sourceRepository`, so `fetch_manifest` raised
   `manifest source differs` after rename. **remediation_track:** Landed on
   `433059fb5f0285f28598df36761366768db634f0`.
2. **scope:** Autonomous merge. **vulnerability:** `--admin` bypasses ADR 0269.
   **remediation_track:** `assert-mergeable-head` only.
3. **scope:** Post-merge proof. **vulnerability:** Adopter still vendors prior
   contract bytes. **remediation_track:** Regen + dry-run on runners#197.

## Dissent

None.
