# ARB chair record — .github#2251 host-export diagnostic relay allowlist

## Charter

- **Run id:** `arb-2251-host-export-relay-allowlist-2026-11-18` (ledger run 6 of the decision; earlier runs below)
- **PR:** [verJSON/.github#2251](https://github.com/verJSON/.github/pull/2251)
- **Issue:** [verJSON/.github#2250](https://github.com/verJSON/.github/issues/2250)
- **Head:** `b2251048c4dc9d16f88b4295e80cd155dcf6336f`
- **Question:** Approve autonomous merge of host-export and adapter diagnostics
  relaying only allowlisted forms: doctl output mapped to fixed messages; the
  CLI's last `✗` error line, an adapter's first `::error::` line and the
  transport's `deployment transport: ` exit line relayed only as short plain
  prose without URL, query, assignment, double quote, brace, flag,
  token-shaped run, credential marker or secret the process holds; any
  traceback withholds everything; allowlist defined once in the transport and
  imported by the controller; ADR 0198 amended.
- **Pins:** ADR 0271, ADR 0198, ADR 0280, ADR 0257, ADR 0202, ADR 0284;
  baseline `main@08bc666f08e9654169c63140a24ddc5e9c0f686e`; organization
  context `verJSON/verjson-docs` config `57932dc678cb089a99c19e6eb3aba78329933eab`, manifest
  `b1b7c897f5208ccc31dbd8b562e0ed0d8169788158281b107c58661bd4b425fd` (grounded, no gaps).
- **Rule:** unanimous; ledger schema v2 (unverified members may block, never
  approve alone); three required members.
- **Status:** `APPROVED` (`validate_ledger.py --state`: `run_state_verified:
  true`, scope `full_slate`, delivery conditions clear).
- **Ledger slate digest:** `b1d3212372a93473d7a423287610a33a8de18de9e30e85ed064b48f7ee1d0bc4`
- **Slate digest (SHA-256)** for Gate R:
  `86736a84ec9ee6b90e06c080c1302556b668fdf6085bd8e0e73e86b91a5aec0e`
  over:

```canonical-slate-json
{"head":"b2251048c4dc9d16f88b4295e80cd155dcf6336f","issues":["verJSON/.github#2250","verJSON/.github#2251"],"items":["S-1","S-2","S-3"],"pr":"verJSON/.github#2251","run_id":"arb-2251-host-export-relay-allowlist-2026-11-18","status":"APPROVED"}
```

- **Approval authority:** ADR 0271 ARB consensus + ADR 0202 receipt — not
  personal CODEOWNERS.
- **Owner direction:** the owner delegated merge authority contingent on ARB
  consensus for this delivery class (2026-11-18 session), chose the allowlist
  design after run 2, and authorized fresh runs after runs 3 and 5 escalated.
  These directions are recorded only in the chair-written charters of the
  following runs; every ledger's `governance.ratification_status` remains
  `pending`, and this record claims no separate human ratification.

## Adopted slate

- **S-1:** Approve autonomous merge of #2251 after the `arb-consensus:v1`
  citation and an exact-head `independent-review:v1` marker on `b2251048c4dc9d16f88b4295e80cd155dcf6336f`.
  Accepted limits, stated in the slate and ADR 0198: (1) config values shorter
  than 8 characters inside a multi-key secret are not checked (values such as
  `default`, `true` or `1` would otherwise withhold nearly every diagnostic;
  single-line secrets are always checked whole); (2) plain words from an HTTP
  body or unflagged command words can relay, since they carry no credential
  shape; CLI and adapter vocabularies are owned by verjson-cli-cloud and
  adopters and are not enumerated here.
- **S-2:** Do not use `--admin`; merge only when `assert-mergeable-head`
  prints `true` for the captured head, with `--match-head-commit`.
- **S-3:** After merge, the verjson-git-runners owner regenerates the adopter
  at the merge tip, Gate R merges that PR, and re-dispatches the protected
  dry-run to read the relayed diagnostic (verjson-git-runners#197). This
  session makes no cross-repository change.

## Ballots (run 6)

| Seat | S-1 | S-2 | S-3 | CLI | Model identity |
| --- | --- | --- | --- | --- | --- |
| A | ACCEPT | ACCEPT | ACCEPT | `claude` | claude-opus-5-5 (chair-verified from `modelUsage` in the claude CLI output; that output is retained with SHA-256 `7bc0d3f68d76cd3c79be2f105dbd184439ee722d420b492d5bc76c5d9a73702c`, and the hashed raw ballot below excludes it) |
| B | ACCEPT | ACCEPT | ACCEPT | `codex` | unverified — requested gpt-6-luna; codex exec reports no served model |
| C | ACCEPT | ACCEPT | ACCEPT | `cursor-agent` | unverified — requested composer-2.5; JSON names no served model |

`approval_includes_unverified_votes`: seats B and C on S-1, S-2 and S-3 (each
candidate also carries seat A's verified ACCEPT).

Raw response digests (run 6):

- seat-a: `f46832777e5657fe23c7c9041a37a64b87fe9176358807d1c2a3e884aaed1693`
- seat-b: `1cb163ad0b417f964c5ad66e9cf03bad8d844795b29c15e9207033d07d4d86bb`
- seat-c: `eff8a7ab5b3c256278771b915ec58907cea76aba9d45bbfc8321f6929393b4a0`

## Run history and dissent

| Run | Head | Rounds used | Dissent and disposition |
| --- | --- | --- | --- |
| 1 | `3ffc83b5` | 1 of 2 | REVISE (seat B, S-1): withhold HTTP bodies, signed URLs, command text — adopted in fdefe6b5 |
| 2 | `fdefe6b5` | 1 of 1 (remaining budget) | REVISE (seats A, B, S-1): replace the denylist with an allowlist — escalated; owner chose the allowlist |
| 3 | `df18d7db` | 2 of 2 (owner-authorized) | REVISE (seat B, S-1): round 1 semantic message lists (rejected by chair; limit stated); round 2 alphabet-only runs and adapter secrets — escalated; fixed in b5e97523 |
| 4 | `b5e97523` | 1 of 2 (owner-authorized) | REVISE (seat B, S-1): short single-line secrets exempted — fixed in c44d6f60 (source correction ends the run) |
| 5 | `c44d6f60` | 1 of 1 (remaining budget) | REVISE (seat B, S-1): check short config values at any length — escalated; chair kept the 8-character floor and corrected ADR 0198 wording |
| 6 | `b2251048` | 1 of 2 (owner-authorized) | none — unanimous ACCEPT |

Where each disposition is recorded: run 1's adoption and run 2's escalation
in their round state (`dispose`, exported as `retained_dissent` files); run 3's
round-1 rejection in run 3's ledger `retained_dissent`; run 3's round-2 and
run 5's escalations in their round state, and in the charter text of the run
that followed; run 4 ended at a source correction under the cross-run budget
rule. Ledgers, round state, packets, raw responses and CLI outputs are
retained privately with the chair.

## Integration constraints

1. **scope:** Gate R on .github#2251. **vulnerability:** a marker on an older
   head is not evidence for `b2251048c4dc9d16f88b4295e80cd155dcf6336f`. **remediation_track:** post the
   `arb-consensus:v1` citation, then the exact-head `independent-review:v1`
   marker, before `assert-mergeable-head`.
2. **scope:** Autonomous merge. **vulnerability:** `--admin` bypasses ADR 0269.
   **remediation_track:** `assert-mergeable-head` only.
3. **scope:** Post-merge proof. **vulnerability:** unit tests simulate doctl and
   CLI output; real relayed text is unproven until the protected dry-run.
   **remediation_track:** adopter regeneration and dry-run under S-3.

## Dissent

None in run 6. Earlier dissent and its dispositions are listed above.
