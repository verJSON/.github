---
date: 2026-11-15
issue: 2065
impact: minor
title: Pin cli-projects Node CI to v5.0.0 with a 26.13.1 floor
---

cli-projects required Node CI now pins the v5.0.0 contract and takes its
consumer `node-version` from the configured floor (`26.13.1`), so
verjson-cli-projects can adopt Node 26.13.1 without weakening ADR 0212
admission. Live ruleset rotation onto the regenerated required workflow
remains a human step.

