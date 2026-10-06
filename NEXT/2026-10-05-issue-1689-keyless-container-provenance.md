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
