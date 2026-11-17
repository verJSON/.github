---
date: 2026-11-17
issue: 1820
impact: minor
title: Add the Jev quality-review adapter and provider manifest
---

Add `scripts/quality_review/providers/jev.py` (pinned `jev-1.14.0`, Bearer
transport, noul/score mapping through the evaluator port) plus
`config/quality-review-providers.json` and contract tests. Part of #1820;
workflow, rubric, and live canary remain later slices.
