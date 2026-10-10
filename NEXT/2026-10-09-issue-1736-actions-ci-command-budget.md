---
date: 2026-10-09
issue: 1736
impact: patch
title: Bound actions-ci command runtimes
---
Actions CI runs independent manifest commands concurrently with a 60-second pull-request budget on every group-runner invocation, including documentation contracts, and reuses parsed workflows, generated callers, and per-file scans so release-contract work can finish without dropping assertions. The required-checks audit, conformance suite, actions-ci manifest contract, and privileged-merge conformance rows run alone to avoid CPU contention at the command cap. The audit contract assertions run in two isolated partitions concurrently within their row; logs report effective parallelism and each command's elapsed time.

The 233 caller-contract mutation executions are split across the existing four cells, with generator-wide assertions run once. Every selected mutation runs the complete generated contract, and each shard fails unless it executes exactly one case. Each worker's output, status, and elapsed time are emitted as soon as it is reaped so a slow sibling cannot hide completed diagnostics; missing or malformed worker results fail the group. Commands run in isolated process groups, and surviving descendants are stopped on normal exit, timeout, worker failure, or cancellation before shared scratch data is removed.

Budgeted commands keep descendants in the tracked process group, and output is captured and sanitized after each process exits, so a child cannot escape cleanup or hold up timeout handling by inheriting stdout. If a worker dies before sanitizing output, the parent preserves its diagnostics. Pull requests ShellCheck only changed tracked shell scripts; pushes to `main` keep the full-tree audit. The workflow retains the 30-minute job timeout and six-job matrix concurrency. See #1736 and ADR 0225.
