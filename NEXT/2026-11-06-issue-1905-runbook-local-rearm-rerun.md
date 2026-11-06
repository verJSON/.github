---
date: 2026-11-06
issue: 1905
impact: patch
title: Extend the arm recovery runbook's never-rerun rule to repository-local re-arm callers
---
`docs/arm-recovery-runbook.md` now says never to run `gh run rerun` on the repository-local re-arm callers (`gate-rearm.yml`, `ai-review-label-rearm.yml`, `ai-review-lifecycle-rearm.yml`), not only on the organization-injected required run. A rerun of the label or lifecycle caller concludes `failure`, because its arm job refuses a replay, and Gate C never sets a failed run aside. A rerun of `gate-rearm.yml` can displace a pending required run. When a close-and-reopen succeeds but Gate C still refuses on cancelled re-arm jobs, the runbook now gives the recovery that worked on #2022: push a new head after the quiet-family check. That push counts as an attempt and needs a fresh Gate R review receipt (#1905).
