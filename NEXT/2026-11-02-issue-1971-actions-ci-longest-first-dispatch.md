---
date: 2026-11-02
issue: 1971
impact: patch
title: Dispatch actions-ci rows exclusive-first and longest-budget-first
---
The actions-ci group runner starts `@exclusive` rows first, then rows with a declared `@budget` (longest first), then the remaining rows in manifest order. Declared budgets now rank dispatch on every event, but they still cap rows only under the pull-request group budget. Replaying `main` run 40642682859 through the dispatcher, the `platform` cell drops from 446 s to 367 s and `merge-gate` drops from 105 s to 86 s.
