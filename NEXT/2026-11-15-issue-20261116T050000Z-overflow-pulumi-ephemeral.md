---
date: 2026-11-15
id: 20261116T050000Z
impact: patch
title: Route pulumi and OVERFLOW adopters off the persistent pool
---

Public `pulumi-ci` validate/preview-admission and the shared OVERFLOW changelog/release-dispatch/propose workflow_call jobs now select the untrusted lane with a hosted tail. Their six relay-backlog rows leave with that change. Part of #1831.

OVERFLOW adopters also ignore `inputs.runner` on public Verjson repositories. Private targets keep their trusted/overflow lanes. ADR 0235 records the route and the backlog departure. `release.yml` `verify`'s pending digest is refreshed because that row binds this checker; the job itself is unchanged.
