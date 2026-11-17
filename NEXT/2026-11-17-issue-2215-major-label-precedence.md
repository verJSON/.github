---
date: 2026-11-17
issue: 2215
impact: patch
title: Lock major-label precedence over breaking title heuristics
---

Add an explicit classifier unit test that a `major` label wins over a title
containing "breaking change", matching the documented label-first rule.

Closes #2215.
