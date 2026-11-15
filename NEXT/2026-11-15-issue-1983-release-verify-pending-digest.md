---
date: 2026-11-15
issue: 1983
impact: patch
title: Refresh release verify backlog digest after fixture-abort tests
---

Caller-contract fixture abort coverage changes full-checkout bytes seen by
`release.yml` verify, so the ADR 0235 pending backlog digest moves to
`sha256:1dbf9d08…`.
