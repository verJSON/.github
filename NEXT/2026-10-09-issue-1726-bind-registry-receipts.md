---
date: 2026-10-09
issue: 1726
impact: major
title: Bind candidate receipts to observed provenance evidence
---

Candidate manifest v4 binds GAR index provenance and per-platform SBOM referrers to registry readback, and checks each destination timestamp independently. Historical v2 and v3 candidates remain readable, while v3 rejects v4-only receipt fields; either version must be rebuilt before promotion.

Promotion preserves the candidate manifest digest and projects each destination receipt to the fields supported by the current release schema; the candidate digest continues to bind the complete GAR evidence-referrer records.

Stable promotion retains the exact signed candidate artifact ZIP as `candidate-manifest.zip` on the GitHub Release and verifies that asset on resume, keeping the evidence inventory recoverable beyond the Actions artifact retention window. The pre-credential reconciliation hook is rejected if it changes any pre-existing untracked or ignored release input, including parent-directory modes, protecting the manifest, candidate archive, and verification receipts before signing and publication. Pinned index entries and flags are fingerprinted. The hook runs without privilege escalation in a Bubblewrap namespace that omits runner home/temp directories and uses a private `/run`, so it cannot queue a same-user systemd service to modify the release engine after reconciliation. Hook descendants are terminated before the release-token step, which independently compares the engine with the immutable contract commit.

The GAR receipt records the referrers observed for its image index and each platform subject digest. Validation compares those digests with the corresponding provenance and SBOM attestation records. [Issue #1726](https://github.com/Verjson/.github/issues/1726) tracks the TTP consumer acceptance case in [self-publish-ai-app#1352](https://github.com/terptechpub/self-publish-ai-app/issues/1352); [ADR 0215](../docs/decisions/0215-verified-oci-candidate-registry-destinations/README.md) records the contract.

Release-hook filesystem isolation tests run on the explicit hosted Ubuntu Actions job that provisions Bubblewrap, rather than persistent fastlane runners that may not have it installed.
