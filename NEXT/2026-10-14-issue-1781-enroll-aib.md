---
date: 2026-10-14
issue: 1781
impact: patch
title: Enroll AiB in the deterministic CI and authorization-arm rulesets
---
The reviewed authorization-arm cohort adds AiB, so its repository properties can move to `verjson-stack=node`, `verjson-core-checks=enforced` and `changelog-contract=adopted`. AiB's `main` previously declared no required status checks, which ADR 0091 recorded as a coverage gap and which made the admin-merge assertion refuse every AiB pull request. ADR 0091 is amended with the enrollment.
