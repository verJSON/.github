---
date: 2026-11-15
issue: 2085
impact: minor
title: Bind Pulumi preview to a caller-named secrets environment
---

Pulumi preview can run in a caller-named GitHub environment so `PULUMI_ACCESS_TOKEN` is an environment secret under a `main`-only deployment branch policy. Validation never binds that environment and never receives the token. An empty `credential-environment` keeps the existing mapped-secret path. See ADR 0266.

Pulumi-ci workflow edits change the settable-guard inventory; `GUARD_COUNT` moves to 552 against current main.

Pulumi-ci validate / preview-admission / preview backlog digests are refreshed for the renamed input and environment binding.
