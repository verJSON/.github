---
date: 2026-10-15
issue: 1792
impact: patch
title: Accept GitHub App client IDs in the changelog fleet report
---

The changelog contract fleet report now validates `app_client_id` as a GitHub App client ID (`^Iv[A-Za-z0-9.]{8,126}$`, the merge gate's pattern) and refuses a numeric legacy App ID with a message naming the client-ID format. It previously required a numeric value, which is the App ID, so every real client ID passed by `verjson-agents` was refused and the scheduled report has produced no inventory since 2026-09-28 (verjson-agents#408).

Callers keep the `app_client_id` input; `verjson-agents` must move its reusable-workflow pin to a commit containing this fix. `scripts/ci-gate/fleet-contract-inventory.test.py` executes the workflow's own guard against real, numeric, empty, whitespace, newline, and shell-metacharacter values. ADR 0201 is amended with the evidence.
