---
date: 2026-11-03
issue: 1982
impact: patch
title: Generate a SHA-pinned AI review lifecycle re-arm caller
---
`scripts/gen-ai-review-lifecycle-rearm-caller.sh` now takes a 40-character contract SHA, as its label re-arm sibling does, and emits a consumer caller pinned to `Verjson/.github/.github/workflows/gate-rearm.yml@<sha>`. The caller delivers `ready_for_review`, `converted_to_draft`, `edited` and `unlabeled`, so a consumer that regenerates its callers at current `main` keeps re-arm on ready, draft, title and hold-label transitions. `--local` still renders this repository's own caller unchanged. See [Verjson/.github#1982](https://github.com/Verjson/.github/issues/1982).
