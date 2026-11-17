---
date: 2026-11-17
issue: 2193
impact: patch
title: Gate R foreign-archive test uses archive repo constant
---

Use `$ARB_ARCHIVE_REPO` in the foreign-archive refusal assertion instead of
hardcoding `verJSON/.github`, so a rename updates one fixture constant.
Fixes #2193.
