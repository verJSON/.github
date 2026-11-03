# 0249 — Temporarily hold autonomous terminal merges

- **Date:** 2026-10-29
- **Status:** Accepted
- **Issue:** [#1945](https://github.com/verJSON/.github/issues/1945)
- **Category:** merge authorization and protected-branch authority

## Context

The existing terminal promotion uses an App with bypass authority. Removing the
`--admin` flag alone cannot establish that a merge respects the repository's
rulesets. Exact-head and local base checks also do not bind the eventual merge
to an unchanged base. Candidate admission and independently trusted agent
identity remain unresolved.

## Decision

Temporarily remove token minting and terminal merge from this canonical workflow;
its terminal helper fails closed. A promotion reports failure and cannot report
or clean up as a successful merge. Restore autonomous delivery only through a
separately reviewed decision establishing non-bypass admission and its evidence.

This change affects callers that adopt a contract revision containing it. It
does not alter previously pinned callers, existing App installations or bypass
permissions, ordinary GitHub merges, or live rulesets. It does not establish
candidate admission or claim that a merge queue or independent agent identity
is deployed. Deployment requires independently verified caller adoption and
ruleset and identity evidence.
