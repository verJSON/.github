---
date: 2026-10-25
issue: 1800
impact: patch
title: Validate configured GitHub App client IDs before every token mint
---

Canonical workflows now use one generated preflight guard bound to the exact client ID passed to each GitHub App token mint, and both Python configuration validators share the same policy. The policy and its limits are recorded in [ADR 0241](../docs/decisions/0241-configured-github-app-client-id-preflight-policy/README.md).
