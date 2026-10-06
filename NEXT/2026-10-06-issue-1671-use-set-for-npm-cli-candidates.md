---
date: 2026-10-06
issue: 1671
title: Use a set for npm CLI candidates
impact: patch
---

Use a set to deduplicate resolved npm CLI paths in the generated protected node CI workflow. Existing tests continue to require alias deduplication and reject distinct ambiguous candidates.
