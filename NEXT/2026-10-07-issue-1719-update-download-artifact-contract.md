---
date: 2026-10-07
issue: 1719
impact: patch
title: Update the generated artifact download contract
---

The canonical runner deployment review producer pins all three artifact downloads to `actions/download-artifact` v8.0.2. Its generated consumer contract continues to pin the complete producer workflow digest, so consumers must regenerate from a reviewed immutable contract commit.
