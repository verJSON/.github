# 0289 — Fleet dispatch credential: cli-cloud-only App, environment-only custody

- **Date:** 2026-11-19
- **Status:** Accepted — ratified by the Architecture Review Board
  (`APPROVED` in the [ARB chair record][arb-record]; run
  `arb-2249-fleet-dispatch-credential-2026-11-19`; slate digest
  `e27e325598d4bec1c085750a381c8d02d806b1c9a4ae80175a4380f0e2e3f810`).
- **Issue:** [#2249](https://github.com/verJSON/.github/issues/2249)
- **Related:**
  [verjson-cli-cloud#620](https://github.com/verJSON/verjson-cli-cloud/issues/620),
  [verjson-cli-cloud#519](https://github.com/verJSON/verjson-cli-cloud/issues/519),
  [verjson-cli-cloud#622](https://github.com/verJSON/verjson-cli-cloud/issues/622),
  [#1940](https://github.com/verJSON/.github/issues/1940)
- **Extends:**
  [ADR 0166](../0166-environment-only-app-private-keys/README.md),
  [ADR 0180](../0180-no-approved-organization-custody-for-environment-only-app-keys/README.md),
  [ADR 0271](../0271-arb-consensus-is-approval-authority/README.md)
- **Category:** cross-repository dispatch credential and custody (sensitive
  class: App credential into the runner-fleet control plane)
- **ARB:** [arb-record.md](./arb-record.md)

## Context

cli-cloud ADR 0059 removed `on.schedule` from the fleet workflows and assigned
`verJSON/.github` the job of deciding *when* they run: O-B after a runner
release publishes, O-A on host or capacity signals. cli-cloud's contract is now
`workflow_dispatch` only and asks for an org dispatcher limited to
`actions: write`. `GITHUB_TOKEN` cannot reach another repository, and the only
existing cross-repository dispatch in this repository uses the org PAT
`ORG_ADMIN_TOKEN` (`notify-umbrella.yml`).

## Decision

1. **Credential.** A dedicated GitHub App, installed on `verjson-cli-cloud`
   only, with exactly `actions: write` and `metadata: read`. Tokens are minted
   with `repositories: verjson-cli-cloud` and `permission-actions: write` only.
   It calls `workflow_dispatch`, never `repository_dispatch` (which needs
   push-equivalent `contents: write`). No org PAT.
2. **Custody.** The private key (secret) and client id (variable) live only in
   a `.github` environment `fleet-dispatch` whose custom deployment-branch
   policy has exactly one rule, `main`, per ADR 0166. This ADR authorizes
   environment-only custody for the role. It adds no organization secret and no
   entry in `config/org-actions-secret-policy.json`, which governs organization
   secrets; the implementation asserts the key name stays absent from them.
3. **Emitter.** `.github/workflows/fleet-dispatch.yml`, `workflow_dispatch`
   only. An uncredentialed prerequisite job verifies the exact ref and the live
   environment policy before the single key-bearing job. Inputs are validated
   before any token is minted; only `runner-hourly-maintenance.yml` and
   `runner-capacity-reconcile.yml` at ref `main` are dispatched; no checkout;
   no cli-cloud variable reads or writes. `expected_manifest` is validated and
   recorded; enforcing it belongs in cli-cloud's environment-bound job.
4. **O-B, interim.** A release manager dispatches the emitter after a runner
   release. This deviates from cli-cloud ADR 0059 decision 3 until the
   cli-cloud owner accepts it as #519's closure evidence or amends ADR 0059.
   Automatic chaining is deferred to a successor ADR.
5. **O-A deferred.** No credential on fleet hosts or queue observers.
6. **Residual risk accepted.** The App can dispatch any cli-cloud
   `workflow_dispatch` workflow, cancel or re-run runs, delete runs and logs,
   and enable or disable workflows. Mitigations: cli-cloud guards on non-fleet
   workflows (cli-cloud#622), the `.github` emitter run summary as the audit record,
   and a rotation and revocation runbook delivered with the emitter.

## Non-goals

- This ADR creates no App, environment, secret or workflow and authorizes no
  live mutation. The App is an owner action; the emitter is a later PR.
- It does not change cli-cloud workflows or ADR 0059.

## Consequences

- #2249 is implemented only after cli-cloud#620 restricts
  `runner-fleet-production` to `main` and provisions its secrets.
- No key is provisioned before Decisions 1 and 2 hold by live readback.
- The emitter maps inputs per target: `runner-hourly-maintenance.yml` takes
  `only` and `trigger_source`; `runner-capacity-reconcile.yml` declares no
  inputs at the pin and is dispatched with none (extra inputs fail the dispatch
  with HTTP 422). The implementation's stubbed test pins that mapping.
- A change of App permissions, custody, or dispatch targets needs a new ADR.

## Verification

The ARB ran two rounds; round 1 drew eight REVISE votes, all adopted, and round
2 was unanimous after one malformed ballot was re-requested. Evidence citations
were checked against the pinned sources with `check_review_packet.py`; the ledger
validates with `validate_ledger.py --state`.

[arb-record]: ./arb-record.md
