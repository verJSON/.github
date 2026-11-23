---
date: 2026-11-23
id: 8211a5782b6b
title: "Release CI assert script is executable in the selection contract"
impact: patch
---
- `scripts/assert-release-head-ci.sh` is mode `100755` in the pin, and the
  generated Release verify step `chmod +x`s it after checkout so a `100644`
  pin cannot fail the `-x` gate with a "missing" error (ADR 0291).
