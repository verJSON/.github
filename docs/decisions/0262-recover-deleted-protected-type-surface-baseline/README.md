# 0262 — Recover a deleted protected type-surface baseline through a declaration-only PR

- **Date:** 2026-11-13
- **Status:** Proposed for review with #2066
- **Issue:** [Verjson/.github#2066](https://github.com/Verjson/.github/issues/2066)
- **Supersedes:** [ADR 0205](../0205-protected-consumer-type-surface-baseline/README.md) only when its base version is absent from the authenticated registry
- **Category:** required-workflow package credential and baseline policy (sensitive class)

## Context

ADR 0205 reads the protected declaration only from the PR base. Organization
retention keeps three stable package versions. If retention removes the version
declared on `main`, every type-surface check fails, including a PR that proposes
a current declaration. Verjson/authz#207 reproduces this with deleted 0.11.1
and published 0.13.0. Restoring a deleted version, raising retention, or an
owner admin merge is not the recovery contract.

## Decision

The PR base remains the default declaration source. The protected acquisition
job queries the approved package's version list with its existing package
credential, after pinning Node and rejecting consumer npm configuration. A
fresh job-scoped npm cache and configuration live under `RUNNER_TEMP` and are
removed after the query. A registry error, unreadable response, or unavailable
package fails closed. If
the base version is still present, the job never reads the head declaration.

If the stable base version is absent, recovery is allowed only when the
authenticated same-repository PR still has the admitted exact head and base,
and its entire changed-file list contains exactly a modified protected
declaration and one added `NEXT/` entry. The job fetches the declaration at
that immutable head SHA through the GitHub contents API. Package and script
must remain the protected caller's approved values. The proposed version must
be a strictly newer stable semantic version in the authenticated registry
list. The job rereads PR identity before issuing the receipt. Any other
change, prerelease, missing replacement, or moved head or base is refused.

The recovery receipt has schema version 2. It binds the base SHA, original
version and declaration digest, admitted head SHA, replacement declaration
digest, package, script, exact version, and verified artifact. The existing
schema version 1 and base-only path remain valid. The credentialless transfer
checks the recovery fields against the job outputs and admitted head; candidate
code still receives no package credential. Ordinary requests retain exact
`CI_SECRETLESS_PACKAGE_POLICY` range membership. For a version 2 recovery
receipt, the protected policy must still authorize the package, scope, and
the **old exact version**. It may authorize the newer replacement through the
receipt even before the policy variable is updated. The replacement remains
subject to exact registry artifact provenance and the credentialless
compatibility test. No general range or package policy override is added.

## Consequences

This narrow exception lets a declaration repair PR pass without letting a
feature PR choose its own compatibility baseline. It adds one authenticated
registry metadata query to protected baseline runs. It temporarily accepts a
newer released version outside the old policy's exact version list; that
trusts the organization's package release authorization for this one repair
PR and must be examined before wider rollout. The recovery receipt
describes a proposed declaration; it does not claim that declaration is on
the protected default branch. A consumer receives the fix only after its
generated caller is repinned to the reviewed canonical commit.

No release is restored and no retention setting changes. Once the repair
merges, normal base-only validation resumes. Rollback holds recovery and
requires a separate safe path for a deleted base version; it must not fall
back to a floating version or bypass the required check.
