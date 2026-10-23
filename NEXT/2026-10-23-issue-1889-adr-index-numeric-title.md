---
date: 2026-10-23
issue: 1889
impact: patch
title: Keep a numeric-hyphen ADR title intact in the generated index
---

`scripts/gen-adr-index.sh` now strips only the directory's own ADR number and one
supported separator from the heading, so `# 0001 — 24-hour notice` is indexed as
`24-hour notice` instead of `hour notice`. The canonical ADR-index test gains
fixtures for both separators and for a mismatched leading number (#1889).
