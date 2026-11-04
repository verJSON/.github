---
date: 2026-11-04
issue: 1997
impact: patch
title: Build expensive test fixtures once instead of per case
---
Three actions-ci test rows stop rebuilding a fixture that every case shares, with no assertion removed or weakened.

- `scripts/ci-gate/app-key-environment.test.py` parses the workflow directory and the App key role manifest once per class instead of once per test. The shared fixture is frozen, so a test that tried to edit it raises instead of leaking the edit into the next test.
- `scripts/ci-gate/node-ci-secretless-hosted-lane.test.py` runs the cheap structural `binding_violations` check on each mutant before the 44-scenario, two-locale replay, so the replay now runs for 8 of the 20 mutants instead of all 20. Every mutant is still detected. The image-family mutant now spells out its letter class instead of using `[a-z]`, so it is still caught by the scenario replay it was written to prove, and not by the bracket-range lint.
- `scripts/ci-gate/review-caller-bootstrap.test.py` caches the bootstrap step's outputs and its generated proof program. The cache key is the step text, the consumer's routes, and the PATH prefix, so `complete()` stops rerunning an identical step. A mutant that changes the bootstrap step still gets a fresh run. A failed run is never cached, and a step that writes no program leaves no stale one behind.
