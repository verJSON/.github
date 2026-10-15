---
date: 2026-10-15
issue: 1423
impact: patch
title: Repin the CLI projects required workflow to node-ci-protected v4.0.0
---

Hardening: both protected Node lanes of `cli-projects-package-surface-required.yml`
now call `node-ci-protected.yml` at `576707fe` (v4.0.0), which contains #1750 and
#1759, instead of `1c7659b7`. `config/cli-projects-required-node-ci.json` now pins the
protected lanes (`protected_node_ci_sha`) separately from the push-only consumer caller
(`consumer_node_ci_sha`), so this repin leaves the consumer `ci.yml` digest that admission
requires unchanged and needs no `verjson-cli-projects` change. Organization ruleset
21830367 keeps running the old revision until an operator rotates it to this change's
merge commit ([ADR 0153](../docs/decisions/0153-cli-projects-package-surface-required-workflow/README.md)).
