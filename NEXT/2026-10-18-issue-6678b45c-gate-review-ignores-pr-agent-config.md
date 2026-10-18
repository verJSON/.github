---
date: 2026-10-18
id: 6678b45c
impact: patch
title: Keep pull-request agent configuration out of the merge-gate Claude review
---

The merge-gate Claude review now loads only the runner's user setting source and only the MCP servers the action passes explicitly, and the gate removes pull-request agent configuration (`.claude/`, `.mcp.json`, root `CLAUDE.md`, and the other paths the pinned action treats as sensitive) right after checking out the head. Adopters receive the fix when their generated caller is regenerated at or after this change.

The pinned `claude-code-action` loads project and local settings by default and restores them from the base branch only for pull-request events, never for the `workflow_dispatch` this gate runs under. A same-repository pull request could therefore have its own configuration acted on inside the gate job. See [ADR 0237](../docs/decisions/0237-gate-review-ignores-pull-request-agent-config/README.md).
