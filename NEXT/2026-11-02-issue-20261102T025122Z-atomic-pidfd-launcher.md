---
date: 2026-11-02
id: 20261102T025122Z
refs: 1966
impact: patch
title: Add atomic pidfd launcher boundary
---

Add an isolated native launcher boundary that creates a child and pidfd
atomically, with a guarded exec and support for pidfd-based cleanup. Part of #1966.
