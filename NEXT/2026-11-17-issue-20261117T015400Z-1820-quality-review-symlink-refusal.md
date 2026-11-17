---
date: 2026-11-17
id: 20261117T015400Z
impact: patch
title: Refuse symlink before quality-review contract checkout
---

Refuse a symlink at the quality-review contract checkout path before
`actions/checkout` can follow it. Part of #1820.
