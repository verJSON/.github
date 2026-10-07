# 0220 — Confine release credentials to dependency acquisition

- **Date:** 2026-10-07
- **Status:** Accepted
- **Issue:** [verJSON/.github#1712](https://github.com/verJSON/.github/issues/1712)
- **Supersedes:** The verification-suite credential grant in the 2026-08-07 refinement [ADR 0062](../0062-release-verifies-before-it-tags/README.md).

## Context

Generated release-node, release-artifact, and release-snapshot workflows installed private packages with NODE_AUTH_TOKEN while npm lifecycle scripts were enabled. They then passed the same package credential to repository-controlled release-verification code. Their checkout also persisted GITHUB_TOKEN in .git/config for later tag lookup. A dependency lifecycle hook or verification hook could therefore read credentials needed only to download private packages.

ADR 0062's verification refinement gave the test hook package access because it might query the registry itself. The dependency tree is already installed before verification, so that access is not needed to run the installed test suite. Restart-safe tag lookup still needs read access to the repository, but it does not require persisting checkout credentials into the workspace.

## Decision

All generated Node release callers use the same short-lived credential boundary:

1. Check out the release tree with persist-credentials disabled.
2. Give GITHUB_TOKEN only to the restart-safe state-resolution step. That step uses process-scoped Git configuration for remote reads and does not write the token to Git configuration. Its complete shell body is pinned by the canonical generator's structural contract.
3. Give NODE_AUTH_TOKEN only to the verification-job step that runs npm ci --ignore-scripts. Then run npm rebuild with NODE_AUTH_TOKEN explicitly empty.
4. Run package preparation, version stamping, and release verification with NODE_AUTH_TOKEN empty.
5. Keep existing publication credential mappings unchanged. Publishing remains a separate job with the credential required by its existing workflow contract.

The credential-boundary test generates all three release modes and performs private registry acquisition against a local token-checking server. It proves install hooks did not run during acquisition and run only after the token is cleared. The structural contract rejects workflow mutations that expose credentials to lifecycle scripts, verification, inherited process environments, or persisted Git configuration.

## Implementation refinement (2026-10-07; issue #1717)

The clean verification process uses the trusted runner PATH and job-scoped changelog cache path captured as step outputs before dependency code runs; ambient GITHUB_ENV changes from lifecycle hooks cannot redirect verification.

The canonical generator contract also pins the verification step's selected-version condition, blocking behavior, command body, and approved environments across all three release modes. It validates the canonical selection-contract checkout's repository, path, immutable ref, and `persist-credentials: false` before normalizing the ref for the step digest. This keeps the guard stable as the contract advances without allowing a moving or altered checkout to pass. Regression cases reject skipped or nonblocking verification, changed checkout inputs, and credential-environment leakage.

This refinement makes the existing credential boundary fail closed in generated callers; it does not grant credentials to additional steps or change release authority.

Package-directory provenance applies the generator's normalized relative-path and duplicate checks. For node-release callers, the forwarded package list must agree with the validated provenance and version-stamp command. The emitted contract validates the post-install version-stamp step's condition, environment, and full command separately from the pre-install digest, so a changed package set or an added command cannot hide behind the digest's installation boundary. The generated contract test pins expected package directories by workflow path. Additional release callers require an explicit path-to-package mapping in contract-test generation, and mixed additive/exact selection flags are rejected. Distinct release callers may select distinct valid package sets when each is pinned explicitly. Executable regression tests run the emitted `.npmrc` guard and verification command; they prove a workspace config blocks npm and the repository verification hook observes an empty package token.

## Consequences

Installed dependencies remain available to release verification without a registry credential. A lifecycle or verification hook that tries to fetch additional private packages must be redesigned; it cannot reuse the acquisition credential. Dependency lifecycle scripts still run after acquisition, subject to the package manager's existing script-approval policy.
