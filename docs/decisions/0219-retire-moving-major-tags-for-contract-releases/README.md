# 0219 — Retire moving major tags for contract releases

- **Date:** 2026-10-06
- **Status:** Accepted
- **Issue:** [verJSON/.github#1644](https://github.com/verJSON/.github/issues/1644)
- **Supersedes:** The moving major tag policy in [ADR 0014](../0014-reusable-workflow-versioning/README.md); release identity remains governed by [ADR 0191](../0191-contract-releases-are-the-unit-of-adoption/README.md).

## Context

The `tag-major` workflow cannot move `v3` when the release commit adds or changes workflow files: its `GITHUB_TOKEN` lacks the `workflows` permission. The `v3` tag for `v3.0.0` needed a manual push. Granting a new App token that permission would preserve a mutable release alias that contract adopters do not use.

An organization code search on 2026-10-06 found no workflow caller of `Verjson/.github/.github/workflows/*@v3`. Existing callers of the older `@v2` alias do exist; their tag remains available at its current target, but it will not receive future updates.

## Decision

Publish immutable `vX.Y.Z` release tags and have contract adopters declare `contract_version` and pin the corresponding commit SHA. Do not create or move `vX` aliases for new releases. Retain existing `vX` refs as static compatibility references; do not delete them or silently repoint them. Remove the `tag-major` workflow and its release-event trigger.

Consumers still using a moving alias must migrate through reviewed pull requests to a supported release commit. They cannot rely on the alias to receive compatible fixes.

## Consequences

Every update to a caller's release pin is explicit and reviewable. Legacy `@v2` callers continue to run at the existing tag target until migrated. The release path needs no App credential with workflow-write authority solely for tag movement.
