---
date: 2026-11-16
issue: 2156
impact: patch
title: Prove no-owner-admin-merge catches both --admin flag orderings
---

Rewrite `no-owner-admin-merge.test.sh` on python3 so merge-gate runners (no
ripgrep) still fail closed, and add probes for both `gh pr merge --admin …` and
`gh pr merge … --admin` plus a non-merge `--admin` negative. Closes #2156.
