---
date: 2026-11-16
id: 20261116T090300Z
impact: patch
title: Route TRUSTED||FALLBACK observe set off the persistent pool
---

Seven observe/reconcile/canary/receipt jobs that used only
`CI_LANE_TRUSTED||FALLBACK` now take the untrusted hosted lane for public and
unclassified repositories. Their seven relay-backlog rows leave with that
change. Part of #1831.

Private repositories keep the trusted lane. ADR 0235 records the route and the
backlog departure. `release.yml` `verify`'s pending digest is refreshed because
that row binds this checker; the job itself is unchanged.

`release-app-canary.test.py` and `renovate-compatibility-workflow.test.sh` bind the private-gated expression so the observe-set route cannot drift back to plain TRUSTED||FALLBACK.
