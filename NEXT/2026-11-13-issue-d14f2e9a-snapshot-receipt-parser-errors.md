---
date: 2026-11-13
id: d14f2e9a
impact: patch
title: Normalize oversized snapshot receipt JSON errors
---
The offline snapshot receipt inspector reports an oversized JSON integer as a typed receipt error while retaining specific duplicate-field and invalid-number diagnostics. Part of #1954.
