# 0215 — Verify OCI candidate registry destinations

- **Date:** 2026-10-02
- **Status:** Accepted
- **Issue:** [#1667](https://github.com/Verjson/.github/issues/1667)
- **Category:** CI authority and artifact publication (sensitive class)
- **Related:** [verjson-ci#30](https://github.com/Verjson/verjson-ci/issues/30)

## Context

The candidate publisher built and attested OCI images only in GHCR. Adopters using
another runtime registry had to maintain a second publisher, while a hand-written
copy job could silently change a multi-platform index digest or leave one platform
missing. A candidate manifest also needed to say how long each immutable digest was
expected to remain available so release promotion would not rebuild or substitute an
expired candidate.

## Decision

GHCR remains the canonical build, provenance, and promotion source. An adopter may
add a narrowly configured destination with provider-specific OIDC identity. The
publisher builds once, copies every platform while preserving the manifest digest,
and fails unless each configured destination reads back the exact candidate digest.
The candidate manifest records the verified digest, the source run start, and a
per-destination expiry. Retention is bounded to 88 days. Stable promotion checks the
canonical GHCR expiry and fails closed after it; it never rebuilds or selects a
different candidate digest to recover an expired image.

Only destination adapters with complete multi-platform support and exact digest
read-back may be enabled. GAR is supported by this contract. Nexus remains disabled
until [verjson-ci#30](https://github.com/Verjson/verjson-ci/issues/30) completes its
OIDC multi-platform index publisher and live acceptance.

## Consequences

- GHCR-only adopters keep the existing behavior by omitting `registryDestinations`.
- A GAR destination requires a reviewed Workload Identity provider, service account,
  Docker repository namespace, and explicit retention limit.
- Missing destinations, authorization failures, conflicts, copy errors, digest
  mismatches, and expired candidate records stop publication or promotion.
- Registry bytes may remain after their declared expiry, but automation treats them
  as unavailable. No cleanup job is required for correctness.
- Destination receipts advance candidate and release manifests to schema v3. Existing
  v2 candidates must be rebuilt; the validator does not infer missing receipts or expiry.
- The published candidate and release JSON Schemas continue to validate immutable v2
  records for readers. Candidate v3 added publication timestamps and destination receipts;
  release promotion rejects candidates that lack the evidence required by its current
  contract.

## Amendment — 2026-10-09 ([#1726](https://github.com/Verjson/.github/issues/1726))

Candidate manifests advance to schema v4 so a registry receipt records the exact
provenance evidence observed at readback. Every destination timestamp is checked
against candidate publication and expiry independently. A GAR receipt records the
index referrers plus referrers for each platform subject digest. Validation requires
the index Sigstore bundle referrer to match the image provenance referrer digest,
and each platform SPDX referrer to match that platform's SBOM attestation referrer
digest. Candidate schema v2 and v3 remain readable as historical records, but the
promotion validator requires v4 and directs older candidates to be rebuilt.

The TTP candidate consumer case is tracked in
[self-publish-ai-app#1352](https://github.com/terptechpub/self-publish-ai-app/issues/1352);
its generated caller must pin a merged immutable contract revision before acceptance.

Stable promotion retains the exact signed candidate artifact ZIP as
`candidate-manifest.zip` on the GitHub Release. The release manifest's
`candidateManifestDigest` remains the ZIP digest, so the released asset preserves the
complete GAR referrer inventory after the Actions artifact expires. This does not extend
candidate registry retention or permit promotion after a destination expires.
