---
date: 2026-10-23
issue: 1869
impact: patch
title: Fail closed on falsy secretless runner lanes
---
Secretless node-ci builds now use CI_LANE_UNTRUSTED only after successful eligibility; a second secretless-only ubuntu-24.04 branch keeps failed, skipped, missing, or falsy results from falling through to the caller runner. The prerequisite diagnostic reports both eligibility and acquisition results and directs callers to inspect those jobs before retrying.

Eligibility still validates the raw lane before dependency acquisition, and the non-secretless runner tail is unchanged. The hosted-lane guard covers raw false, zero, null, and empty JSON strings; the catalog and protected workflow twin are regenerated. Coverage lives in scripts/ci-gate/node-ci-secretless-hosted-lane.test.py, scripts/ci-gate/runner-lane-resolver.test.py, and scripts/ci-gate/runner-routing-policy.test.sh. Exact existing isolation digests, including release verification, are rebound, and the exact-site inventory and mutation probes bind the fallback to both node-ci build-test twins. This restores the hosted-runner invariant in ADR 0234, resolves the falsy-lane concern first recorded in ADR 0240's 2026-10-22 consequences and addressed by its 2026-10-23 amendment, and updates docs/node-workflows.md.
