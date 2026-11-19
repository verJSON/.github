# ARB chair record — fleet dispatch credential (2026-11-19)

## Standards grounding

- status: grounded
- config_sha: `57932dc678cb089a99c19e6eb3aba78329933eab`
- manifest_sha256:
  `5ae0c3cf480c9285bdae773f3d6f1c47a70c74b698f839f574d65e6a08031783`
- canonical_complete: true; gaps: none

## Charter

- **Run id:** `arb-2249-fleet-dispatch-credential-2026-11-19`
- **Issue:** [verJSON/.github#2249](https://github.com/verJSON/.github/issues/2249)
- **Question:** Which credential, custody and trigger should the `verJSON/.github`
  org dispatcher for `verJSON/verjson-cli-cloud` fleet workflows use (cli-cloud
  ADR 0059 O-B/O-A; contract now `workflow_dispatch`-only at cli-cloud@`b4b248ab`),
  given that `actions: write` on cli-cloud can also dispatch `release.yml` and
  `ai-privileged-merge.yml`? Alternatives: (A) no new credential, operators run
  `gh workflow run` directly; (B) dedicated App with `actions: write` on cli-cloud
  only, key in a main-only `.github` environment, fixed-path emitter; (C)
  `repository_dispatch` (`contents: write`); (D) org PAT / `ORG_ADMIN_TOKEN`; (E)
  credential in `verjson-github-runner` for automatic O-B chaining.
- **Pins:** primary `verJSON/.github@2c7aaedba3af4827db5da99889dad48075c2eefe`;
  context `verJSON/verjson-cli-cloud@b4b248abfea9fd55c94d514277265da49b8524ca` and
  `verJSON/verjson-github-runner@6a78a9ded12399e8cfe0acc64812db9d19971a65`.
  Governing: ADR 0166, ADR 0180, ADR 0271; cli-cloud ADR 0059 and its dispatch
  contract.
- **Rule:** unanimous; ledger schema v2 (unverified members may block, never
  approve alone); three required members; at most two rounds.
- **Status:** `APPROVED` (`validate_ledger.py --state`: `run_state_verified:
  true`, scope `full_slate`, delivery conditions clear).
- **Citation packet:** `check_review_packet.py` against the three pinned
  repositories returned `ok: true` for revision 1 (packet SHA-256
  `75ed3ae40c39999153770ffd33bdc7d0be1295062c7db8e3fdaf544a283cc654`) and
  revision 2 (`52d505043567048559a0c4df93003c18f8eb35e57c2c79b0da2b76296ac13eda`).
- **Ledger slate digest:** `b11df92b04c4dcf80d587f05e1e23bbab2ad2aeeabe14977fd4a3ef712b207f7` (revision 2)
- **Slate digest (SHA-256)** for Gate R:
  `e27e325598d4bec1c085750a381c8d02d806b1c9a4ae80175a4380f0e2e3f810`
  over:

```canonical-slate-json
{"decisions":["verJSON/.github ADR 0289"],"issues":["verJSON/.github#2249","verJSON/verjson-cli-cloud#620"],"items":["S-1","S-2","S-3","S-4","S-5","S-6"],"run_id":"arb-2249-fleet-dispatch-credential-2026-11-19","status":"APPROVED"}
```

- **Owner direction:** the owner commissioned this design ARB (2026-11-19) and
  delegates design calls to ARB consensus. Creating and installing the App is an
  owner action. Every ledger's `governance.ratification_status` remains
  `pending`; this record claims no separate human ratification.

## Adopted slate (revision 2)

- **S-1 — Dispatch API and scope.** `workflow_dispatch` only, never
  `repository_dispatch`, through a dedicated App installed on
  `verjson-cli-cloud` alone with exactly `actions: write` and `metadata: read`.
  Each token is minted with `repositories: verjson-cli-cloud` and
  `permission-actions: write` only. Rejects (C) and (D). Blocked by
  cli-cloud#620; before the key is provisioned, a live readback must show
  cli-cloud `runner-fleet-production` with a custom deployment-branch policy
  whose only rule is `main`.
- **S-2 — Custody.** Key (secret) and client id (variable) only in a new
  `.github` environment `fleet-dispatch` whose custom deployment-branch policy
  has exactly one branch rule, `main`. No organization secret and no
  `config/org-actions-secret-policy.json` entry (that policy governs
  organization secrets, `scripts/org-secret-scope-audit.py:14`). This ADR is the
  recorded decision authorizing environment-only custody for the role; the
  implementation asserts the key name is absent from organization secrets.
- **S-3 — Emitter.** `.github/workflows/fleet-dispatch.yml`, `workflow_dispatch`
  only: an uncredentialed ADR 0166 prerequisite job checks the exact ref and live
  environment policy before the single key-bearing job. Validated inputs (target
  enum, `only` regex, `trigger_source` enum, `expected_manifest` as
  `sha256:<64 hex>`, recorded only). No cli-cloud variable reads or writes; two
  fixed workflow paths at ref `main`; no checkout; stubbed behavioral test
  registered in `scripts/actions-ci-groups.tsv`. Manifest enforcement is a
  cli-cloud handoff (an `expected_manifest` input checked inside its
  environment-bound job).
- **S-4 — O-B trigger (interim).** A release manager dispatches the emitter after
  a `verjson-github-runner` release. Recorded as an interim deviation from
  cli-cloud ADR 0059 decision 3; the cli-cloud owner either accepts an
  operator-dispatched emitter run as #519's closure evidence or amends ADR 0059.
  Automatic chaining (E) rejected for now; a successor ADR evaluates options
  including credential-free release observation.
- **S-5 — O-A deferred.** No host or queue observer receives a GitHub
  credential; operators use the same emitter until a signal-bridge ADR.
- **S-6 — Residual risk.** Keep (B) over (A). The App identity can dispatch any
  cli-cloud `workflow_dispatch` workflow (`release.yml`, `ai-privileged-merge.yml`,
  `ai-review-merge.yml`), cancel or re-run runs, delete runs and logs, and enable
  or disable workflows; S-2/S-3 constrain only the intended path. Mitigations: a
  cli-cloud consumer-side guard handoff, the `.github` emitter run summary as the
  authoritative audit record, a rotation and revocation runbook with the
  implementation, and a link to .github#1940. No key before S-1 and S-2 hold.

## Ballots

| Seat | Round 1 (S-1…S-6) | Round 2 (S-1…S-6) | CLI | Model identity |
| --- | --- | --- | --- | --- |
| A | REVISE, REVISE, REVISE, REVISE, ACCEPT, REVISE | ACCEPT ×6 | `claude` | claude-opus-5-5 (chair-verified from `modelUsage`; round-2 raw output SHA-256 `5c5d6bf455b2840ec71135c351a7de1b8b4a3cc7b7f3358a19834ef609914e0c`) |
| B | REVISE, REVISE, ACCEPT, ACCEPT, ACCEPT, REVISE | ACCEPT ×6 (re-cast) | `codex` | unverified — requested gpt-6-luna; codex exec reports no served model |
| C | ACCEPT ×6 | ACCEPT ×6 | `cursor-agent` | unverified — requested composer-2.5; JSON names no served model |

`approval_includes_unverified_votes`: seats B and C on every candidate (each
candidate also carries seat A's verified ACCEPT).

Round 2 raw response digests: seat-a
`3a2169eb1c6f1b6ec4aaf30e2b4ff29cfefcafd866bea7196e289a41d8bd2a37`; seat-b
`bc638f424a8f68461b37da2245739dacb3986fbd9be73af8e91353fb63aca372`; seat-c
`5f3737c9db3eadef2f2fe1f26039dc8fa4f8dc516c7a22c34de9ce670988e6e7`.

**Failed call.** Seat B's first round-2 response
(`19f7ca5ba80222857dc7882a0c186d52c3db02f60b741a8bd3bedaacbd8ad92d`) voted REVISE
on S-6 without the required `correction` and was recorded as `FAILED_CALL`
(`incomplete_dissent`). The chair re-requested it within round 2 with a neutral
format note and did not edit it; the re-cast ballot accepted all six. Its reason
is preserved: the guard should be tracked by a linked cli-cloud issue and
delivered before the key is provisioned. The chair opened
verJSON/verjson-cli-cloud#622 to track it; delivery before provisioning is not
a board decision.

## Dissent

Round 1: eight REVISE votes (seat A on S-1, S-2, S-3, S-4, S-6; seat B on S-1,
S-2, S-6). All were adopted in revision 2 by `ARB chair (.github PM session,
claude-opus-5-5)`:

- variables permission removed (seat A: the emitter-side manifest pre-check was
  unsound because the target reads the variable inside an environment-bound job,
  `runner-hourly-maintenance.yml:27,95`; seat B: the permission name was wrong);
- custody by this ADR, not by a policy-file entry (seats A and B; the chair
  verified the policy governs organization secrets only);
- the ADR 0166 uncredentialed prerequisite job added (seat A; ADR 0166 lines 35–41);
- manual O-B recorded as a deviation from cli-cloud ADR 0059 with an explicit
  owner decision (seat A);
- the full residual Actions surface named, with a guard handoff and runbook
  (seats A and B).

Round 2 had no dissent.

## Integration constraints

1. **scope:** cli-cloud readiness. **vulnerability:** `runner-fleet-production`
   has a protected-branches deployment policy, not a custom rule admitting only
   `main` (insufficient under ADR 0166), and no provisioned secrets. **remediation_track:**
   verJSON/verjson-cli-cloud#620; no key before its branch policy is `main`-only.
2. **scope:** non-fleet cli-cloud workflows. **vulnerability:** the App's
   `actions: write` can dispatch them. **remediation_track:**
   [verJSON/verjson-cli-cloud#622](https://github.com/verJSON/verjson-cli-cloud/issues/622).
3. **scope:** O-B event semantics. **vulnerability:** cli-cloud ADR 0059 expects
   event-driven dispatch. **remediation_track:** cli-cloud owner accepts the
   interim or amends ADR 0059 (handoff on
   [cli-cloud#620](https://github.com/verJSON/verjson-cli-cloud/issues/620#issuecomment-7000967218)).
4. **scope:** platform permissions. **vulnerability:** the permission facts
   (dispatch API scopes, `actions: write` reach) are chair-asserted from GitHub
   documentation, not executed here. **remediation_track:** the implementation
   verifies the App's effective permissions by live readback before first use.
