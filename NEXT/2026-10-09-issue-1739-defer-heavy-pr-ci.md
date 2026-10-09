---
date: 2026-10-09
issue: 1739
impact: patch
title: Defer heavy pull request CI until review is ready
---
Draft pull requests continue to receive fast lint, type-surface, and admission
feedback, while long test suites and container candidate builds wait until the
author marks the pull request ready for review. Ready and converted-to-draft
events preserve the heavy checks' lifecycle and cancellation behavior.
