---
date: 2026-11-17
issue: 2212
impact: minor
title: Read-only fleet ledger for agent-delivery claims
---

Add a read-only operator ledger that lists trusted active agent-delivery claim
receipts across repositories for concurrency visibility. It does not create or
authorize claims — the per-(repo, pr, head_sha) comment receipt remains the
authority (#2212 / ADR 0243).
