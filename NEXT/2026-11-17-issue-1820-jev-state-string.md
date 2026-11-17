---
date: 2026-11-17
issue: 1820
impact: patch
title: Send System One state as a string and fail closed on timeouts
---

Jev's System One API requires `state` as a string; the adapter now sends the
filtered diff text (or a JSON encoding of the port state) and maps read
timeouts into `JevError` with retries. Part of #1820.
