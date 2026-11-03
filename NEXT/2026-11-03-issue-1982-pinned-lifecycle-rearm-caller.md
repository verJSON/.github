---
date: 2026-11-03
issue: 1982
impact: patch
title: Generate a SHA-pinned AI review lifecycle re-arm caller
---
`scripts/gen-ai-review-lifecycle-rearm-caller.sh` now takes a 40-character contract SHA, as its label re-arm sibling does, and emits a consumer caller pinned to `Verjson/.github/.github/workflows/gate-rearm.yml@<sha>`. The caller delivers `ready_for_review`, `converted_to_draft`, `edited` and `unlabeled`, so a consumer that regenerates its callers at current `main` keeps re-arm on ready, draft, title and hold-label transitions. `--local` still renders this repository's own caller unchanged. See [Verjson/.github#1982](https://github.com/Verjson/.github/issues/1982).
- The authorization audit's rollout contract (`config/ai-review-required-workflow-rollout.json`) now lists `.github/workflows/ai-review-lifecycle-rearm.yml` in the completeness set, so an armed adopter without it is reported with an incomplete generated adopter caller set rather than silently losing lifecycle re-arm. It is not an admission caller: the arm still runs without it, and canonical merge re-checks draft and hold state.
