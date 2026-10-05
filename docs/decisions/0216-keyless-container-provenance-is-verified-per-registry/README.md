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
- GAR evidence preservation is an explicit promotion boundary. OCI referrer copying must be validated against the real registries; support or a CLI's preview status alone is not proof.
- The reusable-workflow identity shape is a rollout gate, distinct from ADR 0211's required-workflow capture.
