---
date: 2026-11-17
issue: 2174
impact: patch
title: Log quality-review failures as workflow warnings
---

Emit `::warning::` lines on stderr for JevError and unexpected failures in
`run_review`, inject the contract `scripts/` path under `python3 -I` (which
ignores PYTHONPATH), and pin the same-repo canary to the PR head so Evaluate
imports the package under review. Closes #2174; advances #1820 canary.
