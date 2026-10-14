---
date: 2026-10-13
issue: 1754
impact: patch
title: Admit heads whose cancelled checks a later run of the same workflow superseded
---
`scripts/assert-mergeable-head.sh` no longer refuses a drafted-then-readied green pull request because the draft-time run was cancelled by concurrency: Gates A and C set aside a cancelled check run when its own workflow run was cancelled and the newest later run of the same workflow, under the same event and on the same head, succeeded and reports that check as SUCCESS or not at all (ADR 0226). The passing conclusion set is unchanged, and every other cancellation, failure, or pending check still blocks.

Ordering uses the per-workflow `run_number`, never timestamps or check-run ids. Workflow runs are read only when the head carries a cancelled check run, so that read needs Actions (read) only then; malformed, foreign-head, duplicate-suite, or unattested workflow-run inventories exit `1`. Gate B still reads the raw inventory. A superseder that reports the check as SKIPPED or NEUTRAL does not vouch for it, so a ready-time run that legitimately skipped the cancelled work (#1753's documentation-only shape) still needs a re-run. Evidence: #1750 head `d762771c` and #1753 head `c10425e6`.
