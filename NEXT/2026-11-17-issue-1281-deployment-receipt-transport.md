---
date: 2026-11-17
issue: 1281
impact: minor
title: Parent-owned deployment receipt fetch for rollback
---

The trusted parent transport can retrieve prior `runner-deployment-*` Actions
artifacts with the job `GITHUB_TOKEN`, bind `rollbackSource` during
`collect-evidence`, and keep evidenceCommand credential-free for rollback.

Request shape is dedicated (`operation: deployment-receipt`) rather than reusing
probe/hostExport fields: `github` is repository name/ID only, with optional
`sourceAttemptId` / `retainedReceiptAuthority`, otherwise a bounded digest-only
artifact scan (max 20 downloads). Resume/durable retained-authority fetch is
not wired into `collect-evidence` yet; rollback uses digest-only lookup from
`--rollback-receipt`. Part of #1281.
