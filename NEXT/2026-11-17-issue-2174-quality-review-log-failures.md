---
date: 2026-11-17
issue: 2174
impact: patch
title: Log quality-review failures as workflow warnings
---

Emit `::warning::` lines for JevError and unexpected failures in `run_review`
so advisory CI stays green while workflow logs remain diagnosable. Closes #2174.
