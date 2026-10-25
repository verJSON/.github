---
date: 2026-10-25
id: 20261025T115506Z
refs: 1869
impact: patch
title: Record level-local script failures with trusted entry receipts
---
Part of #1869. Node CI levels now carry a strict, level-local `failFast` value from immutable plan through caller generation, authorization, and both executors. The default remains `true`; `false` continues after ordinary candidate failures while latching level failure, and emits trusted per-entry JSON receipts under host `RUNNER_TEMP`. Infrastructure failures stop later launches, and missing or contradictory receipts fail closed. Source and protected Bubblewrap paths verify process-group cleanup before advancing. No credential, permission, egress, service, cache, or matrix behavior changes.
