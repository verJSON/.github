---
date: 2026-11-15
issue: 2036
impact: patch
title: Re-verify release-artifact downloads before attaching them
---

The generated release-artifact publish job now recomputes each downloaded
asset's SHA-256 against digests recorded by the build leg (bound to run,
attempt, and job index) and fails closed on mismatch or missing inventory
before `gh release upload`.

