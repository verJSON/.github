# 0217 — Bound GAR canary publishing to one GitHub workflow

- **Date:** 2026-10-05
- **Status:** Accepted for the bounded canary; live evidence remains required
- **Issue:** [verJSON/.github#1689](https://github.com/verJSON/.github/issues/1689)
- **Related:** [verJSON/.github#1667](https://github.com/verJSON/.github/issues/1667) and [ADR 0216](../0216-keyless-container-provenance-is-verified-per-registry/README.md)
- **Category:** cloud IAM and workload identity (sensitive class)

## Context

The private reusable-workflow and GHCR-to-GAR canary needs a Docker repository
that a trusted GitHub Actions publisher can write without a long-lived key. The
`verjson-ci-640463` project contains a dedicated `container-canary` repository
in `us-central1`. A project-wide writer or a provider accepting every GitHub
repository would let unrelated workflows publish artifacts into the canary.

## Decision

Use a dedicated GitHub OIDC workload identity pool and provider for the private
`verJSON/verjson-ci` consumer. The provider accepts only tokens whose numeric
repository owner ID is `279365001`, numeric repository ID is `1356313946`, ref
is `refs/heads/main`, event is `push`, and reusable publisher workflow identity
is the reviewed `.github/workflows/container-candidate-publish.yml` at proposed
contract commit `f9b62501593a30406e2e25f041448827c2ea0a20`. Check both the
reusable workflow path and `job_workflow_sha`; a caller-controlled workflow
name or registry configuration is not sufficient authority. An absent or
unexpected claim denies federation. The proposed commit is temporary canary
scope: update the provider to the merged contract SHA and repeat the canary if
the merge changes that SHA.

The canary uses pool `github-oci-candidates`, provider `verjson-ci-main`, and
issuer `https://token.actions.githubusercontent.com/`. Map
`google.subject=assertion.sub` and
`attribute.repository_id=assertion.repository_id`. Its provider condition is:

```text
assertion.repository_owner_id == '279365001' &&
assertion.repository_id == '1356313946' &&
assertion.ref == 'refs/heads/main' &&
assertion.event_name == 'push' &&
assertion.job_workflow_sha == 'f9b62501593a30406e2e25f041448827c2ea0a20' &&
(assertion.job_workflow_ref == 'Verjson/.github/.github/workflows/container-candidate-publish.yml@f9b62501593a30406e2e25f041448827c2ea0a20' ||
 assertion.job_workflow_ref == 'verJSON/.github/.github/workflows/container-candidate-publish.yml@f9b62501593a30406e2e25f041448827c2ea0a20')
```

Grant only the matching repository principal set permission to impersonate a
dedicated `oci-canary-publisher` service account. Grant that account Artifact
Registry Writer on `container-canary` itself, not on the project. Do not create
service-account keys or give pull-request jobs the publisher identity. The
canonical publisher's GAR mirror job is the only job configured to exchange
GitHub OIDC for Google credentials. Other jobs in that immutable reusable
workflow also request OIDC for Cosign, so the provider condition trusts the
reviewed workflow as a whole; it does not distinguish the mirror job by ID.

Before enabling candidate eligibility, inspect the authentic private run's
OIDC and Cosign claims, and independently retrieve the image index, provenance,
and each reviewed platform's signed SBOM from GAR at the original GHCR digest.
If a claim differs from the expected shape, record the observed non-secret
claim and review a narrow policy correction; do not remove a predicate merely
to make federation succeed.

## Consequences

Other organizations can adopt the generated caller with their own reviewed
provider and service account, scoped to their numeric GitHub IDs and protected
publication ref. No global cross-organization publisher identity is implied.
The canary repository and identity incur a bounded operational surface; failed
federation or missing registry evidence leaves promotion unavailable.

## Amendment (2026-10-21) — every publisher repin needs a prior provider rotation

The SHA in the Decision's condition, `f9b62501…`, is the canary-time value and is
historical. The provider was rotated once already to
`6fecc93adbb1aecd6ea3588c2e9f3b96b50b43a6`, but this ADR was not amended then.

[Verjson/verjson-ci#303](https://github.com/Verjson/verjson-ci/pull/303) (merged as
`b0749b81`) regenerated the adopter set at Verjson/.github v4.2.0,
`a89fe1f090545c15cdd6987f20119327206e2c59`. That moved the reusable publisher pin
before anyone rotated the provider. The GAR mirror then failed closed as intended.
In verjson-ci run
[39520892937](https://github.com/Verjson/verjson-ci/actions/runs/39520892937) attempt 1,
`google-github-actions/auth` reported `unauthorized_client … rejected by the
attribute condition`. Runs
[39585702767](https://github.com/Verjson/verjson-ci/actions/runs/39585702767)
(`b780194f`) and
[39585901024](https://github.com/Verjson/verjson-ci/actions/runs/39585901024)
(`8faee03b`) failed the same way because their `mirror-gar` jobs started before
the rotation.

The owner authorized rotating provider
`projects/734515786459/locations/global/workloadIdentityPools/github-oci-candidates/providers/verjson-ci-main`
in project `verjson-ci-640463`. The `update-oidc --attribute-condition` call
changed only the three SHA occurrences: `job_workflow_sha` and both
`job_workflow_ref` spellings (`Verjson/` and `verJSON/`). They moved from
`6fecc93adbb1aecd6ea3588c2e9f3b96b50b43a6` to `a89fe1f090545c15cdd6987f20119327206e2c59`.
The owner ID `279365001`, repository ID `1356313946`, ref `refs/heads/main`,
event `push`, attribute mapping, and provider state did not change. After the
update, a read-back diff against the expected condition was empty. The current
condition is:

```text
assertion.repository_owner_id == '279365001' &&
assertion.repository_id == '1356313946' &&
assertion.ref == 'refs/heads/main' &&
assertion.event_name == 'push' &&
assertion.job_workflow_sha == 'a89fe1f090545c15cdd6987f20119327206e2c59' &&
(assertion.job_workflow_ref == 'Verjson/.github/.github/workflows/container-candidate-publish.yml@a89fe1f090545c15cdd6987f20119327206e2c59' ||
 assertion.job_workflow_ref == 'verJSON/.github/.github/workflows/container-candidate-publish.yml@a89fe1f090545c15cdd6987f20119327206e2c59')
```

Evidence after the rotation: run 39520892937 attempt 2 ran `mirror-gar`
successfully, starting at 2026-10-21T10:53:03Z. Fresh `main` pushes `5df3d3fe`
(run [39589082549](https://github.com/Verjson/verjson-ci/actions/runs/39589082549))
and `d2215fcd` (run
[39589228228](https://github.com/Verjson/verjson-ci/actions/runs/39589228228))
completed both `mirror-gar` and `candidate-manifest` successfully.

**Operational rule.** The provider condition is pinned to the publisher SHA on
purpose. Pinning the reviewed workflow bytes is the authority this ADR grants.
Every adopter publisher repin, meaning a regeneration at a new contract SHA,
MUST be preceded by a reviewed rotation of that adopter's provider to the new
SHA. If the rotation does not happen first, GAR publication fails closed for every
`main` push until it does. A push still on the old pin also fails after the
rotation, so rotate immediately before merging the regeneration and merge nothing
else in between. The rotation substitutes the SHA only, and every other
predicate stays as recorded above.
[`docs/container-candidate-migration.md`](../../container-candidate-migration.md)
has the read-only `describe`, the SHA-only `update-oidc`, and the read-back diff.
Amend this ADR with the new SHA and evidence each time the provider rotates.

**Rerun limits observed during recovery.** These do not change this decision and
are recorded so operators do not mistake them for federation failures:

- `gh run rerun --failed` cannot pass `candidate-manifest`. The `prepare` job
  derived `candidateVersion` with run attempt 1 and is not re-run. The manifest
  records the current attempt, and the validator requires
  `candidateVersion == <nextStableVersion>-rc.<runId>.<runAttempt>`
  (`scripts/container_release_manifest.py:169`). That is why attempt 2 failed
  after its `mirror-gar` passed.
- A full rerun (attempt 3) of the same commit failed `mirror-gar` with
  `registry platform is missing one unique application/spdx+json referrer`.
  The attestation jobs ran again on the unchanged digest and added a second SPDX
  referrer.

Both refusals are fail-closed. To re-prove a commit, push a new commit instead of
rerunning. Use a full rerun only when no earlier attempt of that run reached the
attestation jobs. Never use a partial rerun.
