---
date: 2026-10-04
issue: 1685
impact: patch
title: Validate REST draft metadata at the terminal merge boundary
---

Read the REST pull request response's `draft` field instead of the CLI and
GraphQL `isDraft` field, restoring autonomous merges of verified non-draft PRs.
Use the REST shape in the promotion fixture and verify that draft, missing,
null, and malformed draft values still fail closed before any merge attempt.
The correction restores ADR 0120's terminal authorization boundary.
