---
date: 2026-11-16
id: 20261116T213516Z
impact: patch
title: Route node-ci eligibility off the persistent pool
---

`node-ci.yml` / `node-ci-protected.yml` `eligibility` now stamps the catalog
`untrusted-lane` selector so both workflow_call relay-backlog rows can leave.
Part of #1831.

Eligibility is API-only (status probe + inline guards) and no longer honors
`inputs.runner` or the trusted/fallback pool. ADR 0235 records the route and
the backlog departure. `release.yml` `verify`'s pending digest is refreshed
because that row binds this checker; the job itself is unchanged.
