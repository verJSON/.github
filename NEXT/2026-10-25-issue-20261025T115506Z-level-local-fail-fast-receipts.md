---
date: 2026-10-25
id: 20261025T115506Z
refs: 1869
impact: patch
title: Record level-local script failures with trusted entry receipts
---
Part of #1869. Node CI levels carry strict, level-local `failFast` from the immutable plan through callers, authorization, and both executors; omission defaults to `true`. `false` continues after ordinary candidate failures, latches level failure, and records supervisor-owned JSON receipts under host `RUNNER_TEMP`. The credentialed npm route rejects `false` before checkout or candidate commands, and every source secretless execution downloads the pinned receipt supervisor. Both Bubblewrap executors require matching `--json-status-fd` startup and exit events; setup, boundary, receipt, cleanup, or ambiguous 128–255 status failures are terminal and cannot launch later entries. Protected receipts bind the admitted `inputs.head-sha`. Detached-session cleanup is exercised in both real executors. Local verification used Bubblewrap 0.11.1; hosted acceptance remains gated on the canonical Ubuntu probe and CI. No credential, permission, egress, service, cache, or matrix behavior changes.
