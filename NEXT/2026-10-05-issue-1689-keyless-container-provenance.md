---
date: 2026-10-05
issue: 1689
impact: patch
title: Add keyless Cosign provenance for container candidates
---

Replaces plan-incompatible GitHub Artifact Attestations in the canonical container publisher with keyless Cosign evidence, independently verified source/caller/publisher/contract claims, and a fail-closed GAR evidence read-back gate. Derived image read-back checks the pinned base digest against BuildKit dependencies; the mirror verifies index provenance and each platform's SPDX referrer at their actual OCI subjects, with the registry regressions enforced in CI. Pinned publisher and release helpers carry their required imports, and the reusable canary pins their current digests. Candidate eligibility remains disabled until genuine private reusable-workflow identity and GAR preservation evidence pass.

The bounded canary trust decision scopes GitHub OIDC federation to one private
repository, protected `main`, the proposed canonical publisher revision, and
repository-level GAR writer access; external adopters provide their own
reviewed identities.
The live private canary also exposed an OIDC request-host mismatch; the token
fetcher now accepts GitHub Actions request hosts and refuses redirects before
sending the bearer credential.
The bounded canary identity tracks the reviewed publisher revision.
An authenticated, non-secret runner probe also identified the regional
`run-actions` request-host family, which the token fetcher now accepts.
The GAR provider condition now pins the updated helper commit.
The third live canary exposed Buildx's single-platform provenance shape;
the verifier accepts that exact shape after checking the published OCI index
against the reviewed platform before requesting an OIDC token.
The bounded canary identity follows the reviewed BuildKit-shape fix commit.
ORAS attachments now use validated relative file paths so the signed
provenance and SPDX bundles can be stored without disabling path validation.
The bounded GAR canary identity is repinned to that reviewed helper commit.
ORAS attachment progress no longer enters the machine-readable Cosign receipt;
the signed bundle and exported manifest remain the evidence inputs.
ORAS pull progress is also isolated from JSON verification receipts while
downloaded referrer files remain the verified evidence inputs.
The bounded GAR canary identity now follows that reviewed receipt fix commit.
The SPDX export accepts Buildx's unwrapped document only when the validated
OCI index has exactly the requested single platform; multi-platform exports
still require an exact platform key.
The bounded GAR canary identity follows the reviewed SPDX inventory fix commit.
The GAR mirror now passes authentication to Skopeo's `inspect` subcommand;
its prior global-flag order failed before registry access. A read-only GAR
probe returned `MANIFEST_UNKNOWN` for the new candidate tag; the helper
classifies that as absence while authorization and missing-repository errors
remain fatal, with bounded diagnostics that never echo credentials.
The bounded GAR canary identity follows the reviewed Skopeo inspection fix.
GAR Docker login now receives the action's generated service-account access
token rather than its intermediate federation token; a workflow check guards
the output selection.
GAR may surface the BuildKit attestation descriptor already present in the
validated OCI index as an additional platform referrer. Mirror read-back
accepts that exact descriptor while requiring the same SPDX and Cosign
referrers and rejecting unrelated additions.
