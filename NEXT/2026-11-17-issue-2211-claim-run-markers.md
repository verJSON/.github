---
date: 2026-11-17
issue: 2211
impact: minor
title: Bind agent-delivery claims to run_id and workflow path
---

Claim receipts emit `agent-delivery-claim:v2` markers that bind Actions
`run_id` and the trusted workflow path alongside the `(repo, pr, head_sha)`
triple. Missing or malformed bindings fail closed; legacy v1 markers remain
readable for idempotent find. Part of #2211 (ARB `arb-2139-2026-11-17`).
