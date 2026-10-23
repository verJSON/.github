---
date: 2026-10-22
id: 5e2b8c4a7d13
impact: minor
title: Opt-in typed test levels generate a staged Node CI caller
---

`scripts/gen-node-ci-caller.py` renders a caller from an optional `ci-levels.json`: one `node-ci.yml` call per ordered level, `needs` edges that are `success` or `completed`, event and branch filters, and a fixed `test-levels` aggregate that fails on any failed, cancelled, or unexpectedly skipped level. Without the file the output is the single `ci` job, so `ci / build-test` is unchanged; `node-ci.yml`, the protected twin, and all digests are untouched.

Part of #1869 (ARB change 3, slice 1). Amends ADR 0238 (2026-10-22). `failFast: false` is rejected until in-lane support lands, and `cancel` stays a caller workflow-level concurrency block.
