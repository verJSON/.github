---
date: 2026-10-06
issue: 1650
impact: patch
title: Bind authorization finalizer to the expected pull request head
---

Reject an authorization check whose identity names a different pull request head before the finalizer can update it. The finalizer now uses the same exact-head condition as the other authorization paths.
