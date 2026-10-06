# 0086 — Separate Node dependency acquisition from secretless PR execution

- **Date:** 2026-08-09
- **Issue:** [Verjson/.github#680](https://github.com/Verjson/.github/issues/680)
- **Category:** package credentials / untrusted PR execution — **sensitive class**
- **Status:** Accepted

## Context

The canonical Node workflow previously requested `packages: read` on its build job and
installed private dependencies in the same job that later executed repository-controlled
build and test scripts. A caller that omitted that permission failed when the reusable
workflow started, while granting it made a package-capable job token available in the PR
execution boundary. Passing `NODE_AUTH_TOKEN` into `npm ci` in that job exposed a still
stronger cross-repository package credential.

Private consumers need approved `@verjson` contracts during validation. Running lifecycle
scripts with a package token, routing PRs to persistent capacity, or treating package names
in a PR-controlled lockfile as authorization would each collapse a credential boundary.

## Decision

Add an opt-in `secretless-pr` mode with two jobs. An acquisition job on the isolated
untrusted lane (with a GitHub-hosted fallback and no trusted-lane fallback) checks out
the PR without persisted Git credentials, accepts only an explicitly mapped package
token, and validates a version 2 or 3 package lock before network access. Every resolved
`@verjson` package must exactly match the caller's allowlist and its GitHub Packages
download URL. It rejects repository-controlled `.npmrc` files and forces npm to use a
job-created user config outside the checkout, with the token interpolated only for
`npm.pkg.github.com`. The job runs `npm ci` with lifecycle scripts, audit, and funding
requests disabled, archives only `node_modules`, and retains that artifact for one day.

The build job also uses only the isolated untrusted lane or hosted fallback, overriding
any caller runner selection. It downloads the artifact without package permission, does
not run an install, does not initialize submodules, persists no checkout credential, and
clears package, Git, cloud, and OIDC credential paths before any repository-controlled
command. Secretless mode is restricted to same-repository `pull_request` events and
rejects forks and schema submodules. It never uses `pull_request_target` to check out PR
code. The caller maps only `NODE_AUTH_TOKEN`; `secrets: inherit` is outside the documented
contract.

The existing route remains the default and keeps its authenticated install. The package
secret, not the job token, has always performed cross-repository acquisition, so the
build job no longer requests an unused package capability. Secretless callers grant
`packages: read` so the acquisition job can request it; the correction below records
why this caller permission is required even with a separate package token.

## Consequences

- PR code receives approved dependency bytes without receiving the credential used to
  acquire them.
- A dependency addition requires an explicit caller allowlist review and canonical caller
  update; lockfile names alone never grant access.
- Lifecycle-dependent packages cannot use this mode unless their published artifact is
  changed to work without install scripts. This is an intentional acquisition constraint.
- Private schema submodules require a separately designed trusted acquisition contract;
  they are not silently admitted here.
- The artifact is a same-run handoff, not a shared dependency cache, and expires after one
  day.

## Rollback

Callers can omit `secretless-pr` to use the unchanged credentialed path. Reverting the
implementation removes the opt-in mode without changing existing caller behavior.

## 2026-08-09 correction — root lock entry is not an installed dependency

[Issue #682](https://github.com/Verjson/.github/issues/682) exposed an implementation
error in the lock validator: `packages[""]` describes the repository root, so a scoped
root package has neither an installed-package path nor a registry download URL. Treating
its `name` as an acquired dependency rejected valid same-organization consumers.

The validator now ignores only that exact empty-path entry. Every other lock entry keeps
the original exact allowlist and canonical GitHub Packages URL checks, including entries
that try to hide an internal package name under a nonstandard or dot-shaped path. This
restores the decision's installed-dependency boundary; it does not broaden the allowlist.

## 2026-08-16 correction — acquisition requests package permission

[Issue #833](https://github.com/Verjson/.github/issues/833) exposed a false premise in
the caller contract. A reusable workflow's job permissions intersect with the caller's
permissions, so a caller must grant every permission requested by the called job
regardless of which secret token the job uses. Mapping `secrets.GITHUB_TOKEN` into
`NODE_AUTH_TOKEN` made the defect observable as a private-package `E403`. A prior
successful `npm ci` on the persistent runner used npm's default cross-run cache and did
not prove a fresh authenticated download; the fresh run-attempt cache correctly exposed
the missing authority.

The acquisition job now requests `packages: read`. A caller mapping its
`GITHUB_TOKEN` into `NODE_AUTH_TOKEN` must grant that permission because a reusable
workflow cannot elevate the caller token's permission ceiling. A separately issued
PAT or App token carries its own authority, but the canonical caller contract requires
`packages: read` uniformly so its GitHub-token path cannot silently depend on runner
state. The package capability remains confined to the non-executing acquisition job: exact
scope/package/URL/integrity validation still precedes network access, repository
lifecycle code still never runs there, and the build job remains credentialless with
only `contents: read`.

The acquisition cache path must not exist before the job populates it. This makes the
validated content set evidence of a fresh authenticated request rather than a hit from
persistent runner state. A denied download reports both possible boundaries without
claiming which caused it: the mapped credential must read every approved package and a
mapped `GITHUB_TOKEN` requires caller `packages: read`. There is no contents-only
`GITHUB_TOKEN` fallback because moving that token into repository-controlled execution
would weaken the boundary without granting package authority.

## 2026-10-02 amendment — compare GitHub repository identity without casing (#1682)

GitHub can report different owner or organization-login casing for the same repository. The secretless pull-request boundary now normalizes the complete head and base `owner/repository` names before comparing them. Equality still requires the same owner and repository after normalization, so forks remain rejected. This restores the existing same-repository invariant rather than widening package access. The canonical regression contract verifies the normalization in `node-ci-secretless.test.sh`; the CI reproduction and linked PR are recorded in [issue #1682](https://github.com/verJSON/.github/issues/1682).

## 2026-10-06 amendment — accept registry tarball scope casing (#1694)

GitHub Packages can issue a tarball URL with different ASCII casing from the approved lowercase package name, as reproduced by [issue #1694](https://github.com/Verjson/.github/issues/1694). The acquisition validator compares the URL's ASCII scope and package identity after lowercasing against the exact caller approval and lock entry. It still rejects non-ASCII identities, unapproved packages or scopes, mismatched lock names, malformed URLs, and invalid or conflicting integrity. The registry-issued URL and integrity remain unchanged. The conformance regression runs the embedded validator against both accepted and rejected lockfiles.
