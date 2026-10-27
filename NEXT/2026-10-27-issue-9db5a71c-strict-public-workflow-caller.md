---
date: 2026-10-27
id: 9db5a71c
refs: 1831
impact: patch
title: Enforce public reusable-workflow runner isolation
---
The runner-isolation checker now models public pull-request calls to reusable
workflows with symbolic caller variables, inputs, and identity, and follows
nested local calls. It rejects remote callees and any reachable runner
selector it cannot prove uses a GitHub-hosted ephemeral runner. Per-entrypoint
and workflow-scan budgets fail closed on exhaustion, and the stricter scenario
cannot be waived through generic relay review rows.
