---
date: 2026-10-06
issue: 1645
impact: patch
title: Ignore source templates with variable contract references
---

Contract version verification now ignores shell variable placeholders in source files that assert or generate `uses:` references. It still rejects variable references in Actions YAML and still compares literal source-file pins with the declared release commit.
