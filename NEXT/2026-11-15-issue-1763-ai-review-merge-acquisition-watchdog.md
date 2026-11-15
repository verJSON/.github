---
date: 2026-11-15
issue: 1763
impact: patch
title: Bound runner acquisition on the AI review merge workflow
---

`ai-review-merge.yml` now runs the same hosted-only runner-acquisition watchdog
as `gate-rearm.yml`, so a private review run whose `gate` job never gets a
runner fails closed within the 900 s stall bound instead of holding the PR
indefinitely. ADR 0230 is amended for this follow-up.

The merge-workflow watchdog adds settable-value guards; `GUARD_COUNT` moves to 565.
