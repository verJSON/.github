---
date: 2026-10-18
id: 81666b48
impact: patch
title: Refuse a planted symlink before every fixed-path checkout
---

`changelog-validate.yml` and `generated-artifacts.yml` now refuse a symlink at `.changelog-contract` before checking the contract out, and the merge gate (`ai-review-merge.yml`) refuses one before each of its five fixed-path checkouts. `actions/checkout` follows a symlink at its `path:` and empties the target, so a pull request that committed one could aim that delete at runner state on a persistent runner, or, in the gate, at the immutable arm verifier (ADR 0236; ADR 0220's 2026-10-18 amendment).

`scripts/ci-gate/fixed-path-checkout-refusal.test.py` plants the link, executes the workflow's own steps, and fails when a checkout would follow it.
