---
date: 2026-11-03
issue: 1984
impact: minor
title: Generated Node CI callers re-run when a draft becomes ready
---

`scripts/gen-node-ci-caller.py` now triggers on `pull_request` types `opened`,
`synchronize`, `reopened`, and `ready_for_review`. A pull request marked ready
therefore starts a fresh run of every lane on its head (#1984, part of #1869).
