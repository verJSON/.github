---
date: 2026-11-16
id: 20261116T085400Z
impact: patch
title: Route container deploy/review/release retention off the persistent pool
---

Public Verjson calls to container-deployment, container-deployment-review-producer,
and container-release retention now select the untrusted lane with a hosted tail
instead of CI_LANE_TRUSTED||FALLBACK. Their nine relay-backlog rows leave with
that change. Part of #1831.

Private Verjson keeps the trusted lane; non-Verjson callers stay on hosted.
Public pull-request callers remain rejected by the existing sentinel jobs.
ADR 0235 records the route and the backlog departure. `release.yml` `verify`'s
pending digest is refreshed because that row binds this checker; the job itself
is unchanged.
