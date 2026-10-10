---
date: 2026-10-10
issue: 1747
impact: patch
title: Grant actions read to the generated Node release publish job
---
The `release-node` caller now grants its `publish` job `actions: read`, which `node-release.yml`'s `release` job has declared since #1730. Without it the dispatch ended as a zero-job `startup_failure`, because a called workflow cannot exceed its caller's grant. The release-caller contract test now compares the emitted caller's grants with every job permission declared in the callee and proves the comparison rejects a caller that withholds `actions: read`. Adopters regenerate their release caller at a contract SHA containing this fix.
