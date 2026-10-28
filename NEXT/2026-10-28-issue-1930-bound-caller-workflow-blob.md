---
date: 2026-10-28
issue: 1930
impact: patch
title: Bound caller workflow blob verification
---
Caller workflow verification rejects blobs larger than 1 MiB and hashes accepted blobs from a temporary file in fixed-size chunks, with a bounded subprocess timeout and fail-closed output checks.
