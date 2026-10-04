# 0120 — Bind terminal merge to a repository-scoped App token

> **2026-08-25 identity note:** ADR 0138 records the existing App's rename to the
> organization-neutral `merge-authorization` slug. Historical names below remain
> unchanged because they describe the configuration accepted at this decision date.

- **Date:** 2026-08-23
- **Status:** Accepted
- **Issue:** [#991](https://github.com/Verjson/.github/issues/991)
- **Extends:** [ADR 0036](../0036-separate-pr-review-from-privileged-merge/README.md), [ADR 0042](../0042-privileged-merge-reusable-split/README.md), and [ADR 0118](../0118-route-private-terminal-merge-to-hosted-capacity/README.md)
- **Follows:** [ADR 0099](../0099-release-app-replaces-admin-pat/README.md)

## Context

The terminal promotion workflow receives the long-lived human `ORG_ADMIN_TOKEN` for
its entire authorization-and-merge shell step. Generated callers reproduce that secret
contract across the fleet. Although the job is isolated from pull-request code and
validates its canonical workflow revision, exact head, arm receipt, authorization App,
required checks, source repository, and holds, every read and parser in the terminal
step can still reach a broad human credential.

The dedicated `verjson-merge-authorization` GitHub App is installed on all repositories
with exactly contents-write, pull-requests-write, and metadata-read permissions. App ID
4693283 is an Integration/always bypass actor on every active organization ruleset. The
App deliberately has no Actions, checks, administration, secrets, packages,
deployments, organization, or member permission.

The reusable workflow must therefore finish every read-only authorization decision
before minting merge authority. Repository identity is a particularly sensitive input:
the token action accepts an owner and repository selector, so neither workflow inputs,
pull-request metadata, nor shell-computed attacker-controlled text may widen it.

## Decision

Replace the terminal PAT with a short-lived installation token from the dedicated merge
App. Reusable callers pass only `merge_app_client_id` and
`MERGE_APP_PRIVATE_KEY`; generated callers bind those values to the organization
variable and secret. Empty, malformed, or numeric legacy IDs fail closed before minting.

The credentialless target preflight derives `TARGET_REPO` exclusively from
`github.repository`, validates its owner/name grammar, requires byte-for-byte equality
with `GITHUB_REPOSITORY`, and emits the owner and repository name used by the token
action. The immutable action pin requests exactly `contents: write` and
`pull-requests: write` for that one validated repository. A wrong installation,
inaccessible repository, missing key, or mint failure fails the job with no fallback.

Authorization is completed first under the repository `GITHUB_TOKEN`, with only
Actions, checks, contents, and pull-request read permissions. The step emits an
authorization output only after all existing exact-head, receipt, App-review, hold,
workflow-provenance, and required-check checks succeed. The installation token is then
minted and delivered only to a separate terminal step whose sole GitHub operation is:

`gh pr merge --admin --squash --match-head-commit`

GitHub's merged-state confirmation and best-effort receipt cleanup run afterward under
the read-only repository token; they cannot reuse the merge token. Pending, stale,
held, already-closed, or otherwise ineligible promotions do not mint a token.

## Security analysis

The App's installation-wide availability is narrowed twice: by a fixed trusted owner
derived from the workflow repository context and by the exact validated repository
name. The permission inputs are literal and non-widenable. The private key is consumed
only by the pinned token action, and the output token is absent from checkout,
authorization parsers, artifact processing, logs, and cleanup.

The repository token cannot bypass rulesets or merge, while the merge token cannot read
Actions/checks or organization state. This capability split prevents compromised read
or parsing logic from obtaining terminal authority and prevents the terminal credential
from becoming a general audit token. An attacker-controlled PR number, head SHA,
repository metadata field, runner input, or required-check policy cannot select another
installation repository.

The active rulesets grant bypass only to App ID 4693283, not to a mutable slug or a
human account. The canonical live slug remains `verjson-merge-authorization`; its
shorter name is intentional and is not renamed by this decision.

## Consequences

- Generated privileged-merge and retry callers no longer require
  `ORG_ADMIN_TOKEN`; they require the merge App client-ID/private-key contract.
- `ORG_ADMIN_TOKEN` remains live for separately audited organization runner,
  ruleset, secret-scope, watchdog, notification, and other legitimate consumers. This
  decision does not authorize deleting it.
- Rollout must use the canonical generator at the immutable merged contract SHA and
  must prove the App-backed path with a disposable controlled canary before fleet
  fan-out.
- Rollback disables terminal promotion or reverts workflow and callers together. A PAT
  fallback is not automatic and requires a new reviewed security decision.

## 2026-09-12 — Confirmed as the settled terminal-merge credential shape (#1323)

[#1323](https://github.com/Verjson/.github/issues/1323) asked for a design pass on the
terminal merge credential after Verjson/verjson-observability observed an automation
merge (PR #232, run `34645021831`) attributed to a human PAT owner (`mergedBy:
pyousefi`) instead of a self-identifying actor. Re-reading the current
`ai-privileged-merge.yml` on `main` (881f782) confirms this decision is unchanged and
remains the sole mechanism: the terminal `Merge the authorized head` step runs only
after `authorize-merge` succeeds, and its `GH_TOKEN` is exclusively
`steps.merge-app-token.outputs.token` (an installation token minted from
`MERGE_APP_PRIVATE_KEY`, hardened to the caller-owned `merge-app` main-only environment
by [ADR 0166](../0166-environment-only-app-private-keys/README.md)). No `ORG_ADMIN_TOKEN`
or other PAT path exists in the canonical workflow's merge step, matching the "no
fallback" consequence recorded above.

Root cause of the observed defect is consumer-side, not a canonical-contract gap: the
reporting adopter's caller pins a workflow revision from 2026-08-18 — five days before
this ADR's own merge (commit `c4250f4`, 2026-08-23) introduced the App-token step at
all — and separately still supplies `ORG_ADMIN_TOKEN` as a caller secret, which that
pre-App-token revision consumes directly as the merge credential. This repository is
public; the affected adopter is private and its remediation is not yet landed, so the
adopter's identity, exact pinned SHA, and specific still-open secret wiring are kept in
that private repository's own tracking issue rather than named here. No
consumer-repository change is proposed or made by this record: regenerating that caller
from the current canonical SHA and provisioning the `merge-app` environment (per
[the rollout doc](../../app-key-environment-rollout.md)) is that repository's own
remediation, owned by that repository's PM under the cross-repository ownership
boundary (this repository's PM may only leave a tracking issue there, never implement
or merge on its behalf).

This closes #1323's design pass with no change to this decision: the terminal-merge
credential shape it asks about was already decided here and is not superseded.


## 2026-10-04 — REST draft guard correction

[Issue #1685](https://github.com/Verjson/.github/issues/1685) identified a
representation mismatch at the final App-token read: the REST pull request
response has `draft`, while the CLI and GraphQL representation has `isDraft`.
Reading the latter from REST rejected valid non-draft PRs, including
[document-contracts #58](https://github.com/Verjson/verjson-document-contracts/pull/58).

The terminal guard requires the REST `draft` value to be Boolean `false`.
A draft, absent field, null, or string value continues to reject promotion.
The earlier CLI metadata check keeps its own `isDraft` representation. This
restores the existing authorization invariant without changing merge authority,
hold handling, independent review requirements, or the exact-head merge check.

Evidence: the real REST shape fails the original
`scripts/ci-gate/native-automerge.test.sh` promotion fixture. The corrected
predicate passes that fixture and explicit draft, absent, null, and malformed
value rejection cases; `scripts/ci-gate/terminal-merge-app-token.test.py` also
pins the REST predicate.
