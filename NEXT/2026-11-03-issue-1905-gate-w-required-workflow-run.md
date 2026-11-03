---
date: 2026-11-03
issue: 1905
impact: minor
title: Refuse a merge whose required workflow did not succeed on the exact head (Gate W)
---
`scripts/assert-mergeable-head.sh` adds Gate W. It exits 3 unless every required workflow that a ruleset `workflows` rule names for the base ref has a successful run on the exact head, created after that ruleset last changed. Before this, the gate printed `true` while GitHub refused the merge, which is the #1905 shape seen on verjson-identity-contracts#121 when the local `gate re-arm` caller displaced the cancelled required run (ADR 0255).

- A run binds only through provider fields: its path, the `pull_request_target` event, a required-workflow URL naming its own workflow id, and this pull request's head branch and repository. The run name is never read.
- Only a later successful run of the same required workflow supersedes a cancelled one. The ADR 0226 family set-aside still applies to Gates A and C, and never satisfies Gate W.
- A required run created at or before the ruleset's `updated_at` is stale. Every edit to the ruleset therefore ships a re-arm sweep of open enrolled pull requests (`docs/arm-recovery-runbook.md`).
- Missing, unbound, stale, undated, ambiguous, changed-between-reads, cancelled and not-successful runs all fail closed with exit 3, as does a `workflows` rule whose shape cannot be read. A refused or foreign ruleset read exits 1. A ref with no `workflows` rule is unaffected.
- Gate W is a precondition only. Merge admission stays with #1945.
- A contract test pins that the org-required arm never skips or yields to a local caller on `opened`, `reopened` or `synchronize` (F-3): no condition reads the invocation identity, and the arm classifies only the label and lifecycle callers.
- `docs/arm-recovery-runbook.md` codifies the interim close-and-reopen recovery (F-6). Only a human or an App actor may run it, at most twice per head, and only when the pull request's whole arm family is quiet. It never uses `gh run rerun` and never a bypass. It stays permanently for watchdog and external cancellations.

Part of #1905.
