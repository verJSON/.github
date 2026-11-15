---
date: 2026-11-15
issue: 1808
impact: patch
title: Name the renamed runner repository in privileged-merge public routes
---

Privileged merge's public-hosted admission now compares against
`Verjson/verjson-git-runners` instead of the pre-rename
`Verjson/verjson-github-runner`, so the runner repository can schedule the
terminal job again. ADR 0089 records the amendment; the caller-contract
pins and the public-call guard baseline use the same name.


Head refreshed 2026-11-15 to clear a cancelled required gate-rearm run (Gate W / #1905 recovery).
