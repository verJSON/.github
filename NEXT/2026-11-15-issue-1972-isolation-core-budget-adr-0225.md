---
date: 2026-11-15
issue: 1972
title: Raise runner-isolation core budget to ADR 0225 hosted margin
impact: patch
---

Hosted platform run 43669973375 measured the in-process core isolation row
at 63s. ADR 0225 requires twice the slowest CI time, rounded up to 30s, so
the declared budget moves from 120 to 150. Probe shards at 13s and 11s stay
on the 90s floor.

Part of #1972 / PR #2098.
