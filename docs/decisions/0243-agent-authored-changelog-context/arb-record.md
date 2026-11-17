# ARB chair record — Renovate agent delivery claim (ADR 0243 amend)

## Charter

- **Run id:** `arb-2139-2026-11-17`
- **Issues:** [verJSON/.github#2139](https://github.com/verJSON/.github/issues/2139)
- **Status:** `APPROVED`
- **Slate digest (SHA-256):**
  `9fee6d34a4ef9c4140a899064fd9bfdc82ccddd611eac6c785dbf52b84b7aff9`
- **Canonical slate:**

```canonical-slate-json
{"decisions":{"S-1":"accept_amend","S-2":"reject","S-3":"defer_follow_on"},"issues":["verJSON/.github#2139"],"items":["S-1","S-2","S-3"],"run_id":"arb-2139-2026-11-17","status":"APPROVED"}
```

```json
{"decisions":{"S-1":"accept_amend","S-2":"reject","S-3":"defer_follow_on"},"issues":["verJSON/.github#2139"],"items":["S-1","S-2","S-3"],"run_id":"arb-2139-2026-11-17","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Roster note:** Advisory seats (CLI verified empty). Decision rule:
  unanimous among returned advisory seats.

## Adopted slate

- **S-1 accept_amend:** Durable Renovate handoff is an idempotent **agent
  delivery claim** per `(repo, pr, head_sha)`. Keep `needs-changelog-context`
  as the signal. Worker runs the dependency-upgrade skill. `check-pr` remains
  the sole changelog merge gate. Merge only under autonomous delivery gates.
- **S-2 reject:** Do not reinstate ADR 0104 / privileged `NEXT/` writers,
  actor exemptions, weakened `check-pr`, templated hook fragments, or
  approval-supplying auto-merge.
- **S-3 defer_follow_on:** Dispatcher/worker/canary implementation tracked
  under follow-on (#2191).

## Ballots

| Seat | S-1 | S-2 | S-3 |
| --- | --- | --- | --- |
| A | ACCEPT | ACCEPT | ACCEPT |
| C | ACCEPT | ACCEPT | ACCEPT |

## Dissent

None.

Archived for Gate R citation from the local chair artifact
`verJSON/.github/.git/arb-2139-20261117/chair-record.md`.
