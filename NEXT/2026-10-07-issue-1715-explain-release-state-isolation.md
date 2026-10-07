---
date: 2026-10-07
issue: 1715
impact: patch
title: Explain the isolated release tag lookup environment
---

Generated release callers now explain why the restart-safe tag lookup clears runner-controlled shell and Git settings and passes its authentication header only to an isolated Git child.
