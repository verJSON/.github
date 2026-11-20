---
date: 2026-11-20
id: 20261120T014500Z
impact: minor
title: Release verification may trust exact-head required CI
---

ADR 0291 accepts opt-in exact-head CI trust for generated release callers:
`checks: read` plus a generator-owned assert of named GitHub Actions contexts on
`github.sha`, without giving the adopter verification hook a token. Ships the
assert helper and its tests; generator `--require-ci-context` wiring follows on
#2260.
