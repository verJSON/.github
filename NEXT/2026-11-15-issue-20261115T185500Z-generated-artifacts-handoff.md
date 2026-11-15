---
date: 2026-11-15
id: 20261115T185500Z
impact: patch
title: Carry needs-changelog-context on generated-artifacts callers
---

The Renovate handoff annotation now runs on `generated-artifacts.yml` as well as
`changelog-validate.yml`, so the common adopter pin (including verjson-cli) can
surface `needs-changelog-context` after a contract bump. Part of #2120; ADR 0243
records the amendment; the generated-artifacts backlog digest is refreshed.
