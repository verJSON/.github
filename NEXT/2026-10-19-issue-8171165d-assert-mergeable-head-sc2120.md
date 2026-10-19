---
date: 2026-10-19
id: 8171165d
impact: patch
title: Clear the full-tree ShellCheck warning in the mergeable-head test
---

`scripts/assert-mergeable-head.test.sh` no longer trips ShellCheck SC2120 in the full-tree audit that runs on pushes to `main`. Its `fail_in` fixture defaulted an argument no caller passes; it now uses the fixed timestamp directly, which is the same behavior. Pull-request runs lint only changed files, so the warning reached `main` unnoticed.
