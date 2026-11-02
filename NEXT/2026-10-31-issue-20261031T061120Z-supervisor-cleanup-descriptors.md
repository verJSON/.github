---
date: 2026-10-31
id: 20261031T061120Z
impact: patch
title: Close supervisor descriptors when cleanup verification fails
---

Attempt to close supervisor-owned streams and descriptors even when process
cleanup cannot be verified. Preserve failure provenance and withhold the plan
receipt when cancellation evidence is unavailable. Part of #1966.
