# 0205 — Resolve protected consumer type-surface baselines from the PR base

- **Date:** 2026-09-23
- **Issue:** [Verjson/.github#1575](https://github.com/Verjson/.github/issues/1575)
- **Category:** package credentials / required-workflow provenance — **sensitive class**
- **Status:** Accepted
- **Recovery exception:** [ADR 0262](../0262-recover-deleted-protected-type-surface-baseline/README.md) supersedes the base-only rule only after retention removes the declared stable release.

## Context

The required type-surface lane previously kept a released compatibility version in
the organization workflow and a consumer-owned Git SHA in an auxiliary pin file.
Those two selections drifted independently: Authn had released `@verjson/authn`
3.0.0 while the required lane still requested 2.0.0. A pull request could also
change a workspace copy or a proposed declaration without proving that the value
was protected before the pull request ran.

## Decision

The organization-owned protected Node workflow remains the sole owner of credential
isolation, `CI_SECRETLESS_PACKAGE_POLICY` authorization, strict validation, registry
resolution, and the transferred evidence receipt. A consumer's protected default
branch owns only `.github/ci/type-surface-baseline.json`, with exactly `package`,
`version`, and `script`. The version is an exact stable released semantic version;
prereleases require an explicit protected workflow authorization. The declaration
contains no Git SHA and never uses `latest`.

For a pull request, the protected lane performs one authenticated pull-request API
lookup for the immutable base commit, then fetches only the declaration contents at
that exact SHA. It never reads the declaration from the head, merge tree, workspace,
fork, or a later default-branch ref. The declaration's package and script must match
the protected caller's approved values. Its exact version becomes a one-version
compatibility request, remains a subset of `CI_SECRETLESS_PACKAGE_POLICY`, and must
resolve to a readable exact GitHub Packages artifact.

The receipt binds repository, declaration path, base SHA, declaration SHA-256,
package, version, script, registry tarball URL, SHA-512 integrity, and artifact
digest. The credentialless transfer revalidates that binding and rejects missing,
duplicate, malformed, unauthorized, unavailable, or tampered evidence. Subsequent
consumers adopt the generated protected caller, set the expected package/script in
the organization-owned workflow, add the declaration, and authorize the exact
released version in their protected package policy.

## Consequences

- A release-aligned baseline update is a small protected consumer commit; the
  organization contract does not change for every package release.
- A declaration change in a pull request takes effect only after it lands on the
  protected default branch, because the run reads the base commit.
- Registry retention and policy drift fail the lane closed rather than silently
  comparing against source, `latest`, or an unavailable artifact.
- The protected caller must remain pinned to an immutable organization workflow
  commit and must not pass package credentials to candidate execution.

## Adoption

Subsequent `@verjson/*` packages adopt this contract by adding the exact
three-field declaration on protected `main`, adding the exact package and released
version to the protected compatibility policy, and generating the thin protected
caller from the package's checked-in generator. The generated caller must pin the
organization workflow to one reviewed immutable commit, pass the authenticated
pull-request identity and declaration inputs, and keep the credentialless execution
plan fixed. Run the generator's contract tests, the protected-baseline adversarial
tests, and the package's normal build, type-surface, changelog, and security checks
before opening the adoption PR; never hand-edit a generated workflow or substitute a
PR-head, merge-tree, fork, workspace, or floating-main baseline.

## Rollback

Remove the protected declaration input from an adopter only after restoring an
equivalent protected type-surface contract. Reintroducing a consumer-managed SHA,
floating `latest`, workspace declaration reads, or credentialed candidate execution
is not an acceptable rollback.
