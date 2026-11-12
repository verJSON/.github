---
date: 2026-11-12
id: 20261112T140000Z
refs: 2031
impact: patch
title: Deliver the AI review App key to the merge-admission producer and bound its runs
---

The merge-admission producer now receives the AI review App key. Its caller inherits
the caller's secrets, and its main-only key policy receives the one named key, as the AI
review gate does. Since #2055 merged, every run that found a pull request to evaluate
failed key validation, so the producer never posted a `merge-admission` check (#2031,
ADR 0260 amendment 2026-11-12).

`scripts/ci-gate/app-key-environment.test.py` now treats the producer as an App-key
consumer. It derives the inheritance requirement from every reusable edge into such a
consumer, and the named-key requirement from every `ai-review` key policy.

Before the key turns on real evaluations, the producer's caller bounds its fan-out: one
running and one pending run per evaluated head, and one for the sweep, with a running run
never cancelled. On 2026-11-12 the caller started 246 runs in six hours, 121 of them in one
hour. The concurrency key repeats the planner's choice between one head and a sweep, so a
displaced pending run is always replaced by one that plans the same work. ADR 0260 records
the expected rate, the post-merge measurement, and why the completion triggers still list
every workflow that reports a check on a pull-request head.
