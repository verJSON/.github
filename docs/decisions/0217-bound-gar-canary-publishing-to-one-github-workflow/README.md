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
contract commit `f5ffab3c4164ee85dca4ba698a03a9509edfed58`. Check both the
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
assertion.job_workflow_sha == 'f5ffab3c4164ee85dca4ba698a03a9509edfed58' &&
(assertion.job_workflow_ref == 'Verjson/.github/.github/workflows/container-candidate-publish.yml@f5ffab3c4164ee85dca4ba698a03a9509edfed58' ||
 assertion.job_workflow_ref == 'verJSON/.github/.github/workflows/container-candidate-publish.yml@f5ffab3c4164ee85dca4ba698a03a9509edfed58')
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
