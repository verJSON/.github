---
date: 2026-11-17
issue: 2205
impact: patch
title: Surface operational errors during deployment-receipt digest scan
---

Digest-only `deployment-receipt` search now skips only
`ReceiptAbsentFromArtifact` misses and re-raises other transport failures, so a
corrupt archive or HTTP fault is not reported as "not found".

Closes #2205.
