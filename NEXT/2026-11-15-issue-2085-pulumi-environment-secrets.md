---
date: 2026-11-15
issue: 2085
impact: minor
title: Bind Pulumi preview to a caller-named secrets environment
---

Pulumi preview can run in a caller-named GitHub environment so `PULUMI_ACCESS_TOKEN` is an environment secret under a `main`-only deployment branch policy. Validation never binds that environment and never receives the token. An empty `secrets-environment` keeps the existing mapped-secret path. See ADR 0266.
