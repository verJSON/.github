---
date: 2026-11-15
issue: 2120
impact: patch
title: Carry needs-changelog-context on generated-artifacts callers
---

The Renovate handoff annotation now runs on `generated-artifacts.yml` as well as
`changelog-validate.yml`, so the common adopter pin (including verjson-cli) can
surface `needs-changelog-context` after a contract bump. ADR 0243 records the
amendment; the generated-artifacts backlog digest is refreshed.
