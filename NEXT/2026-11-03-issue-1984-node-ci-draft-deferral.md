---
date: 2026-11-03
issue: 1984
impact: minor
title: Generated Node CI callers defer every lane on draft pull requests
---

`scripts/gen-node-ci-caller.py` now skips every generated job, the
`test-levels` aggregate included, while a pull request is a draft, and triggers
on `pull_request` types `opened`, `synchronize`, `reopened`, and
`ready_for_review`, so marking the pull request ready runs the whole plan on its
head. A draft holds no runner (#1984, part of #1869). The caller's
concurrency now cancels a running run only on a new commit (`synchronize`); a
same-head `opened`, `reopened`, or `ready_for_review` run waits instead of
cancelling a ready run into a red required check.

Required contexts stay fail-closed: the single-job caller's `ci / build-test`
is not reported on a draft, and a skipped `test-levels` is never the SUCCESS
the merge gate's Gate A requires. Drafts skip rather than fail, because a
draft-time failure would stay on the head after the ready run passed and hold
Gate C closed.
