---
date: 2026-11-15
issue: 1978
impact: patch
title: Refresh release verify backlog digest after shard-only fixtures
---

The shard-only caller-contract fixture helpers change full-checkout bytes
seen by `release.yml` verify, so the ADR 0235 pending backlog digest moves
to `sha256:141778ee…`.
