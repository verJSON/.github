---
date: 2026-11-12
id: 20261112T130444Z
refs: 1905
impact: patch
title: Set aside a family run displaced before a guard-yielded local re-arm run
---

`assert-mergeable-head.sh` no longer refuses Gate C (exit `5`) on a cancelled
`ai-review-arm-<PR>` family run when the only newer family run is a local
`gate re-arm` run whose F-1R guard yielded: a run that concluded success and whose
own suite is exactly the guard's `SUCCESS` and the calling job's `SKIPPED`. That run
never entered the concurrency group, so the newest family run that did is consulted
instead, and it must still report only `SUCCESS` (#1905, ARB-G1-2026-11-06 C1
precondition 5). Proposed in an ADR 0226 amendment pending an ARB decision.

The yield shape is declared per caller in `CONCURRENCY_FAMILIES` and derived from
the caller generator by `scripts/ci-gate/concurrency-family-map.test.py`. A failed
or cancelled guard, a calling job that ran, an extra or missing check, a run that
did not succeed, or any other caller keeps today's refusal. Gate W is unchanged.
