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

## Implementation refinement (2026-10-09; issue #1724)

The canonical `node-release` publication workflow applies the same boundary to private dependency acquisition: one Actions step runs `npm ci --ignore-scripts` with `NODE_AUTH_TOKEN`, then exits; a separate step runs `npm ci --prefer-offline` without that secret so npm replays the locked install from its cache and runs the normal dependency and root lifecycle. Separate steps ensure lifecycle code cannot read the earlier step's token from an ancestor process environment. The offline contract test compares hook counts and per-package order with a normal install, inspects lifecycle ancestor environments, and covers empty dependency trees. Publication credentials and provenance behavior stay unchanged.

Package-directory provenance applies the generator's normalized relative-path and duplicate checks. For node-release callers, the forwarded package list must agree with the validated provenance and version-stamp command. The emitted contract validates the post-install version-stamp step's condition, environment, and full command separately from the pre-install digest, so a changed package set or an added command cannot hide behind the digest's installation boundary. The generated contract test pins expected package directories by workflow path. Additional release callers require an explicit path-to-package mapping in contract-test generation, and mixed additive/exact selection flags are rejected. Distinct release callers may select distinct valid package sets when each is pinned explicitly. Executable regression tests run the emitted `.npmrc` guard and verification command; they prove a workspace config blocks npm and the repository verification hook observes an empty package token.

The reusable `node-release` workflow now runs dependency acquisition, lifecycle scripts, package preparation, version stamping, builds, and packing in a preparation job with only repository and package read permissions. The private dependency token remains limited to the `npm ci --ignore-scripts` step; lifecycle scripts run in a later step without it. The legacy runner input remains accepted for caller compatibility but is ignored; ADR 0221 records the fresh-hosted boundary for all three jobs.

Package archives cross a one-day Actions artifact boundary to a fresh `ubuntu-24.04` publisher. The publisher reads expected package names and dispatched versions from the checked-out release tag, then runs the artifact validator from the immutable `contract-ref` checkout. Validation rejects unexpected files, symlinks, unsafe archive paths, identity/version mismatches, and SHA-512 digest mismatches before any package is published. The publisher runs no lifecycle, build, preparation, or pack scripts; `npm publish` receives only validated tarballs with scripts disabled and an explicit GitHub Packages registry. Every Node release job checks `runner.environment` as its first step and stops unless GitHub reports `github-hosted`; the scalar `ubuntu-24.04` selector alone is not proof of runner type because self-hosted runners may carry custom labels. The publisher and retention jobs still keep their existing job-scoped permissions and artifact validation. The uploaded artifact ID is carried as a prepare-job output and reused on release-only reruns, so those reruns do not derive a different artifact name from the new attempt number. The upload uses compression level zero because the package tarballs are already compressed, keeping the artifact API size close to the bytes the runner will extract. Before downloading, the publisher checks that size against a 2 GiB limit with actions-read permission. The validator also caps aggregate compressed and expanded archive bytes, each PAX and GNU metadata record, and cumulative PAX and GNU metadata to bound parser memory. GNU sparse archive formats are rejected before tarfile builds extent lists.

The build artifact is treated as untrusted transport data. Validation recomputes each archive's SHA-512 and requires it to match the digest in the transferred artifact manifest; it verifies package identity and version against the expected package list derived from the immutable tag. The digest check detects a mismatch between the manifest and archive, but is not an independent attestation of build provenance. The build process remains responsible for package contents as before. This refinement does not add a provenance attestation or change the existing publication/release-note behavior.

## Consequences

Installed dependencies remain available to release preparation and verification without a registry credential. A lifecycle hook that tries to fetch additional private packages must be redesigned; it cannot reuse the acquisition credential. Dependency lifecycle scripts run during the read-only preparation job under the package manager's existing script-approval policy. Every Node release job requires a fresh GitHub-hosted runner; callers that previously relied on their `runner` input must use hosted GitHub Actions capacity for the full Node release workflow.

## 2026-10-09 security review refinement — project npm config and caches (#1724)

The token-bearing `npm ci --ignore-scripts` now quarantines the release tag's root `.npmrc` for the duration of that command, restoring it on success and failure. A tag-controlled registry or auth directive therefore cannot redirect `NODE_AUTH_TOKEN`. The workflow also derives a cache-enabled output from the cache input, lockfile match, and only the boolean presence of the private dependency token; `setup-node` persistent caching and its cleanup are disabled when that token is present. Before setup-node runs, the reusable workflow writes a versioned namespace marker into the caller's checked-out workspace and removes it immediately after setup-node calculates the exact npm cache key. Tokenless runs therefore cannot restore legacy entries created before this policy. Contract coverage executes the actual marker creation and cleanup steps against a caller-workspace fixture, including conflicts and partial failures. Private dependency tarballs are not retained in cross-run Actions caches.

## 2026-10-09 refinement — separate npm configuration files (#1725)

The generated credentialless verification environment must assign npm's user and global configuration namespaces to distinct, controlled files. npm rejects loading the same `/dev/null` path for both namespaces before it runs the verification commands. Each generated release-node, release-artifact, and release-snapshot verification step now creates separate empty files under its private temporary home, applies a restrictive umask, and removes that home at step exit. A regression executes the generated verification command with real npm and a fixture build script in all three modes; it checks the two files are distinct, empty, private to the temporary home, and cleaned up afterward. These configuration files preserve the existing credential boundary and release authority.
