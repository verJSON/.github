---
date: 2026-10-05
issue: 1689
impact: patch
title: Add keyless Cosign provenance for container candidates
---

Replaces plan-incompatible GitHub Artifact Attestations in the canonical container publisher with keyless Cosign evidence, independently verified source/caller/publisher/contract claims, and a fail-closed GAR evidence read-back gate. Candidate eligibility remains disabled until genuine private reusable-workflow identity and GAR preservation evidence pass. The release finalization step now receives the same pinned contract SHA used to verify its Cosign signature.
