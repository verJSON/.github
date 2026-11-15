---
date: 2026-11-15
issue: 1978
impact: patch
title: Build caller-contract fixtures only for the selected shard
---

Each changelog caller-contract shard now builds a case fixture after that case
is selected, and builds a shared adopter tree on its first selected use.
Out-of-case reads name the selection that owns them, so skipping a build cannot
silently weaken an assertion. The pinned selection order and the 399-case
shard map are unchanged.

A stubbed walk of the four shards materializes 36, 33, 35, and 32 fixtures
instead of every shard building the full set.

The shard-only caller-contract fixture helpers change full-checkout bytes
seen by `release.yml` verify, so the ADR 0235 pending backlog digest moves
to `sha256:141778ee…`.
