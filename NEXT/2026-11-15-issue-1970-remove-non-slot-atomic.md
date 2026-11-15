---
date: 2026-11-15
issue: 1970
impact: patch
title: Remove the unreachable non-slot atomic launcher
---

The supervised Bubblewrap launcher only constructs slot-owned pidfd processes, so the plain atomic spawn and its separate descriptor cleanup are gone.

`run_supervised_bubblewrap` never built a raw `AtomicPidfdProcess`. The slot supervisor remains the production path, and the tests that existed only to drive the unreachable launcher now use slot-owned descriptors or are dropped. Part of #1966.
