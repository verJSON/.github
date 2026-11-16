---
date: 2026-11-16
id: 20261116T020034Z
impact: patch
title: Route capability-floor-observe off the persistent pool
---

`capability-floor-observe.yml` / `observe` now takes the untrusted hosted lane
for public and unclassified repositories, so its relay-backlog row can leave.
Part of #1831.

Private repositories keep `CI_LANE_PRIVILEGED||FALLBACK`. ADR 0235 records the
route and the backlog departure. The hosted-selector allowlist gains the
private-gated PRIVILEGED expression. Fixture exemplars that used this job as
the no-refusal or live sparse-digest target retarget to `release.yml` /
`publish` and live `ai-promotion-retry.yml` / `resolve`. `release.yml`
`verify`'s pending digest is refreshed because that row binds this checker; the
job itself is unchanged.
