---
date: 2026-11-15
issue: 1831
impact: patch
title: Route public actionlint off the persistent fast lane
---

Public actionlint, including a workflow_call whose caller event is not a public pull request, now selects the untrusted lane with a hosted tail instead of `CI_RUNNER_FASTLANE`. The job's relay-backlog row leaves with that change. Part of #1831.

Private targets stay on the trusted lane. Unresolved visibility still prefers the untrusted lane and may use `CI_LANE_FALLBACK` only when that lane is unset. ADR 0050 and ADR 0235 record the route and the backlog departure. The other relay rows stay. `release.yml` `verify`'s pending digest is refreshed because that row binds this checker, whose backlog set changed; the job itself is unchanged.
