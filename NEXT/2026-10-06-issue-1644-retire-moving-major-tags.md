---
date: 2026-10-06
issue: 1644
title: Retire moving major tags
impact: major
---

Remove the failing `tag-major` release workflow. Published contract releases use immutable version tags and commit SHA pins; existing major aliases remain static for legacy callers until they migrate.
