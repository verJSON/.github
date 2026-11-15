---
date: 2026-11-15
issue: 1401
impact: patch
title: Name a missing admission caller when legacy arm enrollment diverges
---

The authorization-arm audit now reports a repository that carries `verjson-core-checks=enforced` and lacks a dispatchable default-branch `.github/workflows/ai-review-merge.yml` in the same run as a legacy cohort mismatch or a missing deterministic-CI stack. Those checks used to return first, so the required arm could already be failing pull requests while the audit named only the unreviewed repository or the stack gap (#1401, ADR 0206).

The target-selector cohort check still stops before rendering a property cutover. Installing the generated caller set in consumer repositories remains owner work tracked in #1810; this change does not enroll a repository or close #1401.
