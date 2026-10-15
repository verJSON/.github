---
date: 2026-10-15
issue: 1799
impact: patch
title: Accept the withdrawn organization review key in the authorization-arm audit
---

The authorization-arm audit accepts a withdrawn organization copy of `AI_REVIEW_APP_PRIVATE_KEY` as the conforming custody state and then requires every armed repository to hold the key in its `ai-review-app` environment, reporting a missing key separately from an unreadable one. An organization 404 the same token cannot corroborate from a complete secret listing, and every other read failure, still fails closed (#1799, ADR 0166).

The live audit had stopped at the organization secret's 404 since the 2026-09-27 withdrawal. While an organization copy exists the visibility verdict is unchanged, and the `AI_REVIEW_APP_ID`, `AI_REVIEW_APP_SLUG` and `AI_REVIEW_CLIENT_ID` organization variables keep their reach checks.

`config/audit-expected-findings.json` no longer records the #1401 deterministic-CI finding, which stopped reproducing once `verJSON/verjson-agents` moved to `verjson-stack=actions`; the arm audit's expectation is now empty, so every finding it reports is new drift.

A read is treated as absent only when gh's status line ends in `(HTTP 404)`, as `privileged-merge-conformance.sh` already requires; a 404 quoted inside another status's message is an unreadable API.

An unreadable environment key is reported with the reason it could not be read, and every GitHub API read failure in the audit now carries gh's status line (for example `gh: Forbidden (HTTP 403)`), so an expired token is distinguishable from an outage.
