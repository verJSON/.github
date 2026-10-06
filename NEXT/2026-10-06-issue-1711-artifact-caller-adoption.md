---
date: 2026-10-06
issue: 1711
impact: patch
title: Correct generated artifact release caller validation
---

Generated artifact release callers now use the reviewed matrix runner selector,
accept the canonical organization spelling in authentic GitHub Packages download
URLs while still requiring the exact package name, and emit valid JavaScript for
private lock validation. The generator contract exercises the emitted selector,
Node syntax, matching URL, and wrong-package rejection.
