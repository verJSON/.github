---
date: 2026-11-17
issue: 2214
impact: patch
title: Classify Renovate bumps before opening the claim comment store
---

Fail closed on major/breaking Renovate bumps before constructing the
comment-backed claim store so no claim receipt or handoff comment is posted
for rejected upgrades.

Closes #2214. Also covers the #2215 label-precedence classifier test.
