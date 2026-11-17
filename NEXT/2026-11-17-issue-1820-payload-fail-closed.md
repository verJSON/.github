---
date: 2026-11-17
issue: 1820
impact: patch
title: Fail closed on unparseable diffs and broaden env path drops
---

Quality-review payload filtering now rejects unified diffs without
`diff --git` headers instead of labeling them `unknown`, and drops
`*.env` / `.envrc` paths so renames cannot launder env content. Part of #1820.
