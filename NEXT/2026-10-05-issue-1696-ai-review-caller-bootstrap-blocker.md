---
date: 2026-10-05
issue: 1696
title: AI review caller bootstrap blocker
impact: patch
---

Index the fail-closed migration gap for generated AI review callers pinned behind a changed organization identity. The existing default-branch caller cannot verify the current arm receipt and therefore cannot authorize its own pin update; the migration and exact-head proof remain tracked in #1696.
