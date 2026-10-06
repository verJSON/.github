# 0218 — Require an explicit version for release dispatch

- **Date:** 2026-10-06
- **Status:** Accepted
- **Issue:** [#1688](https://github.com/verJSON/.github/issues/1688)
- **Category:** release automation GitHub write authority (sensitive class)
- **Supersedes:** [ADR 0183](../0183-release-plan-resolves-optional-version/README.md)
- **Extends:** [ADR 0038](../0038-canonical-changelog-contract/README.md), [ADR 0060](../0060-node-release-retired/README.md)

## Context

ADR 0183 let a direct `workflow_dispatch` release omit its version and have the
verified release plan derive one from selected `NEXT/` fragments. The current
release policy requires a dispatch to state the exact version it will cut. A
blank version leaves that write decision implicit at the point of authorization.
The release proposal path already derives an exact version and supplies it to
the dispatch workflow, so this reversal does not require another proposal
authority or a new trigger.

## Decision

The canonical caller generator requires a nonblank `version` input in
`release-node`, `release-artifact`, and `release-snapshot`. Their first verify
step rejects an empty or whitespace-only version before checkout, planning, or
any release work. The read-only `release-plan` still validates the supplied
version against the selected fragments, component, prefix, source commit, and
selection receipt. It may still derive a version for proposal and preview
flows; that derivation no longer authorizes a direct release dispatch.

The existing `verify → snapshot → publish` ordering, immutable contract pin,
exact-head checks, release App boundary, and absence of merge or push release
triggers remain the release authority contract. Adopters regenerate the entire
caller family at one reviewed immutable contract commit.

## Consequences

- Operators must state the version when dispatching a release directly.
- Empty and whitespace-only dispatches fail before reading or changing the
  repository. A nonblank but invalid version fails the existing release plan.
- Release proposals continue to compute and dispatch an explicit version.
- Existing generated callers retain their old behavior until regenerated at
  this decision's implementing commit.

## Verification

The generator contract tests check all three release modes, including the
required input, the first-step blank-version guard, and continued release-plan
validation. The pinned release path is exercised in a disposable checkout
before an adopter's generated caller is updated.
