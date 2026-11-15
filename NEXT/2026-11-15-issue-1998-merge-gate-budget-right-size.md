---
date: 2026-11-15
issue: 1998
title: Cut merge-gate makespan by dropping privileged-merge exclusivity
impact: patch
---

#2009 already removed double `run_case`, stubbed recovery sleeps, and
parallelized `review-caller-bootstrap`. Hosted main run 43623577812 measured
bootstrap at 43s (ADR 0225 → `@budget=90`, was 360) and
`privileged-merge-conformance` at 24s alone. Dropping that row's `@exclusive`
lets it overlap the parallel phase (~18–20s of merge-gate wall); `@budget=120`
still fails closed under sibling load. ADR 0225 is amended. The low-impact
`required-checks-audit` shared preamble stays deferred as in #2009.
