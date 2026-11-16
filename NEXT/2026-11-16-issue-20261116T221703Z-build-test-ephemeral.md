---
date: 2026-11-16
id: 20261116T221703Z
impact: patch
title: Route node-ci build-test off the persistent pool for public calls
---

Part of #1831. The `secretless-first` catalog shape now gates every non-private repository onto the literal hosted image before `inputs.runner`, so `workflow_call` relays cannot land public pull-request code on the persistent self-hosted pool. Private Verjson keeps the secretless-first / trusted-lane tail. Both `node-ci.yml` and `node-ci-protected.yml` `build-test` jobs leave the ADR 0235 relay backlog.
