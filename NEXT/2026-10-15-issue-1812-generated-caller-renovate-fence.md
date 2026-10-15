---
date: 2026-10-15
issue: 1812
impact: patch
title: Renovate maintains generator action pins so adopters stop hand-editing generated callers
---

`renovate.json` gains a regex custom manager over `scripts/gen-*.sh|py` that reads every `release_*='owner/repo@<sha> # <tag>'` pin declaration in the changelog caller generator, with the `github-actions` manager's datasource and versioning. Renovate now proposes those digest bumps here, in the same grouped `github actions digests` PR as this repository's workflows, instead of in adopters' generated callers. `scripts/ci-gate/renovate-generated-callers.test.py` (platform group) proves the manager extracts exactly the declared pins.
