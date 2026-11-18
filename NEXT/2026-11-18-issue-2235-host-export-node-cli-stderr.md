---
date: 2026-11-18
issue: 2235
impact: patch
title: Pin Node 26.13.1 for cli-cloud host export and relay CLI stderr
---

The reusable container-deployment workflow still pinned Node 26.12.1 while
`@verjson/cli-cloud@2.1.0` requires `>=26.13.1`. After GitHub binding fixes
landed, dry-runs reached `runner-host-evidence` and failed opaque. Pin Node
26.13.1 and relay a single sanitized CLI/adapter stderr line.

Fixes #2235.
