---
date: 2026-11-15
issue: 1977
impact: patch
title: Share engine-path fixtures and fail closed early on retired Renovate writers
---

The emitted changelog contract checks each dependency manifest and executable
GitHub YAML path on its own branch of one base repository. Each path still
gets its own check-pr and the same single-path rejection, and the executable
YAML cases stay behind the pinned-engine re-pin guard.

Local medians over five runs of those loops, on the post-#1974 generator,
were 3.26s to 2.98s for the 16 dependency paths and 1.01s to 0.89s for the
five executable YAML paths: about 0.4s per full emitted run, with no
diagnostic mismatches. The shared-base generator change also rebinds the
#1831 relay backlog digests for `capability-floor-observe` and `release.yml`
`verify`, which sparse-check out `scripts/gen-changelog-caller.sh`.

`scan_retired_callers` now runs immediately after the generated-set pin and
CODEOWNERS layout checks, before the expensive workflow-shape and fixture
loops, so retired Renovate fragment-writer mutations fail without paying for
most of an emitted run while CODEOWNERS symlink diagnostics keep precedence. A dual-fault adopter
sees the retired-path diagnostic first; ADR 0224 amendment 2026-11-17 records
that ordering. Fixes #1977.
