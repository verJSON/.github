# 0147 — Route untrusted CI to ephemeral hosted runners

- **Date:** 2026-08-27
- **Status:** Accepted
- **Issue:** [#1115](https://github.com/Verjson/.github/issues/1115)
- **Blocked by:** [#1114](https://github.com/Verjson/.github/issues/1114)
- **Extends:** [ADR 0040](../0040-runner-lanes-and-admission-axes/README.md), [ADR 0041](../0041-shared-admission-hosted-and-self-hosted/README.md), [ADR 0086](../0086-secretless-node-pr-validation/README.md), [ADR 0123](../0123-use-organization-neutral-ci-variables/README.md)

## Context

Canonical `node-ci.yml` already gives `secretless-pr` dependency acquisition and
repository-authored build jobs precedence on `CI_LANE_UNTRUSTED`; neither job can
fall through caller-supplied `runner`, trusted, or fallback selection while that
input is active. The live organization value nevertheless resolves to
`["self-hosted","general"]`, the same persistent DigitalOcean pool used by
trusted work. A same-repository pull request can therefore leave state for a
later job on the reused host even though the PR-authored job receives no secret.

Issue #1115 cited ADR 0046 as the hosted-execution authority. ADR 0046 governs
repository README hygiene and is unrelated. The applicable history is ADR 0040's
lane abstraction, ADR 0041's explicit acceptance that persistent and hosted
capacity currently serve both repository visibilities, and ADR 0086's separation
of dependency acquisition from secretless PR execution. This decision narrows
only the untrusted lane; it does not reverse ADR 0041's admission policy for
trusted work.

Two variable migrations leave six names live for immutable workflow pins:

| Generation | Variables |
| --- | --- |
| Canonical lane | `CI_LANE_UNTRUSTED` |
| Organization-neutral runner aliases | `CI_RUNNER_UNTRUSTED`, `CI_RUNNER_ISOLATED` |
| Historical lane | `VERJSON_LANE_UNTRUSTED` |
| Historical runner aliases | `VERJSON_RUNNER_UNTRUSTED`, `VERJSON_RUNNER_ISOLATED` |

Changing only the canonical name protects current callers but leaves historical
pins on persistent hosts. Deleting an alias also is not safe while ADR 0123's
inventory-backed retirement condition remains unmet.

## Decision

After #1114 lands, set all six untrusted variables to the exact JSON selector
`["ubuntu-24.04"]`. GitHub-hosted standard runners are ephemeral per job and do
not share the persistent DigitalOcean host state used by trusted lanes.

The cut does not change `CI_LANE_TRUSTED`, either canonical or historical
fallback variable, either default-runner variable, privileged routing, or any
workflow expression. In particular, `CI_LANE_FALLBACK` remains the persistent
general selector; it is not a rollback path for an explicitly configured
untrusted lane.

`runner-admission-reconcile.sh` treats the six names as one configuration until
their historical callers are proven retired. Every name must be present,
visible to all repositories, a non-empty JSON string array, and equal to
`CI_LANE_UNTRUSTED`. A hosted value is accepted only when its bytes are exactly
`["ubuntu-24.04"]`; another hosted label, extra label, malformed value, missing
alias, or partial cut fails closed. Self-hosted selectors remain valid before
the live cut and during explicit rollback.

No `node-ci.yml` routing rewrite accompanies this decision. Its existing
secretless acquisition and build placement is the source contract; adding a
second resolver would create precedence drift rather than improve isolation.

## Migration and canary

The organization-variable change is a separate, human-gated rollout after the
source decision merges:

1. Confirm #1114 is merged and the consumer chosen for the canary is pinned to a
   commit containing both #1114 and this decision. Snapshot all six untrusted
   variables plus trusted, fallback, privileged, and default selectors with
   their visibility.
2. Require the six untrusted variables to equal the prior persistent selector
   and require all preserved selectors to match the snapshot. Stop on an absent,
   malformed, repository-scoped, or already-divergent value.
3. Patch the six untrusted values in one attended operation, updating the five
   historical aliases first and canonical `CI_LANE_UNTRUSTED` last, in this
   exact order without changing visibility:

   ```text
   CI_RUNNER_UNTRUSTED
   VERJSON_LANE_UNTRUSTED
   VERJSON_RUNNER_UNTRUSTED
   CI_RUNNER_ISOLATED
   VERJSON_RUNNER_ISOLATED
   CI_LANE_UNTRUSTED
   ```

   If any request fails, restore every value changed in that operation before
   starting a canary.
4. Re-read all six through the organization API and require exact
   `["ubuntu-24.04"]` values with `all` visibility. Re-read the preserved
   selectors and require byte-for-byte equality with the preflight snapshot.
   Run the registered runner-admission reconciliation immediately; do not wait
   for its daily schedule.
5. Open or update one same-repository PR whose exact head exercises both
   secretless acquisition and PR-authored build execution. In GitHub's job API
   and `Set up job` logs, require both jobs to carry only the intended
   `ubuntu-24.04` selector and a GitHub-hosted image version, with no
   `self-hosted` label or persistent runner name. Require a trusted control job
   to retain its snapshotted lane. Bind the receipt to repository, PR, head SHA,
   workflow SHA, run attempt, job IDs, labels, runner names, and conclusions.
6. Verify the PR-authored job still has no package, checkout, cloud, OIDC, Git,
   or runner credential and that acquisition remains the only credentialed
   boundary. A green aggregator alone is not placement evidence.

The API assertions prove every historical alias; the canary proves the current
canonical path. Neither substitutes for the other.

## Rollback

Stop new canaries and restore all six untrusted variables to the exact preflight
`["self-hosted","general"]` value with `all` visibility. Do not change trusted,
fallback, privileged, or default selectors. Re-read all six, run runner-admission
reconciliation immediately, and record that untrusted PR execution has returned
to persistent capacity. A partial rollback is not an available state.

Rollback restores availability but also restores the cross-job persistence risk
this decision removes, so it requires a tracked cause and a new hosted canary
before the cut is attempted again.

## Consequences

- PR-authored execution no longer shares a reusable host with trusted work.
- Historical immutable pins follow the same destination as current workflows.
- Private-repository hosted usage can queue or fail when paid capacity is
  unavailable; there is deliberately no automatic persistent fallback.
- The attended live cut and consumer canary remain unperformed in this source
  change and must be recorded separately.

## 2026-09-22 amendment — bind reusable fastlane inputs to secretless PR execution

Issue #1548 requires private compatibility validation to use the centrally
configured GitHub-hosted fastlane after merge. The consumer policy may accept
that runner input only when the caller exposes exactly `pull_request` and
`push` to `main`, calls `node-ci.yml` at the same immutable contract SHA that
supplied the selector policy, and binds `secretless-pr` exactly to the
`pull_request` event. `node-ci.yml` gives that mode precedence over the
caller-supplied runner, so PR-authored code remains on `CI_LANE_UNTRUSTED`; the
fastlane input applies only to trusted `main` validation. The policy rejects
the same selector on merge queues, dispatches, non-default pushes, mismatched
contract pins, unguarded calls, and unrelated reusable workflows because
expression shape alone does not prove the called workflow and event preserve
this trust boundary.

## 2026-10-15 amendment — the persistent rollback is withdrawn

[ADR 0235](../0235-public-pull-request-code-uses-ephemeral-runners/README.md)
withdraws the Rollback section's restoration of the six untrusted variables to
`["self-hosted","general"]`. Public pull-request code now depends on the
untrusted lane being ephemeral, so a rollback target must be GitHub-hosted or a
dedicated ephemeral pool. Live enforcement in `runner-admission-reconcile.sh` is
tracked in [#1831](https://github.com/verJSON/.github/issues/1831).
