---
date: 2026-10-21
id: 6d1f63d6
impact: patch
title: Collapse identical Renovate transition rows instead of refusing the pull request
---

Renovate changelog attribution now records a package transition once when Renovate's update table lists it in several rows, as it does when one image is bumped in more than one file or build stage. Verjson/verjson-ci#290 failed `renovate-changelog / attribute` because `node` 26.10.0-alpine3.23 → 26.12.0-alpine3.23 appeared twice. Rows for the same package with a different from or to version stay separate, the 50-row bound still counts every row before collapsing, and package and version spellings are compared exactly. Adopters receive the fix when they regenerate their callers at a contract SHA that includes it. ADR 0104's 2026-10-21 amendment records the change.
