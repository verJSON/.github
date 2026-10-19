---
date: 2026-10-18
id: 6678b45c
impact: patch
title: Keep pull-request and runner configuration out of the merge-gate review
---

The merge-gate Claude review no longer loads agent configuration from the pull request or the runner. It loads only a fresh per-run user layer and only explicitly configured MCP servers. The gate also removes pull-request agent configuration (`.claude/`, `.mcp.json`, root `CLAUDE.md`, and the other paths the pinned action treats as sensitive) and keeps the job token out of the checkout the model can read. The review can now read only the checkout and its own inputs, never process state. Those inputs and the telemetry payload are written outside the checkout, and an empty or incomplete review diff for a real change fails closed. Lockfiles are elided by path rather than by parsing diff headers, binary changes outside images and fonts go to a human, and approval reuse requires a whitespace-exact match. A verdict that carries credential material is unusable. Review diffs ignore pull-request git attributes and conversion drivers, so a changed text file cannot be shown to the reviewer as binary. Adopters receive the fix when their generated caller is regenerated at or after this change.

The pinned `claude-code-action` loads project and local settings by default, restores them from the base branch only for pull-request events, and rewrites `origin` with its token in agent mode. The gate runs under `workflow_dispatch`, so a same-repository pull request could have its own configuration acted on inside the gate job. See [ADR 0237](../docs/decisions/0237-gate-review-ignores-pull-request-agent-config/README.md).
