# 0216 — Keyless container provenance is verified per registry

- **Date:** 2026-10-05
- **Status:** Accepted; candidate eligibility is gated on private identity and GAR preservation evidence
- **Issue:** [verJSON/.github#1689](https://github.com/verJSON/.github/issues/1689)
- **Related consumer issue:** [terptechpub/self-publish-ai-app#1352](https://github.com/terptechpub/self-publish-ai-app/issues/1352)
- **Supersedes in part:** [ADR 0077](../0077-private-repository-gate-restoration/README.md)'s residual concern for private reusable-workflow signing; [ADR 0211](../0211-signed-merge-gate-provenance-binds-the-canonical-required-workflow/README.md) remains scoped to merge-gate provenance
- **Category:** artifact provenance and publication identity (sensitive class)

## Context

The private organization cannot persist GitHub Artifact Attestations on its current plan. The container-candidate publisher therefore fails after pushing an image but before completing its signed-candidate path. ADR 0077 records why a private required-workflow identity capture was necessary before Sigstore signing could be trusted; ADR 0211 captures that required-workflow shape, not a reusable-workflow publisher calling from a private consumer.

The container release path also copies images from GHCR to GAR. Copying image bytes with the same digest does not prove that the original signatures and attestations remain retrievable from GAR. Signing caller-supplied manifest assertions would create a signature without establishing where the image came from or which reusable publisher produced it.

## Decision

Use keyless Cosign signing through GitHub Actions OIDC, Sigstore Fulcio, and the public Rekor log for container provenance and SBOM evidence. The canonical versioned reusable publisher controls the build and signs only after validating BuildKit evidence for the produced image digest, reviewed platforms, source commit, and required materials. It obtains workflow claims from the GitHub OIDC token used for signing; caller inputs and candidate manifests are not authoritative provenance sources.

The consumer independently authorizes source repository/ref/commit, caller repository/workflow/revision, reusable publisher workflow, and immutable contract revision. It validates those claims and the required BuildKit facts together in one Cosign-verified provenance attestation whose subject is the exact image digest. A separately signed candidate manifest may link evidence identities but cannot replace an image's own provenance verification. SBOM evidence is signed and verified under the same publisher and subject policy.

The image and original evidence must survive transfer to GAR without changing the image digest. Before a candidate becomes eligible for promotion, the consumer retrieves the original evidence from GAR and verifies it there under the same policy. Missing evidence, digest changes, incomplete identity claims, or ambiguous evidence fail closed. There is no unsigned fallback.

All reusable publication behavior and contract tests originate in this repository. Consumer callers are generated at one immutable contract SHA; they are never hand-edited.

## Evidence required before rollout

The contract must remain unavailable for candidate eligibility until a genuine private-consumer run proves the reusable-workflow certificate and statement claim mapping and a real GHCR-to-GAR copy proves the original Cosign evidence remains retrievable and verifiable at the unchanged digest. Run the canary against the proposed immutable contract commit before merge. If the merged contract SHA differs, repeat the canary against that SHA. Preserve appropriately redacted evidence; do not fabricate certificate fixtures.

These gates do not retire the `dev` branch or authorize a production tag. Those actions remain subject to the app's ADR 0053 retirement and release conditions.

## Consequences

- Private candidate publishing no longer depends on GitHub Artifact Attestations billing eligibility.
- Public Rekor records expose accepted signer, workflow, contract, source, and image-digest metadata; predicates must contain no credentials, private build arguments, or secret values.
- GAR evidence preservation is an explicit promotion boundary. The provenance referrer belongs to the image index; each signed SPDX referrer belongs to its platform manifest. Mirror read-back checks the index and every reviewed platform subject separately. OCI referrer copying must be validated against the real registries; support or a CLI's preview status alone is not proof.
- The reusable-workflow identity shape is a rollout gate, distinct from ADR 0211's required-workflow capture.

## Amendment — 2026-10-17: a same-repository publisher is bound to the source commit

**Evidence.** The contract canary `container-candidate-reusable-contract.yml`, dispatched on `main` at `9f62840e`, failed in run [39060824097](https://github.com/verJSON/.github/actions/runs/39060824097) at "Sign controlled BuildKit provenance with Cosign" with `GitHub OIDC publisher workflow is not immutably pinned`. Image build, push, and the disk gate had passed. The canary calls the publisher with `uses: ./`, so GitHub ran `verJSON/.github/.github/workflows/container-candidate-publish.yml@refs/heads/main (9f62840e…)`, and its OIDC `job_workflow_ref` named a branch rather than `@<job_workflow_sha>`. Since #1690 introduced that check, no canary dispatch has reached signing; the only green dispatch since was a diagnostic branch whose publish job was skipped. Adopters call `Verjson/.github/...@<sha>` and were unaffected.

**Rejected alternative.** Repinning the canary to an older contract SHA would make it exercise a stale publisher instead of the commit under test, which is the canary's purpose.

**Decision.** The producer (`build_cosign_provenance_statement`) and the statement verifier (`validate_cosign_provenance`) share one predicate, `require_immutable_publisher` in `scripts/container_candidate_retry.py`. It parses `job_workflow_ref` strictly as `<owner>/<repository>/.github/workflows/<file>.y(a)ml@<ref>` and accepts exactly two shapes:

1. **Pinned** — the ref equals the 40-hex `job_workflow_sha`. This is unchanged for adopters.
2. **Same-repository** — the workflow path is exactly `.github/workflows/container-candidate-publish.yml`, the parsed repository path equals the OIDC `repository` claim exactly (case-sensitive; GitHub emits the canonical path in both), the ref equals the OIDC `ref`, and `job_workflow_sha == workflow_sha == sha`, all 40-hex. Because a `uses: ./` call runs the publisher from the caller's own commit, this binds the publisher bytes to the immutable source commit, which `repository_id` and `sha` already identify.

Everything else is rejected: cross-repository branch or tag refs, any other same-repository workflow, a same-repository ref other than the source ref, any SHA disagreement, lookalike or case-variant repository paths, unparseable refs, malformed SHAs, and missing claims.

The publisher workflow authorizes one identity per run in its `prepare` job (`publisher-workflow-ref`). When `contract-ref` equals both `job.workflow_sha` and `github.sha`, it authorizes `$GITHUB_REPOSITORY/.github/workflows/container-candidate-publish.yml@$GITHUB_REF`; otherwise it authorizes the pinned `Verjson/.github/...@<contract-ref>`. Both retry verifiers, the GAR mirror verifier, and the candidate manifest read that output, so a wrong selection fails closed: the OIDC token must still equal the authorized identity. The candidate-manifest validator accepts the same-repository signer `<source repository>/.github/workflows/container-candidate-publish.yml@refs/heads/main` only when the source repository is the contract repository (`verjson/.github` case-insensitively; its exact canonical spelling must still match the signer). An adopter's local publisher copy is rejected. Every other signer must full-match `Verjson/.github/.github/workflows/container-candidate-publish.yml@<lowercase 40-hex>`, which is exactly what the publisher emits for a non-canary run. The canary fixture reviews that identity as its `builderIdentity`.

**Consumers that deliberately reject canary-signed candidates.** `container-release.yml` stops a canary candidate three times, independently and in order. First, it admits only candidate runs whose `workflow_run.head_branch` is `main` and whose run `event` is `push`; the canary is a `workflow_dispatch`. Second, release attestation verification (`container_attestation_verify.py`) requires each image's `builderIdentity` to equal exactly `Verjson/.github/.github/workflows/container-candidate-publish.yml@<release contract-ref>` before anything else is trusted. Third, promotion (`container_release_promotion.py`) splits the signer on its first `@` and, since this amendment, requires exactly `Verjson/.github/.github/workflows/container-candidate-publish.yml` before it and a 40-hex contract commit after it. Before this, a `Verjson/.github/...@refs/heads/main` candidate passed promotion, the real canary (`verJSON/.github/...`) failed it only because of case, and a `...@refs/heads/main@<sha>` signer passed both the candidate validator's prefix check and promotion's last-`@` split. Promotion does not also compare the candidate contract with the state's `release.workflow.contractCommit`. Attestation verification already requires the candidate contract to equal the current `CONTRACT_REF`, so for a new tag that comparison would be redundant, not wrong. The only case where the two could differ is resuming a tag cut before the exact pin existed, whose recorded `contractCommit` predates it. Canary candidates are also `promotionEligible: false` and use a fixture-only image repository. Tests record each rejection.

**Residual risk.** Any workflow in the source repository at the source ref can now produce an accepted provenance statement through its own local publisher copy. That repository's branch protection, not a contract pin, is the trust root for that statement. Every consumer that authorizes a publisher still names the identity it trusts, and every release-path consumer requires the pinned contract. GitHub's `job_workflow_ref` format and its canonical repository casing are external facts that were observed in run 39060824097 rather than guaranteed. A live canary pass is verifiable only after merge, because the canary dispatches from `main`.
