---
date: 2026-11-15
id: 20261116T041500Z
impact: patch
title: Route public container-candidate off the persistent fallback
---

Public `container-candidate` workflow_call jobs now select the untrusted lane with a hosted tail and ignore `inputs.runner` on public Verjson repositories, so they no longer fall through to `CI_LANE_FALLBACK`. The three relay-backlog rows leave with that change. Part of #1831.

Private Verjson targets keep the trusted lane and may still pass `inputs.runner`. Non-Verjson callers stay on hosted. ADR 0235 records the route and the backlog departure. `release.yml` `verify`'s pending digest is refreshed because that row binds this checker, whose backlog set changed; the job itself is unchanged.
