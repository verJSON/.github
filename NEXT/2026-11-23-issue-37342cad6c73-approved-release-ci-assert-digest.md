---
date: 2026-11-23
id: 37342cad6c73
title: "Approve the Release CI assert step that chmods the ADR 0291 script"
impact: patch
---
- `APPROVED_RELEASE_CI_ASSERT_SCRIPT_SHA256` matches the verify step that
  presence-checks and `chmod +x`s `assert-release-head-ci.sh` before invoke.
