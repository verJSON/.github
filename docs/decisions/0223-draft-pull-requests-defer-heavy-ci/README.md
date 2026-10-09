# 0223 — Draft pull requests defer heavy CI and keep fast checks

- **Date:** 2026-10-09
- **Status:** Accepted
- **Issue:** [verJSON/.github#1739](https://github.com/verJSON/.github/issues/1739)
- **Category:** required-check scope (sensitive class)

## Context

The organization needs quick feedback while a pull request is being drafted,
but full repository suites and candidate builds can use substantially more
runner time. Deferring every workflow would remove useful lint, type-surface,
and admission feedback. Deferring only the expensive jobs requires explicit
classification and a reliable transition that starts them after review begins.

## Decision

Use the classifications and job inventory in
[`docs/ci-workflow-tiers.md`](../../ci-workflow-tiers.md):

1. Tier 1 lint, type-surface, admission, and short contract checks continue to
   run on draft pull requests.
2. Tier 2 full CI suites and candidate builds use job-level draft guards. When
   a workflow serves non-PR events too, its guard preserves those paths with an
   event-name check. Existing path filters, check names, permissions, and job
   predicates remain unchanged.
3. Tier 2 workflows with explicit pull-request activity types include
   `ready_for_review`, which starts the deferred checks, and
   `converted_to_draft`, which lets per-PR concurrency cancel work already in
   progress. Their push and manual-dispatch paths keep their existing behavior.
4. Keep authorization and merge-gate workflows triggered by
   `pull_request_target` outside this scheduling policy.
5. Register a conformance test that pins the workflow classification, draft
   guards, ready transition, and non-PR behavior.

GitHub's default `pull_request` activity types do not include
`ready_for_review`; workflows that opt into activity types must request it
explicitly. Required checks may report skipped successfully, while the draft
state itself prevents merging. Marking the pull request ready schedules new
required-check runs.

## Consequences

Drafts keep fast feedback without holding runners for the long test matrix or
candidate build. Tier 2 check contexts remain stable and run again when the
author marks the pull request ready. The workflow inventory and test must be
updated whenever a new heavy pull-request workflow is added or its work changes
tier.
