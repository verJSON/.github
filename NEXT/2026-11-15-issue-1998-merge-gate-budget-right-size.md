---
date: 2026-11-15
issue: 1998
title: Cut merge-gate makespan by dropping privileged-merge exclusivity
impact: patch
---

Merge-gate no longer reserves the runner for privileged-merge conformance.
Hosted, that single-threaded audit is 16–29s; on a 4-CPU remeasure, sibling
load matched running it alone (73s), inside the 120s budget, so the exclusive
prefix comes off the group wall. `review-caller-bootstrap`'s declared budget
is 120, twice its slowest hosted run after #2009 (50s).

#2009 already removed double `run_case`, stubbed recovery sleeps, and
parallelized the bootstrap row (was 360s, from a 173s serial run). ADR 0225's
2026-11-15 amendment records the exclusivity drop and the budget. The
low-impact `required-checks-audit` shared preamble stays deferred.
