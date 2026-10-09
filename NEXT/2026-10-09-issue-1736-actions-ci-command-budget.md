---
date: 2026-10-09
issue: 1736
impact: patch
title: Bound actions-ci command runtimes
---
Actions CI runs independent manifest commands concurrently with a 60-second pull-request budget and reuses parsed workflows, generated callers, and per-file scans so release-contract work can finish without dropping assertions.

The 233 caller-contract mutation executions are split across the existing four cells, with generator-wide assertions run once. Every selected mutation still runs the complete generated contract; no generated assertions are skipped. Command logs report elapsed time on both success and failure.

Pull requests ShellCheck only changed tracked shell scripts; pushes to `main` keep the full-tree audit. The workflow retains the 30-minute job timeout and six-job matrix concurrency. See #1736 and ADR 0225.
