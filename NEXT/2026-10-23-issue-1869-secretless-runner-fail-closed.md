---
date: 2026-10-23
issue: 1869
impact: patch
title: Fail closed on falsy secretless runner lanes
---
Secretless node-ci builds now use CI_LANE_UNTRUSTED only after successful eligibility; a second secretless-only ubuntu-24.04 branch keeps failed, skipped, missing, or falsy results from falling through to the caller runner. The prerequisite diagnostic reports both eligibility and acquisition results and directs callers to inspect those jobs before retrying.

Eligibility still validates the raw lane before dependency acquisition, and the non-secretless runner tail is unchanged. The catalog and protected workflow twin are regenerated, with coverage in scripts/ci-gate/runner-lane-resolver.test.py and exact existing isolation digests rebound. This restores the hosted-runner invariant in ADR 0234 and resolves the falsy-lane concern first recorded in ADR 0240's 2026-10-22 consequences and addressed by its 2026-10-23 amendment.
