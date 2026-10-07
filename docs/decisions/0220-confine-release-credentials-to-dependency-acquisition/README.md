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

## Consequences

Installed dependencies remain available to release verification without a registry credential. A lifecycle or verification hook that tries to fetch additional private packages must be redesigned; it cannot reuse the acquisition credential. Dependency lifecycle scripts still run after acquisition, subject to the package manager's existing script-approval policy.
