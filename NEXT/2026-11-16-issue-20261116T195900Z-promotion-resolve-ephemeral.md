---
date: 2026-11-16
id: 20261116T195900Z
impact: patch
title: Route ai-promotion-retry resolve off the persistent pool
---

`ai-promotion-retry.yml` / `resolve` now takes the untrusted hosted lane
for public and unclassified repositories, so its `[workflow_call]` relay-backlog
row can leave. Part of #1831.

Private repositories keep `CI_LANE_TRUSTED||FALLBACK`. ADR 0235 records the
route and the backlog departure. Under the public-repository evaluation the
shared expression is hosted-only, so the former `[workflow_run]` reviewed
entry retires with it. Fixture exemplars that used this job as the live
sparse-digest or workflow_call caller target retarget to `runner-canary.yml` /
`canary` and `node-ci-protected.yml` / `build-test`. `release.yml`
`verify`'s pending digest is refreshed because that row binds this checker;
the job itself is unchanged.
