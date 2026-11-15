---
date: 2026-11-15
issue: 1998
title: Right-size review-caller-bootstrap budget after #2009
impact: patch
---

#2009 already removed double `run_case`, stubbed recovery sleeps, and
parallelized `review-caller-bootstrap`. Hosted main run 43623577812
measured that row at 43s, so ADR 0225 sets `@budget=90` (was 360). The
`privileged-merge-conformance` `@exclusive` note now cites the same run's
24s alone timing and keeps the reservation until a sibling-load
remeasure. The low-impact `required-checks-audit` shared preamble stays
deferred as in #2009.
