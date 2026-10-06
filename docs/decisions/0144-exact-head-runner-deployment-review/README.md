# 0144 — Bind runner deployment to exact-head independent review

- **Date:** 2026-08-26
- **Status:** Accepted
- **Supersedes:** [ADR 0078](../0078-container-release-and-runner-deployment-contract/README.md) only for deployment review authorization and credential separation
- **Issue:** [#629](https://github.com/Verjson/.github/issues/629)

## Context

ADR 0078 used a required GitHub environment reviewer as deployment authority and one
environment token for provider mutation. That gate neither proves adversarial review of
the exact published tree nor separates DigitalOcean authority from GitHub runner-control
authority.

## Decision

The `production` environment remains the protected-default-branch credential and audit
boundary. It requires no environment reviewer, may permit administrator bypass, and
records the authoritative bypass fact in every receipt.

Deployment requires three immutable receipts bound to the same repository, published
pull request, head commit, Git tree, and patch artifact: independent adversarial code
review, independent adversarial security review, and terminal-green AI review CI issued
by the configured trusted App. The pre-publication reviews are reconciled to the
published exact head and tree. All reviewer principals differ from each other and the
dispatcher. Branch names, merge refs, comments, stale checks, and dispatcher-authored
evidence confer no authority. Any head or tree change invalidates all three gates.
The canonical workflow, not the consumer evidence adapter, acquires repository and PR
IDs, diff digest, check/App IDs, workflow path/run/attempt, artifact ID/digest, actors,
freshness, and environment policy directly from GitHub. Stable numeric App and principal
IDs establish independence; mutable display names do not.
Trusted App IDs, installation IDs, workflow paths, and check names are immutable fields
in reviewed consumer configuration. Canonical generated code, security, and AI adapter
producers run from the protected producer commit. Uncredentialed analysis treats the PR
only as API data. Separate producer-specific protected environments mint publisher
tokens after artifact creation, execute only the protected producer tree, and compare
the minted installation ID with reviewed configuration. Each publisher token is bound
to the current repository and explicitly requests only `checks: write`; the token action
input contract is tested as an exact map so a future App permission expansion cannot
silently widen an issued token. The AI adapter proves a pinned
App-owned exact-head terminal-green source check. The deployment verifier
downloads the exact artifact ZIP, verifies its GitHub digest, accepts exactly one bounded
`review-receipt.json`, validates canonical receipt bytes and identity, and resolves code
and security principals from exact-head GitHub PR approvals and re-fetches the AI source
check. Generated adapters pin the reusable producer to the exact contract commit.
Caller-owned protected environments are the native pre-secret-release gate; an
uncredentialed prerequisite verifies their policy and the default ref. GitHub releases
environment secrets when the credentialed job starts, so its first step is an audit and
TOCTOU reconciliation, not a retroactive release gate. That job validates GitHub-owned
serialized `job.workflow_repository/ref/sha` and executes only that validated canonical
repository at the exact called-workflow commit. `toJSON(job)` is parsed in a nonsecret
step because actionlint 1.7.7 does not yet type those official fields. Caller
`github.workflow_*` and contract inputs are never producer-code authority. The preflight has
exact `actions: read`/`contents: read` permissions. Both sides hash the sorted compact
environment-policy JSON projection without a trailing newline. Producers run after
merge on the default ref and reconcile prior exact-PR-head reviews into checks on the
deployed commit. Publisher commit/tree and target PR head/tree are distinct
receipt fields. Post-merge reconciliation admits only when the exact checked-out
default-branch tree equals the reviewed PR tree; merge/squash/rebase commit IDs may
differ but conflict-resolution tree drift fails closed. With
no environment protection rules, a false bypass fact
is directly provable from the sole live `branch_policy` rule; any wait timer, reviewer
rule, or custom protection rule fails closed.

Provider and GitHub authority are separate. `DIGITALOCEAN_RUNNER_FLEET_TOKEN` is mapped
only to `DIGITALOCEAN_ACCESS_TOKEN` for the runner-update child process. The workflow
mints a short-lived `org-gh-runner-registration` installation token from
`GH_RUNNER_REGISTRATION_APP_CLIENT_ID`, `GH_RUNNER_REGISTRATION_APP_INSTALLATION_ID`,
and `GH_RUNNER_REGISTRATION_APP_PRIVATE_KEY`, bound to the current repository and
requesting only `organization_self_hosted_runners: write`. Probes, receipts, artifacts, and logs
receive neither credential.

All other ADR 0078 invariants remain: immutable manifest provenance, explicit dispatch,
dry-run, drain and quarantine, canary-first one-host-at-a-time rollout, capacity floors,
append-only receipts, resumability, and independently dispatched verified rollback.

## Consequences

Administrator operation remains possible without an environment approval while every
deployed tree still needs independent code, security, and AI evidence. Historical
schema-v3 receipts remain immutable and cannot be reinterpreted as this authority.
The v4 controller rejects schema-v3 rollback sources explicitly. Operators must finish
or roll back every active v3 attempt with the pinned v3 contract before cutting over;
v3 human environment approval is never translated into v4 review authority.

## 2026-10-06 — Keep observation-only previews separate from deployment authority

Issue [#1654](https://github.com/verJSON/.github/issues/1654) showed that the
dry-run workflow could not collect evidence: it had no reviewed deployment
authorization, while the controller required that authorization whenever
GitHub supplied a run ID. The subsequent plan builder required it too.

A dry-run now records the workflow repository and run identity as observation
authority and emits a marked preview plan. It still checks the immutable release,
host observation receipt, fleet, and checked-out source. It cannot create an
admitted receipt or execute a deployment. The mutating path still requires the
reviewed authorization and exact workflow-run binding described above. This
keeps the dry-run useful without treating observation as deployment approval.
