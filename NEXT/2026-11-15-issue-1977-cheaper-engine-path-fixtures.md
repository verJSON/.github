---
date: 2026-11-15
issue: 1977
impact: patch
title: Share one fixture repo across emitted engine path checks
---

The emitted changelog contract checks each dependency manifest and executable
GitHub YAML path on its own branch of one base repository. Each path still
gets its own check-pr and the same single-path rejection, and the executable
YAML cases stay behind the pinned-engine re-pin guard.

Local medians over five runs of those loops, on the post-#1974 generator,
were 3.26s to 2.98s for the 16 dependency paths and 1.01s to 0.89s for the
five executable YAML paths: about 0.4s per full emitted run, with no
diagnostic mismatches. Part of #1977. Moving `scan_retired_callers` earlier
would change which diagnostic a dual-fault adopter sees and still needs an
ADR 0224 decision, so that placement is unchanged.
