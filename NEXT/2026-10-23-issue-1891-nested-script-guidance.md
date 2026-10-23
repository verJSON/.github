---
date: 2026-10-23
issue: 1891
impact: patch
title: Document the nested-only install-script limit for adopters
---

`docs/node-workflows.md` and ADR 0086 now state that a script-bearing package present only nested under another package's `node_modules/` is outside the offline rebuild accounting, stays unbuilt without a warning, and must be made a top-level dependency if a consumer needs its native build. They also note that the pnpm rebuild does not ignore a repository `pnpmfile`.
