---
date: 2026-10-29
id: e273a4d1
title: Inspect snapshot commit parents from raw Git objects
impact: patch
---

Read bounded commit object headers directly so local grafts cannot substitute a
snapshot parent during offline consistency inspection. Part of #1954.
