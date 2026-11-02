---
date: 2026-11-02
id: 20261102T195331Z
impact: patch
title: Run namespace-bound launcher tests in the hosted compatibility job
---

Run the real pidfd-launcher and mount-destination namespace tests only in the
provisioned hosted compatibility job (VERJSON_TEST_REAL_BWRAP=1); elsewhere
they skip. Part of #1966.
