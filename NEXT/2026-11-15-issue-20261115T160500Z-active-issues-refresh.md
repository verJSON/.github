---
date: 2026-11-15
id: 20261115T160500Z
impact: patch
title: Refresh Active Issues after closed admission and makespan work
---

Prune closed Active Issues entries (#1998, #1875) and re-verify open ones against live API state after #1401, #1843/#2116, #2114, and #2115.

#1998 and #1875 closed; #1401's admission gap is gone (four original repos have callers); ruleset 20722935 already runs `gate-rearm@24db445f` with the ADR 0230 watchdog; #2114 bound the same on `ai-review-merge.yml`. #1869/#1945/#1363/#1763/#1810 text updated from `gh` + ruleset reads on 2026-11-15.
