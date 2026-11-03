---
date: 2026-11-03
issue: 1987
impact: patch
title: Fail closed on Actions-paused drift
---
The scheduled organization ruleset conformance run now fails when ADR 0252's Actions-paused exclusion drifts from live state: a `paused` repository with Actions enabled, a default branch governed by the baseline ruleset 26466984 when it should not be (or not governed when it should), or a paused-only relaxation ruleset left at the wrong enforcement. Unreadable or unexpected API state fails too.
- The ruleset IDs live in one table, `config/actions-paused-drift-policy.json`, whose source is ADR 0252.
- It must land before the first canary repository is re-enabled (#1987, part of #1985).
