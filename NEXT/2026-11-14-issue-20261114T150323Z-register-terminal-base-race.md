---
date: 2026-11-14
id: 20261114T150323Z
refs: 1945
impact: patch
title: Register terminal base race acceptance test
---
Register the terminal base-race acceptance repro in merge-gate CI. A vulnerable
positive control confirms the fake GitHub fixture advances the base before a
direct merge; the production helper remains fail-closed pending non-bypass
admission rollout. Following the 2026-11-13 ARB dependency review, #1945 is no
longer blocked by #1964 and blocks #1861; keep the #1861 preservation branch
unmerged pending rollout validation.
Part of #1945.
