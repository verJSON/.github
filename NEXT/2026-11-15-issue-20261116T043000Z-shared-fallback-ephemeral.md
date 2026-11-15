---
date: 2026-11-15
id: 20261116T043000Z
impact: patch
title: Route five shared FALLBACK adopters off the persistent pool
---

Public `generated-artifacts`, `notify-umbrella`, `pulumi-ci` preview, `repo-hygiene`, and `ui-ci` workflow_call jobs now select the untrusted lane with a hosted tail and ignore `inputs.runner` on public Verjson repositories. Their five relay-backlog rows leave with that change. Part of #1831.

Private Verjson targets keep the trusted lane and may still pass `inputs.runner`. Non-Verjson callers stay on hosted. ADR 0235 records the route and the backlog departure. `release.yml` `verify`'s pending digest is refreshed because that row binds this checker; the job itself is unchanged.
