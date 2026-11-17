---
date: 2026-11-17
issue: 2209
impact: minor
title: Renovate agent-delivery worker with fail-closed majors
---

Replace the Renovate agent-delivery claim stub with `DependencyUpgradeWorker`:
major/breaking bumps fail closed with a visible worker receipt and invent no
`NEXT/` fragment; eligible claims post a durable `agent-delivery-worker:v1`
handoff so an agent can run the dependency-upgrade skill under existing merge
gates (#2209 / ADR 0243, ARB `arb-2139-2026-11-17`).
