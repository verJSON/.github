---
date: 2026-11-10
issue: 1905
impact: minor
title: Document re-arm recovery and accept provider-bound required workflow runs
---

`docs/arm-recovery-runbook.md` now says never to run `gh run rerun` on the
repository-local re-arm callers (`gate-rearm.yml`, `ai-review-label-rearm.yml`,
`ai-review-lifecycle-rearm.yml`), not only on the organization-injected required
run. A rerun of the label or lifecycle caller concludes `failure`, because its arm
job refuses a replay, and Gate C never sets a failed run aside. A rerun of
`gate-rearm.yml` can displace a pending required run. When close-and-reopen
succeeds but Gate C still refuses on cancelled re-arm jobs, the runbook gives the
recovery that worked on #2022: push a new head after the quiet-family check. That
push counts as an attempt and needs a fresh Gate R review receipt (#1905).

Gate W now accepts a required workflow run triggered by `pull_request` as well as
`pull_request_target` when GitHub binds the run to its required-workflow URL and
workflow ID, and its head, branch, repository, freshness, and successful result
match. Runs from `push`, `merge_group`, and other events remain unbound. The
concurrency-family contract and Gate C behavior are unchanged.
