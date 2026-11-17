---
date: 2026-11-17
issue: 629
impact: patch
title: Clear verjson-git-runners production environment reviewers per ADR 0144
---

Remove the stray `required_reviewers` rule on `verjson-git-runners` `production` so the
environment matches ADR 0144 (branch policy only; deployment authority from exact-head
receipts).

Part of #629. Live PUT cleared reviewers; this change records the ADR amendment and Active Issues refresh.
