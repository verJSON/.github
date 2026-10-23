---
date: 2026-10-23
issue: 1830
impact: patch
title: Explain a failed secretless transfer restore and document the rerun and pin causes
---

The secretless `build-test` job keeps `fail-on-cache-miss: true` on the run-bound dependency transfer and now prints a static diagnostic naming the two known causes (a pin from before #1251 whose cache path included `run_attempt`, which made every failed-job rerun miss, and an evicted or expired entry) and the remedy. `docs/node-workflows.md` gains a troubleshooting section that also covers the Playwright download failure in the offline rebuild, and ADR 0095 records the decision not to cold-start without credentials. Regenerated `node-ci-protected.yml` in the same change.
