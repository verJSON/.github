---
date: 2026-10-09
issue: 1736
impact: patch
title: Bound actions-ci command runtimes
---
Actions CI runs independent manifest commands concurrently with a 60-second pull-request budget and reuses parsed workflows, generated callers, and per-file scans so release-contract work can finish without dropping assertions. The required-checks audit and privileged-merge conformance rows run alone to avoid CPU contention at the command cap; logs report effective parallelism and each command's elapsed time.

The 233 caller-contract mutation executions are split across the existing four cells, with generator-wide assertions run once. Every selected mutation runs the complete generated contract, and each shard fails unless it executes exactly one case. Command logs report elapsed time on success and failure; missing or malformed worker results fail the group.

Pull requests ShellCheck only changed tracked shell scripts; pushes to `main` keep the full-tree audit. The workflow retains the 30-minute job timeout and six-job matrix concurrency. See #1736 and ADR 0225.
