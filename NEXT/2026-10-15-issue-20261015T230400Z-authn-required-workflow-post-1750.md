---
date: 2026-10-15
id: 20261015T230400Z
impact: patch
title: Repin the authn required workflow to node-ci-protected v4.0.0
---

Hardening: the generated `authn-type-surface-required.yml` now calls
`node-ci-protected.yml` at `576707fe` (v4.0.0), which contains #1750 and #1759,
instead of `3f8fb4ba`. Organization ruleset 21750617 keeps running the old revision
until an operator rotates it to this change's merge commit
([ADR 0151](../docs/decisions/0151-authn-type-surface-required-workflow/README.md)).
