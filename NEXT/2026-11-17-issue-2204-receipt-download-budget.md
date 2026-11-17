---
date: 2026-11-17
issue: 2204
impact: patch
title: Lock deployment-receipt digest-scan download budget at twenty
---

A unit test asserts the digest-only receipt scan fails closed after exactly
`MAX_ARTIFACT_DOWNLOADS` ZIP fetches, matching the intended twenty-download
boundary from #2203.

Closes #2204.
