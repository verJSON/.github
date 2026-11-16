---
date: 2026-11-16
issue: 2156
impact: patch
title: Prove no-owner-admin-merge catches both --admin flag orderings
---

Add explicit probes to `no-owner-admin-merge.test.sh` so the inventory pattern is
shown to match `gh pr merge --admin …` and `gh pr merge … --admin`, and to reject
non-merge `--admin` strings. Closes #2156.
