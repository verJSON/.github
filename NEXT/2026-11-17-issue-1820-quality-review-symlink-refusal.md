---
date: 2026-11-17
issue: 1820
impact: patch
title: Refuse a symlink before the quality-review contract checkout
---

Add the ADR 0236 fixed-path checkout refusal immediately before checking out
`Verjson/.github` into `.quality-review-contract` in `quality-review.yml`.
