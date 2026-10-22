---
date: 2026-10-22
issue: 1423
impact: patch
title: Record the ADR 0212 second rotation receipt for ruleset 21830367
---

Ruleset 21830367 now runs `f967b0b7`, the commit that nulled the CLI Projects legacy caller digest. ADR 0212 records the dry-run, rollback sha, acknowledged apply, a two-line read-back diff, audit, and a fresh exact-head verify-run. Documentation only. Part of #1423.
