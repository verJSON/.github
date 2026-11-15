---
date: 2026-11-15
issue: 1994
impact: patch
title: Pin freshness hold reasons in the merge-gate test
---

The freshness merge-gate cases now require the conflict, metadata, mergeability, or compare hold text the step already prints. A shared exit 1 from an earlier hold can no longer keep a later case green.

`scripts/ci-gate/arm-receipt.test.sh` and `scripts/ci-gate/native-automerge.test.sh` were already reason-pinned by #2023. This slice covers the ten remaining status-only freshness refusals. Part of #1994.
