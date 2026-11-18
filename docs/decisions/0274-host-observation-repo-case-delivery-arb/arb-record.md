# ARB chair record — host observation repository case identity (#2226)

## Charter

- **Run id:** `arb-host-obs-case-2026-11-18`
- **PR:** [verJSON/.github#2226](https://github.com/verJSON/.github/pull/2226)
- **Issue:** [verJSON/.github#2225](https://github.com/verJSON/.github/issues/2225)
- **Head:** `7587a6a1074636e5774f47fa8fba9ce50799b187`
- **Question:** Approve autonomous merge of the deployment controller + transport
  ADR 0213 ASCII case-insensitive repository/organization identity fix so
  protected dry-runs accept live `verJSON/...` API display case against reviewed
  `Verjson/...` spellings?
- **Pins:** ADR 0213, ADR 0271, ADR 0269, ADR 0257, ADR 0202
- **Rule:** Unanimous ACCEPT among three returned seats after Seat A REVISE was
  remediated on the transport layer (Grok 4.7, Grok 4.6, Composer 2.5).
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `008823d561df3f9f21fd9a976c2c757ce244959aaefc435d187b1c252e749af9`
  over:

```canonical-slate-json
{"head":"7587a6a1074636e5774f47fa8fba9ce50799b187","issues":["verJSON/.github#2225","verJSON/.github#2226","verJSON/verjson-git-runners#197"],"items":["S-1","S-2","S-3"],"pr":"verJSON/.github#2226","run_id":"arb-host-obs-case-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner ratification:** Owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 carry-forward session).

## Adopted slate

- **S-1:** Approve autonomous merge of #2226 after Gate R markers
  (`arb-consensus:v1` citation then ADR 0202 independent-review) on exact head
  `7587a6a1074636e5774f47fa8fba9ce50799b187` (code) / tip including this archive.
- **S-2:** Do not use `--admin`; merge only via `assert-mergeable-head` when
  green.
- **S-3:** After merge, regenerate verjson-git-runners deployment adopter at the
  new contract SHA and re-dispatch protected dry-run (`github-canary`,
  `sha256:4f5bb96e…`) for runners#197.

## Ballots

| Seat | Overall | S-1 | S-2 | S-3 | Model |
| --- | --- | --- | --- | --- | --- |
| A (initial) | REVISE | REVISE | ACCEPT | REVISE | grok-4.7-high |
| A (re-ballot) | ACCEPT | ACCEPT | ACCEPT | ACCEPT | grok-4.7-high |
| B | ACCEPT | ACCEPT | ACCEPT | ACCEPT | cursor-grok-4.6-high-fast |
| C | ACCEPT | ACCEPT | ACCEPT | ACCEPT | composer-2.5 |

## Integration constraints

1. **scope:** Controller-only casefold. **vulnerability:** Transport still
   exact-matched live `login`/`full_name`. **remediation_track:** Landed on
   `7587a6a1` (`same_organization` / `same_repository` in transport).
2. **scope:** Autonomous merge. **vulnerability:** `--admin` bypasses ADR 0269.
   **remediation_track:** `assert-mergeable-head` only.
3. **scope:** Post-merge proof. **vulnerability:** Adopter still vendors prior
   contract bytes. **remediation_track:** Regenerate + dry-run on runners#197.

## Dissent

None after re-ballot.
