---
date: 2026-10-15
issue: 1778
impact: patch
title: Mint the Renovate compatibility token for the renamed runner repository
---

The Renovate compatibility reconciliation requested its App token for `verjson-github-runner`, which was renamed to `verjson-git-runners`. GitHub refuses a token request that names any repository the installation cannot see, so every scheduled run failed before reconciling anything. The token now names the current repository; its scope and read-only permissions are unchanged (#1778).
