---
date: 2026-10-15
issue: 1803
impact: patch
title: Enroll verjson-editor in the reviewed authorization-arm cohort
---

The reviewed authorization-arm cohort adds `Verjson/verjson-editor`, which gained `verjson-core-checks=enforced` on 2026-10-15 and so is already armed by the legacy selector. The scheduled authorization-arm audit had stopped at the cohort mismatch; it now reaches the adopter checks again (#1803, part of #1401, ADR 0206).

With every armed `ai-review-app` environment now provisioned, `config/audit-expected-findings.json` re-records the remaining finding under #1810 for what still reproduces: verjson-editor has no `ai-review-merge.yml` admission caller, and 23 generated adopter callers are missing across 19 armed repositories, mostly `ai-review-label-rearm.yml`.
