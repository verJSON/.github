---
date: 2026-10-25
id: 20261025T115506Z
refs: 1869
impact: patch
title: Record level-local script failures trusted entry receipts
---
Part of #1869. Typed Node CI levels carry strict, level-local `failFast` through generated callers, authorization, and both executors; omission defaults to `true`. With `false`, ordinary candidate failures are recorded and later entries run for diagnostics, while the level failure remains latched. The credentialed npm route rejects `false` before checkout or candidate commands.

A trusted PID 1 supervisor writes versioned per-entry and plan JSON receipts under a fresh private host `RUNNER_TEMP` directory. Receipts bind the admitted checkout SHA and the complete run, job, level, and expanded-plan identity. The supervisor waits for the direct candidate, then performs bounded namespace cleanup and reaps descendants before the next entry. Separate Bubblewrap and trusted-wrapper status streams fail closed on missing, malformed, contradictory, or ambiguous evidence. Cancellation invalidates plan acceptance while preserving completed entry facts.

Real local Bubblewrap coverage exercises source and protected execution, detached and double-forked descendants, stopped and TERM-resistant children, receipt/cancellation paths, and denial of candidate access to the supervisor channel. Local verification used Bubblewrap 0.11.1; canonical GitHub-hosted acceptance remains pending. No credential, permission, egress, service, cache, or matrix behavior changes.
