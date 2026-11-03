---
date: 2026-11-02
issue: 1980
impact: patch
title: Declare the measured budget for the secretless hosted-lane contract
---
`node-ci-secretless-hosted-lane.test.py` declares `@budget=150` in the actions-ci manifest. Its CI runtimes of 45–67 s (one more run timed out at 60 s) sat at or above the 60 s default pull-request budget, so it timed out on unrelated pull requests. Per ADR 0225, the budget is twice its slowest measurement (67 s), rounded up to 30 s.
