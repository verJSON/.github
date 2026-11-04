---
date: 2026-10-29
issue: 1958
title: Make runner watchdog deadlines timezone-independent
impact: patch
---

Evaluate runner acquisition timestamps in UTC so watchdog deadlines remain
consistent across runner timezones.
