---
date: 2026-10-22
issue: 1869
impact: minor
title: Container candidate publication evicts its one-time dependency transfer cache
---

The immutable container candidate publisher now deletes the roughly 900 MB private-dependency transfer cache after its build jobs succeed, so it stops filling an adopter's 10 GB Actions cache quota and evicting dependency and browser caches. Publication callers must grant `actions: write`; regenerate with `scripts/gen-container-candidate.sh`.

Part of #1869 (item 6). Amends ADR 0078 (2026-10-22). Failed runs keep the entry so re-running failed jobs still works.
